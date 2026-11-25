"""
QUANT HPC OS — Held-out Embargado
Separa o histórico em janela de PESQUISA (primeiros 1-embargo) e janela de
AUDITORIA (últimos `embargo_fraction`). A zona de auditoria é proibida durante
seleção (GA) e validação (walk-forward) — existe apenas para inspeção final
honesta de generalização, em dados que o sistema nunca tocou ao decidir.
"""
from __future__ import annotations
from core.config import config


def research_cut(n: int, embargo=None) -> int:
    """Índice de corte: research = [0, cut), audit = [cut, n)."""
    emb = config.embargo_fraction if embargo is None else embargo
    return int(n * (1.0 - emb))


def split_embargo(features, prices, embargo=None):
    """
    Retorna ((research_features, research_prices), (audit_features, audit_prices)).
    research → permitido para GA e validação.
    audit    → proibido; apenas auditoria final.
    """
    cut = research_cut(len(features), embargo)
    research = (features.iloc[:cut], prices.iloc[:cut])
    audit = (features.iloc[cut:], prices.iloc[cut:])
    return research, audit
