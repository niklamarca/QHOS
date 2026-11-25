"""
QUANT HPC OS — Genetic Evolution Engine
Seleção evolutiva de estratégias: crossover, mutação, elitismo, diversidade.
Gera 500+ estratégias por geração com operadores genéticos reais.
"""
from __future__ import annotations
import asyncio
import copy
import json
import os
import time
import uuid
import numpy as np
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from pathlib import Path
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger
from data.storage import StorageEngine
from .strategy_dna import StrategyDNA, TradingRule, ExitRule, RuleType, SignalType, StrategyFactory

if TYPE_CHECKING:
    from backtest.cpu_cluster import BacktestCluster
    from validation.validator import ValidationCluster

log = get_logger("GeneticEngine")


class GeneticEngine:
    """
    Motor de evolução genética de estratégias.

    Pipeline por geração:
    1. Inicializa população aleatória (geração 0)
    2. Avalia fitness via backtest paralelo
    3. Seleciona elite (top %)
    4. Crossover entre pares de elite
    5. Mutação de indivíduos
    6. Injeta diversidade aleatória
    7. Filtra duplicatas por fingerprint
    8. Loop para próxima geração
    """

    def __init__(
        self,
        storage: StorageEngine,
        backtest_cluster: Optional["BacktestCluster"] = None,
        validator: Optional["ValidationCluster"] = None,
    ):
        self.storage = storage
        self.backtest_cluster = backtest_cluster
        self.validator = validator
        self.bus = event_bus

        self.population: List[StrategyDNA] = []
        # Arquivo estrito: somente candidatos que passaram os testes internos.
        # O nome legado permanece na API/arquivo por compatibilidade.
        self.hall_of_fame: List[StrategyDNA] = []
        self.generation: int = 0
        self._rng = np.random.default_rng(seed=42)
        self._generation_running: bool = False
        self._seen_fingerprints: set = set()
        # Penalidade por família com alta taxa de rejeição (sliding window)
        self._family_rejection_rate: dict = {}  # family → 0.0-1.0
        self._family_stats: list = []           # [(family, rejected), ...] — últimas 200 validações
        self._checkpoint_path = Path(config.data_lake_dir) / "evolution_state.json"
        self._checkpoint_loaded = False

        # LLM agent (injetado externamente)
        self.llm_agents = None

    def inject_dependencies(self, backtest_cluster, validator, llm_agents=None):
        self.backtest_cluster = backtest_cluster
        self.validator = validator
        self.llm_agents = llm_agents

    @staticmethod
    def _current_data_epoch() -> str:
        from governance.epistemics import current_scientific_epoch
        return current_scientific_epoch()

    def restore_checkpoint(self) -> bool:
        """Restaura uma geração concluída; nunca um checkpoint parcial.

        A época dos dados é parte do contrato. Uma população evoluída sobre uma
        janela diferente não é um ativo científico e não pode contaminar o run.
        """
        try:
            if not self._checkpoint_path.exists():
                return False
            payload = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
            if payload.get("schema") not in {1, 2}:
                raise ValueError("schema desconhecido")
            same_epoch = payload.get("data_epoch") == self._current_data_epoch()
            population = [
                StrategyDNA.from_dict(item) for item in payload.get("population", [])
                if str(item.get("status", "")).lower() not in {"rejected", "falsified"}
            ]
            if same_epoch:
                hall = [
                    StrategyDNA.from_dict(item) for item in payload.get("hall_of_fame", [])
                    if str(item.get("status", "")).lower() == "validated"
                ]
            else:
                # Memoria estrutural sobrevive; métricas/julgamentos não. Assim
                # uma sessão nova não joga fora os DNAs descobertos, mas nenhum
                # Sharpe antigo se fantasia de evidência atual.
                hall = []
                for strategy in population:
                    strategy.status = "pending"
                    strategy.rejection_reason = None
                    strategy.fitness = 0.0
                    strategy.oos_sharpe = 0.0
                    strategy.is_sharpe = 0.0
                    strategy.max_drawdown = 0.0
                    strategy.win_rate = 0.0
                    strategy.n_trades = 0
                    strategy.stability_score = 0.0
                    strategy.regime_robustness = 0.0
                    strategy.wf_mean_sharpe = 0.0
                    strategy.walk_forward_ok = False
                    strategy.noise_stability = -1.0
            if not population:
                raise ValueError("população vazia")
            self.population = population
            self.hall_of_fame = hall
            self.generation = max(0, int(payload.get("generation", 0)))
            self._seen_fingerprints = {s.fingerprint() for s in self.population}
            stats = payload.get("family_stats", [])
            self._family_stats = [
                (str(item[0]), bool(item[1])) for item in stats
                if isinstance(item, list) and len(item) == 2
            ][-300:]
            self._rebuild_family_rejection_rates()
            rng_state = payload.get("rng_state")
            if isinstance(rng_state, dict):
                self._rng.bit_generator.state = rng_state
            self._checkpoint_loaded = True
            if same_epoch:
                log.warning(
                    f"Checkpoint evolutivo restaurado: geração {self.generation}, "
                    f"população {len(self.population)}, aprovados {len(self.hall_of_fame)}"
                )
            else:
                log.warning(
                    f"Checkpoint: {len(self.population)} estruturas recuperadas; "
                    "métricas e aprovações invalidadas pela nova época científica"
                )
            return True
        except Exception as exc:
            log.warning(f"Checkpoint evolutivo ignorado por erro: {type(exc).__name__}: {exc}")
            return False

    def save_checkpoint(self) -> None:
        """Persiste somente uma geração completa usando replace atômico."""
        try:
            self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema": 2,
                "data_epoch": self._current_data_epoch(),
                "saved_at": time.time(),
                "generation": self.generation,
                "population": [strategy.to_dict() for strategy in self.population],
                "hall_of_fame": [strategy.to_dict() for strategy in self.hall_of_fame],
                "family_stats": [[family, rejected] for family, rejected in self._family_stats[-300:]],
                "rng_state": self._rng.bit_generator.state,
            }
            tmp = str(self._checkpoint_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self._checkpoint_path)
            log.info(
                f"Checkpoint evolutivo salvo: geração {self.generation}, "
                f"população {len(self.population)}, HOF {len(self.hall_of_fame)}"
            )
        except Exception as exc:
            log.error(f"Checkpoint evolutivo falhou: {type(exc).__name__}: {exc}")

    def _rebuild_family_rejection_rates(self) -> None:
        from collections import defaultdict
        counts: dict = defaultdict(lambda: [0, 0])
        for family, rejected in self._family_stats:
            counts[family][0] += 1
            counts[family][1] += int(rejected)
        self._family_rejection_rate = {
            family: values[1] / values[0]
            for family, values in counts.items() if values[0] >= 3
        }

    # ─── Population Initialization ────────────────────────────────────

    def initialize_population(
        self,
        size: int = None,
        available_assets: Optional[List[str]] = None,
    ) -> List[StrategyDNA]:
        """Gera população inicial com warm start: carrega melhores do DB + novas aleatórias."""
        import json
        size = size or config.evolution_population_size
        assets = available_assets or config.data_assets_stocks[:10]

        if self._checkpoint_loaded and self.population:
            log.warning(
                f"População preservada do checkpoint: geração {self.generation}, "
                f"{len(self.population)} estratégias — warm start dispensado"
            )
            return self.population

        log.info(f"Inicializando população (warm start): alvo={size} estratégias")
        population = []

        # ── EPOCA DE DADOS ───────────────────────────────────────────
        # Estrategias validadas sob uma janela de dados diferente NAO podem
        # semear a populacao: o Sharpe que as qualificou foi medido em 487
        # barras. Semear com elas enviesaria a nova evolucao para justamente
        # as estruturas que sobreajustaram o ruido. Quando a epoca muda, o
        # warm start e pulado UMA vez; nada e apagado do banco.
        _EPOCA = self._current_data_epoch()
        _epoca_ok = True
        try:
            import os as _os
            _ep_f = _os.path.join(str(config.data_lake_dir), "data_epoch.json")
            _ant = None
            if _os.path.exists(_ep_f):
                _ant = json.load(open(_ep_f)).get("epoch")
            if _ant != _EPOCA:
                _epoca_ok = False
                log.warning(
                    f"EPOCA DE DADOS mudou ({_ant} -> {_EPOCA}): warm start PULADO "
                    "nesta inicializacao. As estrategias antigas foram validadas sobre "
                    "487 barras; agora a pesquisa ve o historico completo. Nada foi "
                    "apagado do banco — apenas nao serve de semente."
                )
                _tmp = _ep_f + ".tmp"
                with open(_tmp, "w") as _f:
                    json.dump({"epoch": _EPOCA, "ts": __import__("time").time()}, _f)
                _os.replace(_tmp, _ep_f)
        except Exception as _e:
            log.debug(f"marcador de epoca: {_e}")

        # ── Warm Start: carrega estratégias validadas do DB ───────────
        # Semeia com até 40% da população de runs anteriores para não
        # perder centenas de gerações de evolução após restart
        try:
            if not _epoca_ok:
                raise RuntimeError("epoca de dados mudou — sem warm start")
            df = self.storage.get_validated_strategies(min_sharpe=1.0)
            if not df.empty:
                max_seeds = int(size * 0.4)
                max_per_name = 3  # máx 3 por fingerprint-família — força diversidade
                seeded = 0
                name_counts: dict = {}
                for _, row in df.iterrows():
                    try:
                        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                        dna = StrategyDNA.from_dict(meta)
                        # Limita por família de nome (ex: pattern_df5d81, regime_filter_13bdca)
                        family = dna.name.rsplit("_", 1)[0] if "_" in dna.name else dna.name
                        if name_counts.get(family, 0) >= max_per_name:
                            continue
                        dna.fitness = 0.0          # resetar fitness — será recomputado
                        dna.status = "pending"     # revalidar neste run
                        dna.noise_stability = -1.0 # forçar re-teste de noise no validator
                        fp = dna.fingerprint()
                        if fp not in self._seen_fingerprints:
                            population.append(dna)
                            self._seen_fingerprints.add(fp)
                            name_counts[family] = name_counts.get(family, 0) + 1
                            seeded += 1
                            if seeded >= max_seeds:
                                break
                    except Exception as e:
                        log.debug(f"Warm start: falha ao carregar estratégia: {e}")
                log.info(f"Warm start: {seeded} estratégias carregadas do DB — famílias: {dict(name_counts)}")
            else:
                log.info("Warm start: nenhuma estratégia validada no DB — população 100% aleatória")
        except Exception as e:
            log.warning(f"Warm start falhou, usando população aleatória: {e}")

        # ── Complementa com estratégias aleatórias ────────────────────
        attempts = 0
        while len(population) < size and attempts < size * 3:
            dna = StrategyFactory.random(assets, self._rng)
            fp = dna.fingerprint()
            if fp not in self._seen_fingerprints:
                population.append(dna)
                self._seen_fingerprints.add(fp)
            attempts += 1

        self.population = population
        log.info(f"População inicializada: {len(population)} estratégias únicas")
        return population

    # ─── Genetic Operators ───────────────────────────────────────────

    def select_parents(
        self,
        population: List[StrategyDNA],
        n: int,
        method: str = "tournament",
    ) -> List[StrategyDNA]:
        """Seleção de pais para crossover."""
        if method == "tournament":
            return self._tournament_selection(population, n)
        elif method == "roulette":
            return self._roulette_selection(population, n)
        else:
            return self._rank_selection(population, n)

    def _tournament_selection(
        self, pop: List[StrategyDNA], n: int, tournament_size: int = 5
    ) -> List[StrategyDNA]:
        """Tournament selection com penalidade por família de alta rejeição."""
        def _effective_fitness(s: StrategyDNA) -> float:
            family = s.name.rsplit("_", 1)[0] if "_" in s.name else s.name
            rej_rate = self._family_rejection_rate.get(family, 0.0)
            # Penalidade: família com 40% rejeição → fitness × 0.6
            # Penalidade máxima: 70% (família nunca zerada — mantém pressão mínima)
            penalty = max(0.30, 1.0 - rej_rate)
            return s.fitness * penalty

        selected = []
        for _ in range(n):
            candidates = self._rng.choice(
                len(pop),
                size=min(tournament_size, len(pop)),
                replace=False
            )
            winner = max([pop[i] for i in candidates], key=_effective_fitness)
            selected.append(winner)
        return selected

    def _roulette_selection(self, pop: List[StrategyDNA], n: int) -> List[StrategyDNA]:
        """Roulette wheel selection baseada em fitness."""
        fitnesses = np.array([s.fitness for s in pop]) + 1e-8
        probs = fitnesses / fitnesses.sum()
        indices = self._rng.choice(len(pop), size=n, p=probs, replace=True)
        return [pop[i] for i in indices]

    def _rank_selection(self, pop: List[StrategyDNA], n: int) -> List[StrategyDNA]:
        """Rank selection — favorece elite mas mantém diversidade."""
        ranked = sorted(pop, key=lambda s: s.fitness, reverse=True)
        ranks = np.arange(len(ranked), 0, -1).astype(float)
        probs = ranks / ranks.sum()
        indices = self._rng.choice(len(ranked), size=n, p=probs)
        return [ranked[i] for i in indices]

    def crossover(self, parent1: StrategyDNA, parent2: StrategyDNA) -> List[StrategyDNA]:
        """
        Crossover entre duas estratégias.
        Produz 2 filhos com regras recombinadas.
        """
        if self._rng.random() > config.evolution_crossover_rate:
            return [parent1.clone(), parent2.clone()]

        child1 = parent1.clone()
        child2 = parent2.clone()

        # Troca alguns genes (regras de entrada)
        rules1 = copy.deepcopy(parent1.entry_rules)
        rules2 = copy.deepcopy(parent2.entry_rules)

        # Crossover de ponto único
        if len(rules1) > 1 and len(rules2) > 1:
            pt1 = int(self._rng.integers(1, len(rules1)))
            pt2 = int(self._rng.integers(1, len(rules2)))
            child1.entry_rules = rules1[:pt1] + rules2[pt2:]
            child2.entry_rules = rules2[:pt2] + rules1[pt1:]

        # Troca parâmetros de saída
        if self._rng.random() < 0.5:
            child1.exit_rule, child2.exit_rule = (
                copy.deepcopy(parent2.exit_rule),
                copy.deepcopy(parent1.exit_rule),
            )

        # Crossover de assets alvo
        if self._rng.random() < 0.5:
            all_assets = list(set(parent1.target_assets + parent2.target_assets))
            n = self._rng.integers(1, max(2, len(all_assets)))
            child1.target_assets = list(
                self._rng.choice(all_assets, size=min(n, len(all_assets)), replace=False)
            )
            child2.target_assets = child1.target_assets.copy()

        # Trim: máximo 6 regras
        child1.entry_rules = child1.entry_rules[:6]
        child2.entry_rules = child2.entry_rules[:6]

        return [child1, child2]

    def mutate(self, strategy: StrategyDNA) -> StrategyDNA:
        """Mutação de uma estratégia — altera thresholds, features, parâmetros."""
        if self._rng.random() > config.evolution_mutation_rate:
            return strategy

        mutated = strategy.clone()

        # Escolhe tipo de mutação
        # rule_type_swap (15%): troca família inteira → gera novo fingerprint
        mutation_type = self._rng.choice([
            "threshold", "threshold", "operator", "weight",
            "add_rule", "remove_rule", "exit_params",
            "asset_swap", "position_size", "rule_type_swap",
        ])

        if mutation_type == "threshold" and mutated.entry_rules:
            idx = self._rng.integers(0, len(mutated.entry_rules))
            rule = mutated.entry_rules[idx]
            rule.threshold += float(self._rng.normal(0, 0.3))
            rule.threshold = np.clip(rule.threshold, -5.0, 5.0)

        elif mutation_type == "operator" and mutated.entry_rules:
            idx = self._rng.integers(0, len(mutated.entry_rules))
            ops = [">", "<", ">|<", "rolling_z", "percentile_rank"]
            mutated.entry_rules[idx].operator = str(self._rng.choice(ops))

        elif mutation_type == "weight" and mutated.entry_rules:
            idx = self._rng.integers(0, len(mutated.entry_rules))
            mutated.entry_rules[idx].weight *= float(self._rng.uniform(0.5, 2.0))
            mutated.entry_rules[idx].weight = np.clip(
                mutated.entry_rules[idx].weight, 0.1, 5.0
            )

        elif mutation_type == "add_rule" and len(mutated.entry_rules) < 6:
            pool = StrategyFactory.FEATURE_POOLS.get(mutated.rule_type, [])
            if pool:
                feat = str(self._rng.choice(pool))
                new_rule = TradingRule(
                    rule_type=mutated.rule_type,
                    feature=feat,
                    operator=str(self._rng.choice([">", "<", "rolling_z"])),
                    threshold=float(self._rng.uniform(-2, 2)),
                    weight=float(self._rng.uniform(0.5, 2.0)),
                )
                mutated.entry_rules.append(new_rule)

        elif mutation_type == "remove_rule" and len(mutated.entry_rules) > 1:
            idx = self._rng.integers(0, len(mutated.entry_rules))
            mutated.entry_rules.pop(int(idx))

        elif mutation_type == "exit_params":
            mutated.exit_rule.stop_loss_pct = float(
                np.clip(mutated.exit_rule.stop_loss_pct + self._rng.normal(0, 0.005), 0.005, 0.10)
            )
            mutated.exit_rule.take_profit_pct = float(
                np.clip(mutated.exit_rule.take_profit_pct + self._rng.normal(0, 0.01), 0.01, 0.30)
            )
            mutated.exit_rule.max_holding_days = int(
                np.clip(mutated.exit_rule.max_holding_days + self._rng.integers(-5, 6), 1, 120)
            )

        elif mutation_type == "asset_swap":
            all_assets = config.data_assets_stocks[:15]
            if all_assets:
                new_asset = str(self._rng.choice(all_assets))
                if new_asset not in mutated.target_assets:
                    if len(mutated.target_assets) > 1:
                        idx = self._rng.integers(0, len(mutated.target_assets))
                        mutated.target_assets[int(idx)] = new_asset
                    else:
                        mutated.target_assets = [new_asset]

        elif mutation_type == "position_size":
            mutated.position_size = float(
                np.clip(mutated.position_size + self._rng.normal(0, 0.01), 0.01, 0.10)
            )

        elif mutation_type == "rule_type_swap":
            # Troca a família inteira da estratégia → gera fingerprint completamente novo
            from evolution.strategy_dna import RuleType
            all_types = [rt for rt in RuleType if rt != mutated.rule_type]
            new_type = RuleType(self._rng.choice([rt.value for rt in all_types]))
            new_strat = StrategyFactory.random(mutated.target_assets, self._rng)
            # Herda assets e posição do pai, mas regras e tipo são novos
            new_strat.target_assets = mutated.target_assets.copy()
            new_strat.position_size = mutated.position_size
            log.debug(f"rule_type_swap: {mutated.rule_type.value} → {new_strat.rule_type.value}")
            return new_strat

        return mutated

    # ─── Evolution Cycle ─────────────────────────────────────────────

    async def run_generation(self) -> Dict:
        if self._generation_running:
            log.warning("run_generation já em execução — pulando chamada duplicada")
            return {}
        self._generation_running = True
        try:
            return await self._run_generation_impl()
        finally:
            self._generation_running = False

    async def _run_generation_impl(self) -> Dict:
        """Executa um ciclo completo de evolução."""
        gen = self.generation
        log.info(f"=" * 50)
        log.info(f"Geração {gen} — população: {len(self.population)}")
        t_start = time.time()

        # 1. Avalia fitness via backtest
        if self.backtest_cluster and self.population:
            log.info(f"  Avaliando fitness de {len(self.population)} estratégias...")
            await self.backtest_cluster.run_population(self.population)

        # 2. Filtra estratégias sem trades suficientes
        viable = [
            s for s in self.population
            if s.n_trades >= config.backtest_min_trades
            and str(s.status).lower() not in {"rejected", "falsified"}
        ]
        log.info(f"  Estratégias viáveis: {len(viable)}/{len(self.population)}")

        if not viable:
            log.warning("Nenhuma estratégia viável — reinicializando população parcialmente")
            assets = config.data_assets_stocks[:10]
            new_individuals = [
                StrategyFactory.random(assets, self._rng) for _ in range(50)
            ]
            self.population.extend(new_individuals)
            viable = self.population if self.population else new_individuals

        # 3. Calcula fitness e ordena. Marcas paper sao anexadas como telemetria;
        # por padrao nao afetam selecao (ver online_feedback_affects_selection).
        try:
            from core.live_performance import get_live_performance
            _live = get_live_performance()
            for s in viable:
                _n, _mean, _sh = _live.stats(s.strategy_id)
                s.live_trades = _n
                s.live_sharpe = _sh
        except Exception as _le:
            log.debug(f"live feedback indisponivel: {_le}")
        for s in viable:
            s.compute_fitness()
        viable.sort(key=lambda s: s.fitness, reverse=True)

        # 4. Julga a fronteira ANTES de selecionar/reproduzir. O fluxo antigo
        # colocava a elite no arquivo historico e gerava filhos antes de saber
        # que os pais haviam sido rejeitados pelo walk-forward.
        top_candidates = viable[:20]
        if self.validator:
            try:
                await self.validator.validate_batch(top_candidates)
            except Exception as e:
                log.warning(f"Validação falhou: {e}")

        for s in top_candidates:
            family = s.name.rsplit("_", 1)[0] if "_" in s.name else s.name
            self._family_stats.append((family, s.status == "rejected"))
        self._family_stats = self._family_stats[-300:]
        self._rebuild_family_rejection_rates()
        if self._family_rejection_rate:
            top_rej = sorted(self._family_rejection_rate.items(), key=lambda x: -x[1])[:3]
            log.info(f"  Rejeição por família: {[(f, f'{r:.0%}') for f, r in top_rej]}")

        viable = [
            s for s in viable
            if str(s.status).lower() not in {"rejected", "falsified"}
        ]
        for s in viable:
            s.compute_fitness()
        viable.sort(key=lambda s: s.fitness, reverse=True)
        if not viable:
            log.warning("Todos os candidatos julgados foram rejeitados; reiniciando busca")
            assets = config.data_assets_stocks[:10]
            viable = [StrategyFactory.random(assets, self._rng) for _ in range(50)]

        # 5. Elitismo — somente candidatos nao rejeitados sobrevivem.
        n_elite = max(2, int(len(viable) * config.evolution_elite_fraction))
        elite = viable[:n_elite]

        # O arquivo historico aceita SOMENTE aprovados nos testes internos.
        self._update_hall_of_fame([s for s in elite if s.status == "validated"])

        # 6. Consulta LLM agents (ocasionalmente)
        llm_injections = []
        if (self.llm_agents and
                gen % max(1, int(1 / config.evolution_llm_sample_rate)) == 0):
            log.info("  Consultando agentes LLM...")
            try:
                llm_injections = await self.llm_agents.generate_strategies(
                    top_strategies=elite[:5],
                    n_strategies=10,
                )
            except Exception as e:
                log.warning(f"  LLM agents erro: {e}")

        # 7. Gera nova população
        target_size = config.evolution_population_size
        new_population = list(elite)  # Elite preservada

        # Crossover
        n_crossover = int(target_size * 0.5)
        parents = self.select_parents(viable, n_crossover * 2)
        for i in range(0, len(parents) - 1, 2):
            children = self.crossover(parents[i], parents[i + 1])
            new_population.extend(children)

        # Mutação
        n_mutate = int(target_size * 0.2)
        for _ in range(n_mutate):
            parent = self.select_parents(viable, 1)[0]
            mutant = self.mutate(parent)
            new_population.append(mutant)

        # LLM injections
        new_population.extend(llm_injections)

        # Novas aleatórias — garante mínimo de 15% da população
        n_min_random = int(target_size * 0.15)  # 75 estratégias em pop=500
        n_random = max(target_size - len(new_population), n_min_random)
        assets = config.data_assets_stocks[:10]
        for _ in range(max(0, n_random)):
            new_population.append(
                StrategyFactory.random(assets, self._rng)
            )

        # 8. Remove duplicatas por fingerprint + cap 15% por família
        seen = set()
        unique_pop = []
        family_counts: dict = {}
        asset_counts: dict = {}
        max_per_family = max(10, int(target_size * 0.15))  # 15% do tamanho alvo
        # Cap por ATIVO primario (config-gated): evita colapso num unico ticker
        _asset_frac = getattr(config, "evolution_max_per_asset_frac", 0.0)
        max_per_asset = int(target_size * _asset_frac) if _asset_frac > 0 else 0
        for s in new_population:
            fp = s.fingerprint()
            if fp in seen:
                continue
            family = s.name.rsplit("_", 1)[0] if "_" in s.name else s.name
            if family_counts.get(family, 0) >= max_per_family:
                continue  # família já no limite — força diversidade
            primary = s.target_assets[0] if s.target_assets else "?"
            if max_per_asset > 0 and asset_counts.get(primary, 0) >= max_per_asset:
                continue  # ativo já no limite — força diversidade de ativo
            seen.add(fp)
            unique_pop.append(s)
            family_counts[family] = family_counts.get(family, 0) + 1
            asset_counts[primary] = asset_counts.get(primary, 0) + 1
            if len(unique_pop) >= target_size:
                break
        # Se ficou curto por cap, completa com aleatórias
        if len(unique_pop) < target_size:
            assets = config.data_assets_stocks[:10]
            while len(unique_pop) < target_size:
                s = StrategyFactory.random(assets, self._rng)
                fp = s.fingerprint()
                if fp not in seen:
                    seen.add(fp)
                    unique_pop.append(s)
        if family_counts:
            top_fam = sorted(family_counts.items(), key=lambda x: -x[1])[:3]
            log.info(f"  Diversidade pop: {top_fam} (max/família={max_per_family})")

        self.population = unique_pop[:target_size]
        self._seen_fingerprints = seen
        self.generation += 1

        # 9. Salva melhores estratégias no data lake
        for s in elite[:5]:
            self.storage.save_strategy(s.strategy_id, s.to_dict())

        # Só depois de backtest, seleção e validação: um BSOD no meio do ciclo
        # volta ao último estado consistente, nunca a uma mistura parcial.
        self.save_checkpoint()

        elapsed = time.time() - t_start
        best = elite[0] if elite else None
        result = {
            "generation": gen,
            "population_size": len(self.population),
            "viable": len(viable),
            "best_fitness": best.fitness if best else 0.0,
            "best_oos_sharpe": best.oos_sharpe if best else 0.0,
            "best_strategy_id": best.strategy_id if best else None,
            "hall_of_fame_size": len(self.hall_of_fame),
            "elapsed_seconds": elapsed,
        }

        log.info(
            f"  Gen {gen} completo — "
            f"melhor fitness={result['best_fitness']:.3f}, "
            f"Sharpe={result['best_oos_sharpe']:.2f} "
            f"({elapsed:.1f}s)"
        )

        await self.bus.publish(Event(
            type=EventType.GENERATION_COMPLETE,
            source="genetic_engine",
            payload=result,
        ))

        return result

    def _update_hall_of_fame(self, elite: List[StrategyDNA], max_size: int = 50):
        """Atualiza o arquivo somente com candidatos aprovados internamente."""
        all_candidates = [
            s for s in self.hall_of_fame + elite
            if str(s.status).lower() == "validated" and s.fitness > 0
        ]
        seen = set()
        unique = []
        family_counts: dict = {}
        asset_counts: dict = {}
        lineage_counts: dict = {}
        max_per_family = max(2, max_size // 10)  # 10% do HOF por família (ex: 5/50) — força ≥10 famílias
        # Cap por ATIVO no HOF (config-gated): impede HOF virar aposta num so ticker
        _asset_frac = getattr(config, "evolution_max_per_asset_frac", 0.0)
        max_per_asset = max(2, int(max_size * _asset_frac)) if _asset_frac > 0 else 0
        # Cap por LINHAGEM (nome completo, ex: volatility_8fdcd4): impede o HOF de
        # ser inundado por copias quase-identicas de UMA linhagem. O GA converge
        # nisso e a "diversificacao" vira ilusoria (todas sao ~a mesma estrategia).
        _max_lineage = getattr(config, "evolution_max_per_lineage", 0)

        for s in sorted(all_candidates, key=lambda x: x.fitness, reverse=True):
            fp = s.fingerprint()
            if fp in seen:
                continue
            family = s.name.rsplit("_", 1)[0] if "_" in s.name else s.name
            if family_counts.get(family, 0) >= max_per_family:
                continue  # família no limite — abre espaço para outras
            if _max_lineage > 0 and lineage_counts.get(s.name, 0) >= _max_lineage:
                continue  # linhagem no limite — força variedade real de estrategias
            primary = s.target_assets[0] if s.target_assets else "?"
            if max_per_asset > 0 and asset_counts.get(primary, 0) >= max_per_asset:
                continue  # ativo no limite — abre espaço para outros ativos
            seen.add(fp)
            unique.append(s)
            family_counts[family] = family_counts.get(family, 0) + 1
            lineage_counts[s.name] = lineage_counts.get(s.name, 0) + 1
            asset_counts[primary] = asset_counts.get(primary, 0) + 1

        # Completa se HOF ficou pequeno — MAS mantem o cap por ATIVO (hard),
        # pra nao re-encher de um unico ticker (o overflow antigo ignorava os
        # caps e desfazia a diversidade). HOF pode ficar < max_size se a
        # diversidade real de ativos for limitada — comportamento correto.
        if len(unique) < max_size:
            for s in sorted(all_candidates, key=lambda x: x.fitness, reverse=True):
                if len(unique) >= max_size:
                    break
                fp = s.fingerprint()
                if fp in seen:
                    continue
                if _max_lineage > 0 and lineage_counts.get(s.name, 0) >= _max_lineage:
                    continue  # mantem o cap por linhagem tambem no overflow
                primary = s.target_assets[0] if s.target_assets else "?"
                if max_per_asset > 0 and asset_counts.get(primary, 0) >= max_per_asset:
                    continue
                seen.add(fp)
                unique.append(s)
                lineage_counts[s.name] = lineage_counts.get(s.name, 0) + 1
                asset_counts[primary] = asset_counts.get(primary, 0) + 1

        self.hall_of_fame = unique[:max_size]
        if family_counts:
            log.debug(f"HOF famílias: {dict(sorted(family_counts.items(), key=lambda x: -x[1])[:4])}")


    async def start(self):
        """Inicializa o engine."""
        self.restore_checkpoint()
        # Carrega em executor — evita bloquear o event loop se DuckDB estiver travado
        loop = asyncio.get_running_loop()
        try:
            strats_df = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self.storage.get_validated_strategies(min_sharpe=0.0)
                ),
                timeout=8.0,
            )

            if len(strats_df) > 10:
                log.info(f"Carregando {len(strats_df)} estratégias do data lake")
        except (asyncio.TimeoutError, Exception):
            pass
        log.info("GeneticEngine iniciado")

    async def stop(self):
        log.info("GeneticEngine parado")
