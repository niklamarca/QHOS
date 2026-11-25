"""
QUANT HPC OS — Internal Validation Cluster
Pipeline local de filtros anti-overfitting. Aprovação aqui não implica edge.
"""
from __future__ import annotations
import asyncio
import json
import os
import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger
from data.storage import StorageEngine
from data.features import FeatureEngine
from evolution.strategy_dna import StrategyDNA
from backtest.engine import BacktestEngine
from backtest.walk_forward import WalkForwardValidator
from core.embargo import split_embargo

log = get_logger("Validator")


@dataclass
class ValidationResult:
    """Resultado completo da validação de uma estratégia."""
    strategy_id: str
    passed: bool = False
    benchmark_margin_ok: bool = False   # bate comprar-e-segurar?
    benchmark_margin: float = 0.0       # diferenca de Sharpe vs buy&hold
    benchmark_p: float = 1.0            # P(margem <= 0) no bootstrap em blocos
    selection_floor: float = 0.0        # legado: agora armazena alpha corrigido
    rejection_reason: Optional[str] = None
    score: float = 0.0

    # Testes individuais
    oos_sharpe_ok: bool = False
    walk_forward_ok: bool = False
    regime_robustness_ok: bool = False
    complexity_ok: bool = False
    noise_injection_ok: bool = False
    monte_carlo_ok: bool = False
    min_trades_ok: bool = False

    # Detalhes
    oos_sharpe: float = 0.0
    wf_mean_sharpe: float = 0.0
    regime_robustness: float = 0.0
    noise_stability: float = 0.0
    mc_sharpe_p5: float = 0.0
    n_trades: int = 0
    complexity: int = 0

    def compute_score(self) -> float:
        """Score de validação [0, 1]."""
        checks = [
            self.oos_sharpe_ok,
            self.walk_forward_ok,
            self.regime_robustness_ok,
            self.complexity_ok,
            self.noise_injection_ok,
            self.monte_carlo_ok,
            self.min_trades_ok,
            self.benchmark_margin_ok,
        ]
        self.score = sum(checks) / len(checks)
        return self.score


