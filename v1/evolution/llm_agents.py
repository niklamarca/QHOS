"""
QUANT HPC OS — Multi-Agent LLM Pool (Claude API)
4 agentes especializados com papéis distintos no processo evolutivo:
  1. Alpha Agent    — gera ideias de estratégias inovadoras
  2. Risk Agent     — avalia e reduz riscos das estratégias
  3. Overfitting Agent — detecta e elimina overfitting
  4. Microstructure Agent — analisa impacto de microestrutura
"""
from __future__ import annotations
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING
import anthropic
from core.config import config
from core.logger import get_logger
from .strategy_dna import StrategyDNA, TradingRule, ExitRule, RuleType, SignalType

log = get_logger("LLMAgents")


EPISTEMIC_CONTRACT = """
CONTRATO EPISTEMICO OBRIGATORIO:
- Voce sugere hipoteses; nao certifica, valida, prova nem autoriza execucao.
- Nomes de fatores academicos sao priors, nao evidencia neste dataset.
- Separe fato observado, inferencia, hipotese e limitacao.
- Nao invente dados, custos, microestrutura, fills, fontes ou resultados ausentes.
- Dados diarios OHLCV publicos nao observam fila, spread historico ou impacto real.
- Toda conclusao depende do CURRENT_STATE fornecido e deve declarar limitacoes.
"""

ALPHA_AGENT_SYSTEM = EPISTEMIC_CONTRACT + """\nVocê é um pesquisador quantitativo que gera candidatos.
Sua tarefa é sugerir hipóteses testáveis inspiradas em literatura acadêmica.
Considere momentum, value, quality, low volatility, carry e mean reversion como
hipoteses conhecidas, nunca como fatores comprovados nos dados locais.
Seja específico sobre as features a usar, os thresholds e a lógica de entrada/saída.
Responda APENAS em JSON válido conforme o schema solicitado."""

RISK_AGENT_SYSTEM = EPISTEMIC_CONTRACT + """\nVocê revisa risco quantitativo.
Analise estratégias de trading e identifique riscos ocultos:
- Tail risk e eventos extremos
- Correlação com fatores conhecidos (beta, tamanho, valor)
- Liquidez e impacto de mercado
- Dependência de regime não identificada
- Concentration risk
Sugira ajustes conservadores. Responda APENAS em JSON válido."""

OVERFITTING_AGENT_SYSTEM = EPISTEMIC_CONTRACT + """\nVocê revisa risco de overfitting.
Identifique sinais de overfitting:
- Número excessivo de parâmetros livres
- Sharpe IS/OOS divergente
- Dependência de período histórico específico
- Múltiplas comparações sem correção estatística
- Complexidade desnecessária (Occam's Razor)
Sugira simplificações. Responda APENAS em JSON válido."""

MICROSTRUCTURE_AGENT_SYSTEM = EPISTEMIC_CONTRACT + """\nVocê revisa hipoteses de custos de execução.
Analise o impacto de:
- Bid-ask spread nos retornos líquidos
- Market impact e slippage
- Timing de execução (open vs close vs intraday)
- Custos de transação reais
- Gaps overnight
Estime custos realistas e sugira ajustes. Responda APENAS em JSON válido."""


class LLMAgent:
    """Agente LLM individual com papel especializado."""

    def __init__(self, name: str, system_prompt: str, model: str):
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self._client = None
        # Um canal de geração morto precisa ser declarável: sem estes campos o
        # sistema tentou 366 vezes em 65 dias e reportou diagnóstico de pesquisa
        # sem dizer que metade da geração de hipóteses estava inoperante.
        self.consecutive_failures = 0
        self.last_error_class: Optional[str] = None
        self.disabled_reason: Optional[str] = None
        # Falta de crédito é condição TRANSITÓRIA (uma recarga resolve). Antes,
        # o agente ficava morto até o restart do servidor — recarga sem efeito.
        # Agora uma sonda a cada _RETRY_AFTER_S tenta reativar sozinha.
        self._disabled_at: Optional[float] = None
        self._enabled = config.anthropic_api_key not in ("", "disabled", None)
        if not self._enabled:
            self.disabled_reason = "no_api_key"
        if self._enabled:
            try:
                self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)
            except Exception as e:
                log.warning(f"Anthropic client não inicializado: {e}")
                self._enabled = False
                self.disabled_reason = f"client_init_failed:{type(e).__name__}"

    def health(self) -> Dict:
        """Estado factual do canal, sem inferir qualidade das sugestões."""
        return {
            "enabled": bool(self._enabled),
            "disabled_reason": self.disabled_reason,
            "consecutive_failures": int(self.consecutive_failures),
            "last_error_class": self.last_error_class,
        }

    async def query(self, user_message: str, max_tokens: int = 2000) -> str:
        """Envia query para o agente LLM."""
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            self._query_sync,
            user_message,
            max_tokens,
        )
        return response

    _RETRY_AFTER_S = 3600.0  # crédito é transitório: sonda de reativação por hora

    def _maybe_reenable(self) -> None:
        """Reativa o canal para UMA tentativa após o cooldown.

        Só para ``api_credit_unavailable``: chave ausente ou client quebrado
        não se resolvem sozinhos. Se o crédito continuar esgotado, a própria
        chamada falha e desativa de novo — custo de uma sonda por hora.
        """
        if (self.disabled_reason == "api_credit_unavailable"
                and self._client is not None
                and self._disabled_at is not None
                and time.time() - self._disabled_at > self._RETRY_AFTER_S):
            self._enabled = True
            self.disabled_reason = None
            self._disabled_at = None
            log.info(f"Agente {self.name}: sonda de reativacao apos recarga de credito")

    def _query_sync(self, user_message: str, max_tokens: int) -> str:
        if not self._enabled:
            self._maybe_reenable()
        if not self._enabled or self._client is None:
            return "{}"
        try:
            try:
                from governance.epistemics import load_current_state
                truth = load_current_state()
                factual = {
                    "schema": truth.get("schema"), "health": truth.get("health"),
                    "data": truth.get("data"), "research": truth.get("research"),
                    "execution": truth.get("execution"), "evidence": truth.get("evidence"),
                    "versions": truth.get("versions"),
                }
                user_message = ("CURRENT_STATE (fonte factual; nao extrapole):\n"
                                + json.dumps(factual, ensure_ascii=False, default=str)
                                + "\n\nTAREFA:\n" + user_message)
            except Exception:
                pass
            message = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=self.system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            self.consecutive_failures = 0
            self.last_error_class = None
            return message.content[0].text
        except Exception as e:
            self.consecutive_failures += 1
            self.last_error_class = type(e).__name__
            log.error(f"Erro no agente {self.name}: {e}")
            if any(token in str(e).lower() for token in ("credit balance", "billing", "insufficient_credit")):
                self._enabled = False
                self.disabled_reason = "api_credit_unavailable"
                self._disabled_at = time.time()
                log.warning(f"Agente {self.name} desativado: credito de API indisponivel "
                            f"(sonda de reativacao em {self._RETRY_AFTER_S/3600:.0f}h)")
            return "{}"

    async def query_json(self, user_message: str, max_tokens: int = 2000) -> Dict:
        """Retorna resposta parseada como JSON (tolerante a truncamento)."""
        raw = await self.query(user_message, max_tokens=max_tokens)
        parsed = self._loads_json_lenient(raw)
        if parsed is not None:
            return parsed
        log.warning(f"Agente {self.name} retornou JSON inválido: {raw[:200]!r}")
        self.consecutive_failures += 1
        self.last_error_class = "InvalidJSON"
        return {}

    @staticmethod
    def _loads_json_lenient(raw: str) -> Optional[Dict]:
        """Extrai o maior objeto JSON recuperável da resposta do modelo."""
        if not raw:
            return None
        text = raw.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()
        start = text.find("{")
        if start < 0:
            return None
        blob = text[start:]
        decoder = json.JSONDecoder()

        def _try(payload: str) -> Optional[Dict]:
            try:
                obj, _ = decoder.raw_decode(payload)
            except json.JSONDecodeError:
                return None
            return obj if isinstance(obj, dict) else None

        direct = _try(blob)
        if direct is not None:
            return direct

        repaired = blob.rstrip().rstrip(",")
        if repaired.count('"') % 2 == 1:
            repaired += '"'
        # Fecha o maior prefixo parseável (descarta o objeto cortado no fim).
        for cut in range(len(repaired), 0, -1):
            if repaired[cut - 1] not in "}]":
                continue
            chunk = repaired[:cut]
            open_curly = chunk.count("{") - chunk.count("}")
            open_brack = chunk.count("[") - chunk.count("]")
            if open_curly < 0 or open_brack < 0:
                continue
            candidate = chunk + ("]" * open_brack) + ("}" * open_curly)
            parsed = _try(candidate)
            if parsed is not None:
                return parsed
        return None