class ValidationCluster:
    """
    Cluster de validação anti-overfitting.

    Pipeline:
    1. Mínimo de trades (significância estatística)
    2. OOS Sharpe mínimo
    3. Walk-forward consistency
    4. Robustez cross-regime
    5. Noise injection (parâmetros perturbados)
    6. Monte Carlo (retornos aleatorizados)
    7. Complexity penalty (Occam's razor)

    Uma estratégia que passa em TODOS os testes recebe o código interno "validated".
    Uma estratégia que falha em qualquer teste é imediatamente rejeitada.
    """

    def __init__(self, storage: StorageEngine, feature_engine: FeatureEngine):
        self.storage = storage
        self.feature_engine = feature_engine
        self.bus = event_bus
        self.engine = BacktestEngine()
        self.wf_validator = WalkForwardValidator()
        # Revalidar a mesma elite depois de um reboot desperdiçava walk-forward,
        # noise injection e Monte Carlo. O cache é persistido por época de dados;
        # se o dataset muda, a memória antiga é descartada de forma explícita.
        self._val_cache: Dict[str, ValidationResult] = {}
        self._cache_path = Path(config.data_lake_dir) / "validation_cache.json"
        self._load_validation_cache()

    def _data_epoch(self) -> str:
        # A etiqueta manual antiga ("full_history_v1") sobrevivia a revisoes
        # de historico e fazia o cache afirmar que dados diferentes eram iguais.
        from governance.epistemics import current_scientific_epoch
        return current_scientific_epoch()

    def _load_validation_cache(self) -> None:
        """Restaura julgamentos apenas se vierem da mesma época de dados."""
        try:
            if not self._cache_path.exists():
                return
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1 or payload.get("data_epoch") != self._data_epoch():
                # Preserva a multiplicidade histórica mesmo quando os resultados
                # deixam de ser reutilizáveis após mudança de código/dados.
                try:
                    from governance.epistemics import current_data_lineage
                    from governance.experiment_registry import experiments
                    for old_key in (payload.get("results") or {}):
                        experiments.record_hypothesis_test(
                            str(old_key).split("@", 1)[0], current_data_lineage(), legacy=True
                        )
                except Exception as migration_error:
                    log.debug(f"migração de multiplicidade: {migration_error}")
                log.warning("Cache de validação ignorado: época de dados incompatível")
                return
            restored: Dict[str, ValidationResult] = {}
            for key, value in (payload.get("results") or {}).items():
                if isinstance(key, str) and isinstance(value, dict):
                    restored[key] = ValidationResult(**value)
            # Limite duro inclusive para arquivo corrompido ou excessivo.
            self._val_cache = dict(list(restored.items())[-5000:])
            if self._val_cache:
                log.info(f"Cache de validação restaurado: {len(self._val_cache)} julgamentos")
        except Exception as exc:
            log.warning(f"Cache de validação ignorado por erro: {type(exc).__name__}: {exc}")
            self._val_cache = {}

    def _save_validation_cache(self) -> None:
        """Grava cache pequeno de forma atômica; falha nunca invalida o run."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema": 1,
                "data_epoch": self._data_epoch(),
                "saved_at": __import__("time").time(),
                "results": {key: asdict(result) for key, result in self._val_cache.items()},
            }
            tmp = str(self._cache_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self._cache_path)
        except Exception as exc:
            log.warning(f"Cache de validação não persistiu: {type(exc).__name__}: {exc}")

    @staticmethod
    def _apply_cached_result(strategy: StrategyDNA, result: ValidationResult) -> None:
        """Reaplica os campos usados pelo GA ao reutilizar um julgamento."""
        strategy.wf_mean_sharpe = result.wf_mean_sharpe
        strategy.walk_forward_ok = result.walk_forward_ok
        strategy.noise_stability = result.noise_stability
        strategy.regime_robustness = result.regime_robustness
        strategy.stability_score = result.noise_stability if result.passed else 0.0
        strategy.status = "validated" if result.passed else "rejected"
        strategy.rejection_reason = result.rejection_reason
        if not result.passed:
            strategy.fitness = 0.0

    async def validate_single(self, strategy: StrategyDNA) -> ValidationResult:
        """Valida uma única estratégia pelo pipeline completo."""
        result = ValidationResult(strategy_id=strategy.strategy_id)
        log.debug(f"Validando: {strategy.name} ({strategy.strategy_id})")

        # Carrega dados
        features, prices = await self._load_data(strategy)
        if features is None or len(features) < 150:
            result.rejection_reason = "insufficient_data"
            strategy.status = "rejected"
            strategy.rejection_reason = "insufficient_data"
            return result

        # Embargo: validação opera só na janela de pesquisa (primeiros 80%).
        # Os últimos embargo_fraction são held-out de auditoria — proibidos aqui.
        (features, prices), _audit = split_embargo(features, prices)
        if len(features) < 150:
            result.rejection_reason = "insufficient_data_after_embargo"
            strategy.status = "rejected"
            strategy.rejection_reason = "insufficient_data_after_embargo"
            return result

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, self._validate_sync, strategy, features, prices
        )

        # Atualiza status da estratégia (sempre escreve WF + noise results no DNA)
        strategy.wf_mean_sharpe = result.wf_mean_sharpe
        strategy.walk_forward_ok = result.walk_forward_ok
        strategy.noise_stability = result.noise_stability  # sempre escreve (GA usa no fitness)
        if result.passed:
            strategy.status = "validated"
            strategy.regime_robustness = result.regime_robustness
            strategy.stability_score = result.noise_stability
            strategy.compute_fitness()  # recomputa fitness com noise_stability real
            await self.bus.publish(Event(
                type=EventType.STRATEGY_VALIDATED,
                source="validator",
                payload={"strategy_id": strategy.strategy_id, "score": result.score}
            ))
            log.info(f"✓ Testes internos aprovados: {strategy.name} (score={result.score:.2f})")
        else:
            strategy.status = "rejected"
            strategy.rejection_reason = result.rejection_reason
            strategy.fitness = 0.0  # GA não deve re-selecionar estratégias rejeitadas
            # NÃO chama compute_fitness() — mantém fitness=0 para o GA não selecionar
            await self.bus.publish(Event(
                type=EventType.STRATEGY_REJECTED,
                source="validator",
                payload={
                    "strategy_id": strategy.strategy_id,
                    "reason": result.rejection_reason
                }
            ))
            log.info(f"✗ Rejeitada: {strategy.name} — {result.rejection_reason}")

        self.storage.save_strategy(strategy.strategy_id, strategy.to_dict())
        return result

    def _validate_sync(
        self,
        strategy: StrategyDNA,
        features: pd.DataFrame,
        prices: pd.Series,
    ) -> ValidationResult:
        """Pipeline de validação síncrono."""
        result = ValidationResult(strategy_id=strategy.strategy_id)

        # ── Teste 1: Mínimo de trades ─────────────────────────────
        # Se a estratégia não passou por run_population, executa backtest rápido
        if strategy.n_trades == 0:
            quick = self.engine.run(strategy, features, prices)
            strategy.n_trades = quick.n_trades
            log.debug(f"Backtest rápido: {strategy.name} → {quick.n_trades} trades, sharpe={quick.sharpe:.2f}")
            if strategy.oos_sharpe == 0.0:
                strategy.oos_sharpe = quick.sharpe

        result.n_trades = strategy.n_trades
        result.min_trades_ok = strategy.n_trades >= config.backtest_min_trades
        if not result.min_trades_ok:
            result.rejection_reason = f"too_few_trades ({strategy.n_trades} < {config.backtest_min_trades})"
            return result

        # ── Teste 2: OOS Sharpe mínimo ────────────────────────────
        result.oos_sharpe = strategy.oos_sharpe
        result.oos_sharpe_ok = strategy.oos_sharpe >= config.validation_min_oos_sharpe
        if not result.oos_sharpe_ok:
            result.rejection_reason = f"oos_sharpe_too_low ({strategy.oos_sharpe:.2f})"
            return result

        # ── Teste 3: Complexidade ─────────────────────────────────
        result.complexity = strategy.complexity
        result.complexity_ok = strategy.complexity <= config.validation_max_complexity
        if not result.complexity_ok:
            result.rejection_reason = f"too_complex ({strategy.complexity} rules)"
            return result

        # ── Teste 4: Walk-Forward ─────────────────────────────────
        wf_result = self.wf_validator.validate(
            strategy, features, prices,
            n_folds=config.backtest_walk_forward_folds
        )
        result.wf_mean_sharpe = wf_result.mean_sharpe
        result.walk_forward_ok = wf_result.overall_valid
        if not result.walk_forward_ok:
            result.rejection_reason = (
                f"walk_forward_failed "
                f"(mean_sharpe={wf_result.mean_sharpe:.2f}, "
                f"pct_pos={wf_result.pct_positive_folds:.2f})"
            )
            return result

        # ── Teste 5: Regime Robustness ────────────────────────────
        regime_results = self.wf_validator.regime_split_test(strategy, features, prices)
        robustness = self.wf_validator.compute_regime_robustness(regime_results)
        result.regime_robustness = robustness
        result.regime_robustness_ok = robustness >= config.validation_min_regime_robustness
        if not result.regime_robustness_ok:
            result.rejection_reason = f"regime_not_robust ({robustness:.2f})"
            return result

        # ── Teste 6: Noise Injection ──────────────────────────────
        noise_result = self._noise_injection_test(strategy, features, prices)
        result.noise_stability = noise_result["stability"]
        result.noise_injection_ok = noise_result["stability"] >= 0.5
        if not result.noise_injection_ok:
            result.rejection_reason = f"noise_sensitive (stability={noise_result['stability']:.2f})"
            return result

        # ── Teste 7: Monte Carlo ──────────────────────────────────
        mc_result = self.engine.run_monte_carlo(strategy, features, prices, n_trials=100)
        result.mc_sharpe_p5 = mc_result.get("sharpe_p5", 0)
        result.monte_carlo_ok = mc_result.get("sharpe_p5", 0) > -0.5
        if not result.monte_carlo_ok:
            result.rejection_reason = f"mc_downside_too_large (p5={result.mc_sharpe_p5:.2f})"
            return result

        # ── Teste 8: MARGEM SOBRE COMPRAR E SEGURAR ───────────────
        # O TESTE QUE FALTAVA. Ate 14/08/2026 o GA pontuava contra ZERO: uma
        # estrategia que apenas fica comprada num ativo que subiu era aprovada
        # como "descoberta". Foi exatamente essa falha que fez o motor simbolico
        # produzir quatro "achados" com Sharpe identico +0,89 — todos eram
        # `posicao = +1 sempre`, ou seja, o benchmark fantasiado.
        #
        # Duas laminas, ambas necessarias:
        #  (a) bootstrap em blocos da DIFERENCA de Sharpe contra buy&hold do
        #      proprio ativo — preserva autocorrelacao, ao contrario do iid;
        #  (b) PISO DE SELECAO sqrt(2*ln n)*sqrt(252/T): a vantagem que o melhor
        #      de n candidatos exibiria por PURO ACASO. Foi este piso que matou
        #      o SYMB-1 (margem +0,17 contra piso 0,83).
        try:
            import numpy as _np, math as _m
            _r = self.engine.run(strategy, features, prices)
            # BacktestResult NAO tem `.returns` — tem `equity_curve`. Derivar
            # dali. (A primeira versao deste teste procurava `.returns`, nao
            # achava, e aprovava tudo em silencio: o mesmo tipo de no-op que
            # esta auditoria vem eliminando. Pego antes de entrar em producao.)
            _eq = getattr(_r, "equity_curve", None)
            if _eq is None or len(_eq) < 251:
                result.benchmark_margin_ok = False
                result.rejection_reason = "benchmark_test_unavailable (equity_curve curta)"
                return result
            else:
                _eqv = _np.asarray(_eq, dtype=float)
                _rs = _np.diff(_eqv) / _np.maximum(_eqv[:-1], 1e-12)
                _bh = _np.asarray(prices.pct_change().fillna(0.0).values[-len(_rs):], dtype=float)
                def _sh(x):
                    sd = x.std()
                    return float(x.mean() / sd * _m.sqrt(252)) if sd > 1e-12 else 0.0
                _d = _sh(_rs) - _sh(_bh)
                _T = len(_rs)
                # O numero de hipoteses e o de DNAs unicos que chegaram ao
                # julgamento, nao o numero de folds/reruns no banco. Contar
                # todo backtest transformava reexecucao tecnica em nova tese.
                try:
                    from governance.epistemics import current_data_lineage
                    from governance.experiment_registry import experiments
                    _historical_unique = experiments.count_unique_hypotheses(
                        current_data_lineage()
                    )
                except Exception:
                    _historical_unique = 0
                _n_test = max(_historical_unique,
                              len(getattr(self, "_val_cache", {})) + 1, 2)
                _alpha = 0.05 / _n_test
                # Resolucao suficiente para o limiar atual. Com teto atingido,
                # o teste pode rejeitar por falta de resolucao, nunca aprovar.
                _B = max(4000, min(100000, int(_m.ceil(5.0 / _alpha))))
                # bootstrap em blocos de 21 pregoes
                _rng = _np.random.default_rng(7); _bl = 21
                _nb = max(_T // _bl, 2); _ds = _np.empty(_B)
                for _i in range(_B):
                    _idx = _np.concatenate([_np.arange(x, x + _bl)
                                            for x in _rng.integers(0, max(_T - _bl, 1), _nb)])
                    _idx = _idx[_idx < _T]
                    _ds[_i] = _sh(_rs[_idx]) - _sh(_bh[_idx])
                _p = float((_np.count_nonzero(_ds <= 0) + 1) / (_B + 1))
                # CORRECAO DE MULTIPLICIDADE NO VALOR-p, NAO NO TAMANHO DO EFEITO.
                #
                # A primeira versao exigia margem >= sqrt(2 ln n)*sqrt(252/T).
                # Medido empiricamente em 14/08/2026: o melhor-de-N por acaso
                # atinge margem p95 de -0,08 a -0,03 — ou seja, o vies de selecao
                # NESTA estatistica e praticamente zero, enquanto o piso teorico
                # pedia +0,42 a +0,63. Cerca de 20x rigoroso demais.
                #
                # Motivo: sqrt(2 ln n) corrige o MAXIMO de n Sharpes INDEPENDENTES
                # contra nulo zero. Aqui a estatistica e a DIFERENCA contra o
                # buy&hold, e todos os candidatos sao correlacionados com o
                # proprio benchmark — a diferenca quase nao infla com a busca.
                # Do jeito anterior o teste exigiria bater o indice por +0,6 de
                # Sharpe e rejeitaria qualquer edge real.
                #
                # O correto e Bonferroni sobre o valor-p do bootstrap.
                # GUARDA DE VOLATILIDADE ECONOMICA.
                # Ataque adversarial contra este proprio teste (14/08/2026):
                # uma serie em caixa 94% do tempo com 400 acertos de +0,2%
                # exibiu Sharpe +3,99 sobre vol de 0,7% e PASSOU com margem
                # +3,385. Sharpe a essa volatilidade e economicamente vazio:
                # precisaria de ~20x de alavancagem para importar, e o edge nao
                # sobreviveria a custo nem a capacidade. Exigimos que a
                # estrategia carregue risco comparavel ao do benchmark.
                _vol_s = float(_np.std(_rs) * _m.sqrt(252))
                _vol_b = float(_np.std(_bh) * _m.sqrt(252))
                _vol_min = 0.20 * _vol_b          # ao menos 20% do risco do indice
                if _vol_s < _vol_min:
                    result.benchmark_margin = _d
                    result.benchmark_p = 1.0
                    result.benchmark_margin_ok = False
                    result.rejection_reason = (
                        f"vol economicamente irrelevante ({_vol_s:.1%} contra "
                        f"minimo {_vol_min:.1%} = 20% do indice) — Sharpe alto "
                        f"sobre risco quase nulo nao e escalavel"
                    )
                    return result

                # PORTAO DUPLO. Medido em 14/08/2026: o Sharpe do buy&hold varia
                # de -0,66 (VIXY) a +1,19 (BNB) no universo — amplitude de 1,85.
                # Com benchmark so no PROPRIO ativo, bastava mirar o VIXY e ficar
                # parado para "bater o indice" por +0,66. Com benchmark so no SPY,
                # bastava ficar comprado em GOOGL (0,90 contra 0,60) sem nenhuma
                # habilidade de timing. Cada portao sozinho e gameavel na direcao
                # oposta; juntos, nao.
                #
                #   (a) bater o buy&hold do PROPRIO ativo -> prova timing
                #   (b) bater o SPY                        -> prova que vale a pena
                #                                             ter isso em vez do indice
                _ok_a = (_d > 0.0) and (_p < _alpha)

                _ok_b = False; _d_spy = None; _p_spy = None
                try:
                    # BENCHMARK EM CACHE. Antes este load_features("SPY") rodava
                    # POR ESTRATEGIA — 20 leituras de disco/DuckDB por geracao,
                    # concorrendo com as 500 escritas de save_backtest do
                    # executor. DuckDB e single-writer: isso virava contencao de
                    # lock e travava a geracao inteira (19/08/2026: geracao 130
                    # repetindo em loop, evolution_step morrendo por timeout de
                    # 600s sem nunca incrementar o contador).
                    if not hasattr(self, "_spy_cache"):
                        self._spy_cache = self.storage.load_features("SPY")
                    _spy_df = self._spy_cache
                    if _spy_df is not None and "close" in _spy_df.columns:
                        _spy_r = _spy_df["close"].astype(float).pct_change().fillna(0.0).values
                        _spy_r = _spy_r[-len(_rs):] if len(_spy_r) >= len(_rs) else None
                        if _spy_r is not None and len(_spy_r) == len(_rs):
                            def _shf(x):
                                sd = x.std()
                                return float(x.mean() / sd * _m.sqrt(252)) if sd > 1e-12 else 0.0
                            _d_spy = _shf(_rs) - _shf(_spy_r)
                            _ds2 = _np.empty(_B)
                            for _i in range(_B):
                                _idx = _np.concatenate([_np.arange(x, x + _bl)
                                        for x in _rng.integers(0, max(_T - _bl, 1), _nb)])
                                _idx = _idx[_idx < _T]
                                _ds2[_i] = _shf(_rs[_idx]) - _shf(_spy_r[_idx])
                            _p_spy = float((_np.count_nonzero(_ds2 <= 0) + 1) / (_B + 1))
                            _ok_b = (_d_spy > 0.0) and (_p_spy < _alpha)
                except Exception as _e2:
                    log.debug(f"perna SPY do teste 8: {_e2}")

                result.benchmark_margin = _d
                result.benchmark_p = _p
                result.selection_floor = _alpha
                result.benchmark_margin_ok = _ok_a and _ok_b
                if not result.benchmark_margin_ok:
                    _qual = "proprio ativo" if not _ok_a else "SPY"
                    _dd = _d if not _ok_a else (_d_spy if _d_spy is not None else 0.0)
                    _pp = _p if not _ok_a else (_p_spy if _p_spy is not None else 1.0)
                    result.rejection_reason = (
                        f"nao bate o benchmark [{_qual}] (margem={_dd:+.3f}, "
                        f"P(<=0)={_pp:.4f}, limiar-multiplicidade={_alpha:.5f}, "
                        f"hipoteses={_n_test}, bootstrap={_B})"
                    )
                    return result
        except Exception as _e:
            log.warning(f"teste de margem sobre benchmark indisponivel: {_e}")
            result.benchmark_margin_ok = False
            result.rejection_reason = f"benchmark_test_error ({type(_e).__name__})"
            return result

        # ── APROVADO ──────────────────────────────────────────────
        result.passed = True
        result.compute_score()
        return result

    def _noise_injection_test(
        self,
        strategy: StrategyDNA,
        features: pd.DataFrame,
        prices: pd.Series,
        n_trials: int = None,
    ) -> Dict:
        """
        Injeta ruído nos parâmetros das regras e verifica estabilidade.
        Estratégia sensível a pequenas mudanças = overfit.
        """
        n_trials = n_trials or config.validation_noise_trials
        sharpes = []

        for trial in range(n_trials):
            # Cria cópia com parâmetros perturbados
            noisy = strategy.clone()
            for rule in noisy.entry_rules:
                noise = np.random.normal(0, 0.1)   # 10% de ruído
                rule.threshold += noise
                rule.weight *= (1 + np.random.normal(0, 0.05))

            noisy.exit_rule.stop_loss_pct *= (1 + np.random.normal(0, 0.1))
            noisy.exit_rule.stop_loss_pct = np.clip(noisy.exit_rule.stop_loss_pct, 0.005, 0.15)

            r = self.engine.run(noisy, features, prices)
            if r.is_valid:
                sharpes.append(r.sharpe)

        if not sharpes:
            return {"stability": 0.0, "n_valid": 0}

        arr = np.array(sharpes)
        # Estabilidade: % de testes com Sharpe > 0 E desvio padrão baixo
        pct_positive = float(np.mean(arr > 0))
        std_norm = float(np.std(arr) / (abs(np.mean(arr)) + 1e-8))  # CV
        stability = pct_positive * (1 - min(std_norm, 1.0))

        return {
            "stability": float(stability),
            "sharpe_mean": float(np.mean(arr)),
            "sharpe_std": float(np.std(arr)),
            "pct_positive": pct_positive,
            "n_valid": len(sharpes),
        }

    def _cache_key(self, strategy: StrategyDNA) -> str:
        """Identidade para fins de validacao: estrutura + epoca dos dados.

        A epoca entra porque uma estrategia validada sobre 487 barras NAO esta
        validada sobre 8.441 — mudou o mundo, tem de ser rejulgada uma vez.
        """
        try:
            fp = strategy.fingerprint()
        except Exception:
            fp = getattr(strategy, "strategy_id", str(id(strategy)))
        return f"{fp}@{self._data_epoch()}"

    def _validate_sync(self, strategy: StrategyDNA) -> ValidationResult:
        """Executa validate_single fora do event loop principal."""
        import asyncio as _a
        _l = _a.new_event_loop()
        try:
            return _l.run_until_complete(self.validate_single(strategy))
        finally:
            _l.close()

    async def validate_batch(self, strategies: List[StrategyDNA]) -> List[ValidationResult]:
        """Valida um lote de estratégias em paralelo."""
        # CACHE DE VALIDACAO. A elite e estavel entre geracoes (e o ponto do
        # elitismo), entao validate_batch(elite[:20]) rejulgava as MESMAS
        # estrategias a cada geracao. Medido em 14/08/2026: 552 validacoes para
        # 12 estrategias distintas — uma delas 257 vezes. Cada validacao roda
        # walk-forward de 5 folds + teste por regime + injecao de ruido + 100
        # simulacoes Monte Carlo, agora sobre 8.441 barras. 97,8% de desperdicio,
        # em CPU que fazia falta para a evolucao de verdade.
        # Registra cada DNA que chegou ao tribunal, inclusive se o resultado
        # vier de cache. A contagem e append-only por linhagem de dados.
        try:
            from governance.epistemics import current_data_lineage
            from governance.experiment_registry import experiments
            lineage = current_data_lineage()
            for candidate in strategies:
                experiments.record_hypothesis_test(candidate.fingerprint(), lineage)
        except Exception as registry_error:
            log.warning(f"Registro de hipótese falhou: {registry_error}")

        novas, reaproveitadas = [], 0
        for s_ in strategies:
            k = self._cache_key(s_)
            if k in self._val_cache:
                r = self._val_cache[k]
                self._apply_cached_result(s_, r)
                reaproveitadas += 1
            else:
                novas.append(s_)
        if reaproveitadas:
            log.debug(f"Validacao: {reaproveitadas} reaproveitadas do cache, "
                      f"{len(novas)} novas")
        strategies = novas
        if not strategies:
            return []

        # EVENT LOOP: validate_single e SINCRONO por dentro (backtest, walk-forward,
        # Monte Carlo, bootstrap). Com asyncio.gather as 20 rodavam na MESMA thread
        # e bloqueavam o loop inteiro por minutos — em 19/08/2026 isso derrubou
        # 414 risk_check, 42 regime_predict e 41 data_ingest por timeout, alem da
        # propria evolution_step. Agora vai para thread pool: o loop continua
        # respirando e os outros jobs sobrevivem.
        _loop = asyncio.get_running_loop()
        tasks = [_loop.run_in_executor(None, self._validate_sync, s) for s in strategies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results = []
        for r in results:
            if isinstance(r, ValidationResult):
                valid_results.append(r)

        for _s, _r in zip(strategies, results):
            if not isinstance(_r, Exception):
                self._val_cache[self._cache_key(_s)] = _r
        if len(self._val_cache) > 5000:                    # teto de memoria
            for _k in list(self._val_cache)[:1000]:
                self._val_cache.pop(_k, None)
        self._save_validation_cache()

        passed = sum(1 for r in valid_results if r.passed)
        log.info(f"Validação batch: {passed}/{len(strategies)} aprovadas "
                 f"(novas; cache com {len(self._val_cache)} julgamentos)")
        return valid_results

    async def _load_data(
        self, strategy: StrategyDNA
    ) -> Tuple[Optional[pd.DataFrame], Optional[pd.Series]]:
        """Carrega features e preços para validação."""
        for asset in strategy.target_assets + ["SPY"]:
            df = self.storage.load_features(asset)
            if df is not None and len(df) >= 150:
                if "close" in df.columns:
                    return df, df["close"]
                raw = self.storage.load_raw(asset)
                if raw is not None:
                    return df, raw["close"].reindex(df.index, method="ffill")
        return None, None

    async def audit_holdout(self, strategy: StrategyDNA) -> Dict:
        """
        Auditoria final no held-out embargado (últimos embargo_fraction).
        ÚNICO caminho autorizado a tocar a zona proibida. Resultados aqui NÃO
        entram em seleção, fitness ou validação — apenas inspeção honesta de
        generalização em dados nunca vistos durante a tomada de decisão.
        """
        features, prices = await self._load_data(strategy)
        if features is None:
            return {"strategy_id": strategy.strategy_id, "error": "no_data"}
        _research, (audit_features, audit_prices) = split_embargo(features, prices)
        if len(audit_features) < 30:
            return {
                "strategy_id": strategy.strategy_id,
                "error": "insufficient_holdout",
                "n": len(audit_features),
            }
        r = self.engine.run(strategy, audit_features, audit_prices)
        return {
            "strategy_id": strategy.strategy_id,
            "holdout_sharpe": float(r.sharpe),
            "holdout_n_trades": int(r.n_trades),
            "holdout_max_drawdown": float(r.max_drawdown),
            "holdout_rows": int(len(audit_features)),
        }

    async def start(self):
        log.info("ValidationCluster iniciado")

    async def stop(self):
        log.info("ValidationCluster parado")