class LLMAgentPool:
    """
    Pool de agentes LLM especializados.
    Coordena o ciclo de análise multi-perspectiva de estratégias.
    """

    def __init__(self):
        self.alpha_agent = LLMAgent(
            "alpha",
            ALPHA_AGENT_SYSTEM,
            config.llm_model_alpha,
        )
        self.risk_agent = LLMAgent(
            "risk",
            RISK_AGENT_SYSTEM,
            config.llm_model_risk,
        )
        self.overfitting_agent = LLMAgent(
            "overfitting",
            OVERFITTING_AGENT_SYSTEM,
            config.llm_model_overfitting,
        )
        self.microstructure_agent = LLMAgent(
            "microstructure",
            MICROSTRUCTURE_AGENT_SYSTEM,
            config.llm_model_microstructure,
        )

    def _agents(self) -> Dict[str, LLMAgent]:
        return {"alpha": self.alpha_agent, "risk": self.risk_agent,
                "overfitting": self.overfitting_agent,
                "microstructure": self.microstructure_agent}

    def status(self) -> Dict:
        """Saúde dos canais de geração/revisão, para o estado autoritativo."""
        return {"ts": time.time(),
                "agents": {name: agent.health()
                           for name, agent in self._agents().items()}}

    def publish_status(self) -> None:
        """Grava a saúde em disco: o CURRENT_STATE é construído a partir de
        arquivos, não do processo, e precisa enxergar canal inoperante."""
        try:
            path = Path(config.data_lake_dir) / "llm_agents_state.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self.status(), ensure_ascii=False,
                                            indent=2), encoding="utf-8")
            os.replace(temporary, path)
        except Exception as exc:
            log.debug(f"status dos agentes não publicado: {exc}")

    async def generate_strategies(
        self,
        top_strategies: List[StrategyDNA],
        n_strategies: int = 3,
    ) -> List[StrategyDNA]:
        """Usa o Alpha Agent para gerar novas ideias de estratégias."""
        if not config.anthropic_api_key:
            log.debug("ANTHROPIC_API_KEY não configurada — pulando LLM agents")
            self.publish_status()
            return []

        top_desc = []
        for s in top_strategies[:3]:
            top_desc.append({
                "strategy_id": s.strategy_id,
                "rule_type": s.rule_type.value,
                "fitness": round(s.fitness, 3),
                "oos_sharpe": round(s.oos_sharpe, 2),
                "n_rules": len(s.entry_rules),
                "rules_summary": [
                    f"{r.feature} {r.operator} {r.threshold:.2f}"
                    for r in s.entry_rules[:2]
                ],
            })

        prompt = f"""Os candidatos mais bem ranqueados antes da decisao final sao:
{json.dumps(top_desc, indent=2)}

Gere {n_strategies} novas ideias de estratégias de trading que sejam DIFERENTES das existentes
mas baseadas nos mesmos princípios que funcionaram.

Responda SOMENTE com JSON valido (sem markdown). rationale <= 120 caracteres.
Para cada estratégia, use este schema:
{{
  "strategies": [
    {{
      "name": "nome descritivo",
      "rule_type": "momentum|mean_revert|breakout|volatility|volume|multi_factor",
      "signal_type": "long_short|long_only|strength",
      "rationale": "mecanismo hipotetico e falsificavel",
      "epistemic_status": "HYPOTHESIS",
      "assumptions": [],
      "limitations": [],
      "entry_rules": [
        {{
          "feature": "nome_da_feature",
          "operator": ">|<|>|<|rolling_z|percentile_rank",
          "threshold": 0.0,
          "weight": 1.0
        }}
      ],
      "stop_loss_pct": 0.02,
      "take_profit_pct": 0.05,
      "max_holding_days": 20,
      "position_size": 0.05,
      "target_assets": ["SPY"]
    }}
  ]
}}

Features disponíveis: mom_5, mom_21, mom_63, rsi_14, macd_hist, z_score_20,
bb_pct_20, vol_20, vol_regime, hurst_approx, trend_5_20, obv_trend, stoch_k,
atr_pct_14, autocorr_1, vwap_dist, trend_20_50, price_to_sma_50.

IMPORTANTE: use APENAS os nomes de feature acima, exatamente como escritos."""

        try:
            result = await self.alpha_agent.query_json(prompt, max_tokens=3000)
            strategies_data = result.get("strategies", [])

            generated = []
            for sd in strategies_data:
                try:
                    dna = self._dict_to_dna(sd)
                    generated.append(dna)
                except Exception as e:
                    log.debug(f"Estratégia LLM inválida: {e}")

            log.info(f"Alpha Agent gerou {len(generated)} estratégias")
            return generated

        except Exception as e:
            log.error(f"Erro ao gerar estratégias com LLM: {e}")
            return []
        finally:
            self.publish_status()

    async def analyze_strategy(self, strategy: StrategyDNA) -> Dict:
        """Análise multi-agente completa de uma estratégia."""
        if not config.anthropic_api_key:
            return {"skipped": True, "reason": "no_api_key"}

        strategy_desc = json.dumps({
            "name": strategy.name,
            "rule_type": strategy.rule_type.value,
            "entry_rules": [r.to_dict() for r in strategy.entry_rules],
            "stop_loss_pct": strategy.exit_rule.stop_loss_pct,
            "take_profit_pct": strategy.exit_rule.take_profit_pct,
            "fitness": strategy.fitness,
            "oos_sharpe": strategy.oos_sharpe,
            "max_drawdown": strategy.max_drawdown,
            "n_trades": strategy.n_trades,
        }, indent=2)

        # Executa 3 agentes em paralelo
        results = await asyncio.gather(
            self.risk_agent.query_json(
                f"Analise os riscos desta estratégia:\n{strategy_desc}\n\n"
                "Retorne JSON: {\"risk_score\": 0-10, \"concerns\": [], \"suggestions\": []}"
            ),
            self.overfitting_agent.query_json(
                f"Avalie o risco de overfitting:\n{strategy_desc}\n\n"
                "Retorne JSON: {\"overfit_risk\": 0-10, \"reasons\": [], \"simplifications\": []}"
            ),
            self.microstructure_agent.query_json(
                f"Avalie o impacto de microestrutura:\n{strategy_desc}\n\n"
                "Retorne JSON: {\"estimated_slippage_bps\": 5, \"net_sharpe_estimate\": 0.5, \"concerns\": []}"
            ),
            return_exceptions=True,
        )

        risk_result = results[0] if not isinstance(results[0], Exception) else {}
        overfit_result = results[1] if not isinstance(results[1], Exception) else {}
        micro_result = results[2] if not isinstance(results[2], Exception) else {}

        analysis = {
            "strategy_id": strategy.strategy_id,
            "risk": risk_result,
            "overfitting": overfit_result,
            "microstructure": micro_result,
            "overall_recommendation": self._synthesize_recommendation(
                risk_result, overfit_result, micro_result, strategy
            ),
        }

        log.info(
            f"Análise LLM: {strategy.name} — "
            f"risk={risk_result.get('risk_score', '?')}, "
            f"overfit={overfit_result.get('overfit_risk', '?')}"
        )
        return analysis

    def _synthesize_recommendation(
        self,
        risk: Dict,
        overfit: Dict,
        micro: Dict,
        strategy: StrategyDNA,
    ) -> str:
        """Sintetiza recomendação final baseada nas análises dos agentes."""
        risk_score = risk.get("risk_score", 5)
        overfit_risk = overfit.get("overfit_risk", 5)
        net_sharpe = micro.get("net_sharpe_estimate", strategy.oos_sharpe)

        if overfit_risk > 8:
            return "reject_overfitting"
        if risk_score > 8:
            return "reject_risk"
        if net_sharpe < config.validation_min_oos_sharpe:
            return "reject_costs"
        if risk_score < 5 and overfit_risk < 5 and net_sharpe > 1.0:
            return "suggest_priority_review"
        return "suggest_conditional_review"

    def _dict_to_dna(self, d: Dict) -> StrategyDNA:
        """Converte dict do LLM em StrategyDNA."""
        rule_type = RuleType(d.get("rule_type", "momentum"))
        signal_type = SignalType(d.get("signal_type", "long_short"))

        entry_rules = []
        for r in d.get("entry_rules", []):
            entry_rules.append(TradingRule(
                rule_type=rule_type,
                feature=r.get("feature", "mom_21"),
                operator=r.get("operator", ">"),
                threshold=float(r.get("threshold", 0.0)),
                weight=float(r.get("weight", 1.0)),
            ))

        if not entry_rules:
            raise ValueError("Estratégia sem regras de entrada")

        exit_rule = ExitRule(
            stop_loss_pct=float(d.get("stop_loss_pct", 0.02)),
            take_profit_pct=float(d.get("take_profit_pct", 0.05)),
            max_holding_days=int(d.get("max_holding_days", 20)),
        )

        targets = d.get("target_assets") or ["SPY"]
        if not isinstance(targets, list):
            targets = [targets]
        return StrategyDNA(
            name=d.get("name", "llm_generated"),
            rule_type=rule_type,
            signal_type=signal_type,
            entry_rules=entry_rules,
            exit_rule=exit_rule,
            target_assets=[str(targets[0])],
            position_size=float(d.get("position_size", 0.05)),
        )

    async def start(self):
        self.publish_status()
        if not self.alpha_agent._enabled:
            # WARNING, não INFO: com o canal alpha desligado, a geração de
            # hipóteses roda com uma fonte a menos e o diagnóstico de pesquisa
            # precisa dizer isso em voz alta.
            log.warning(
                "LLMAgentPool: canal ALPHA inoperante (%s) — geração de hipóteses "
                "roda somente pelo motor genético. Declarado em research_blockers.",
                self.alpha_agent.disabled_reason or "desconhecido",
            )
        else:
            log.info("LLMAgentPool iniciado (canal alpha ativo)")

    async def stop(self):
        log.info("LLMAgentPool encerrado")
