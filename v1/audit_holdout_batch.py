#!/usr/bin/env python3
"""
QUANT HPC OS — Re-auditoria no Held-out Embargado
==================================================
Roda cada estrategia VALIDADA (sobreviventes do gate atual, status='validated')
na ZONA DE AUDITORIA — os ultimos `embargo_fraction` do historico que NUNCA
foram tocados em selecao (GA) nem validacao (walk-forward).

Para cada estrategia compara:
    research_sharpe  = Sharpe na janela de pesquisa (primeiros 80%)
    holdout_sharpe   = Sharpe no held-out embargado (ultimos 20%, virgem)
    degradation      = holdout_sharpe / research_sharpe
e decide SOBREVIVE / OVERFIT.

REGRA DE USO UNICO (disciplina anti-vazamento):
    Este resultado e VEREDITO FINAL. Use SOMENTE para descartar estrategias
    comprovadamente overfit. NAO use para retunar thresholds nem para "auditar
    ate passar" — no instante em que voce ajustar algo com base nisto, o
    held-out deixa de ser held-out e o vazamento volta pela porta dos fundos.

PRE-REQUISITO: PARE o sistema antes de rodar (libera o lock do DuckDB).
    Se a base estiver travada, o StorageEngine cai para :memory: (base vazia)
    e o script aborta avisando.

USO:
    python audit_holdout_batch.py
    python audit_holdout_batch.py --min-sharpe 0.5 --max-degradation 0.4 --out audit_report.csv
"""
from __future__ import annotations
import argparse
import json
import os
import sys

# Garante que o root do projeto esta no sys.path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from core.config import config
from core.embargo import split_embargo
from data.storage import StorageEngine
from evolution.strategy_dna import StrategyDNA
from backtest.engine import BacktestEngine


def _load_asset_data(storage: StorageEngine, strategy: StrategyDNA):
    """Replica a logica de _load_data do validator: primeiro asset com dados."""
    targets = list(getattr(strategy, "target_assets", []) or []) + ["SPY"]
    for asset in targets:
        df = storage.load_features(asset)
        if df is not None and len(df) >= 150:
            if "close" in df.columns:
                return asset, df, df["close"]
            raw = storage.load_raw(asset)
            if raw is not None:
                return asset, df, raw["close"].reindex(df.index, method="ffill")
    return None, None, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-auditoria no held-out embargado")
    ap.add_argument("--min-sharpe", type=float, default=config.validation_min_oos_sharpe,
                    help="Sharpe minimo no holdout para sobreviver (default: validation_min_oos_sharpe)")
    ap.add_argument("--max-degradation", type=float,
                    default=getattr(config, "validation_wf_max_degradation", 0.4),
                    help="holdout/research minimo para sobreviver (default: validation_wf_max_degradation)")
    ap.add_argument("--min-trades", type=int, default=config.backtest_min_trades,
                    help="minimo de trades no holdout para significancia")
    ap.add_argument("--out", type=str, default="audit_report.csv", help="CSV de saida")
    args = ap.parse_args()

    storage = StorageEngine()

    # Sanidade: detecta fallback para :memory: (base travada pelo sistema vivo)
    try:
        catalog_n = storage.query("SELECT COUNT(*) AS n FROM strategy_catalog")["n"].iloc[0]
    except Exception as e:
        print(f"[ERRO] Nao consegui consultar strategy_catalog: {e}")
        print("       O sistema provavelmente esta rodando e travando o DuckDB.")
        print("       PARE o sistema e rode de novo.")
        return 2

    # Auditoria de holdout olha o histórico INTEIRO de propósito: ela existe
    # para julgar vereditos passados, inclusive os de épocas já encerradas.
    # É o único chamador legítimo de any_epoch — nunca use isto para capital.
    df = storage.get_validated_strategies(min_sharpe=0.0, any_epoch=True)
    if len(df) == 0:
        print("[ABORTADO] Zero estrategias 'validated' encontradas.")
        print(f"           strategy_catalog total = {catalog_n}.")
        if catalog_n == 0:
            print("           Catalogo vazio => DuckDB caiu para :memory: (base travada).")
            print("           PARE o sistema (libera o lock) e rode novamente.")
        return 2

    print(f"Estrategias validadas no catalogo: {len(df)}")
    print(f"Criterio de sobrevivencia: holdout_sharpe >= {args.min_sharpe} "
          f"AND degradation >= {args.max_degradation} AND holdout_trades >= {args.min_trades}")
    print(f"embargo_fraction = {config.embargo_fraction} "
          f"(research = primeiros {100*(1-config.embargo_fraction):.0f}%, "
          f"holdout = ultimos {100*config.embargo_fraction:.0f}%)\n")

    engine = BacktestEngine()
    rows = []
    for _, rec in df.iterrows():
        sid = rec["strategy_id"]
        try:
            meta = json.loads(rec["metadata"]) if rec.get("metadata") else {}
            strat = StrategyDNA.from_dict(meta)
        except Exception as e:
            rows.append(dict(strategy_id=sid, name=rec.get("name", ""), status="ERRO_DNA",
                             detail=str(e)))
            continue

        asset, feat, px = _load_asset_data(storage, strat)
        if feat is None:
            rows.append(dict(strategy_id=sid, name=getattr(strat, "name", ""),
                             status="SEM_DADOS"))
            continue

        (rf, rp), (af, ap_) = split_embargo(feat, px)
        if len(af) < 30:
            rows.append(dict(strategy_id=sid, name=getattr(strat, "name", ""),
                             status="HOLDOUT_CURTO", holdout_rows=len(af)))
            continue

        res_r = engine.run(strat, rf, rp)
        res_h = engine.run(strat, af, ap_)
        research_sharpe = float(res_r.sharpe) if not np.isnan(res_r.sharpe) else 0.0
        holdout_sharpe = float(res_h.sharpe) if not np.isnan(res_h.sharpe) else 0.0
        degradation = holdout_sharpe / research_sharpe if research_sharpe > 1e-9 else (
            1.0 if holdout_sharpe >= research_sharpe else 0.0)

        survive = (holdout_sharpe >= args.min_sharpe
                   and degradation >= args.max_degradation
                   and res_h.n_trades >= args.min_trades)

        rows.append(dict(
            strategy_id=sid,
            name=getattr(strat, "name", ""),
            asset=asset,
            oos_sharpe_stored=round(float(rec.get("oos_sharpe", 0) or 0), 3),
            research_sharpe=round(research_sharpe, 3),
            holdout_sharpe=round(holdout_sharpe, 3),
            degradation=round(degradation, 3),
            holdout_trades=int(res_h.n_trades),
            holdout_max_dd=round(float(res_h.max_drawdown), 3),
            status="SOBREVIVE" if survive else "OVERFIT",
        ))

    out = pd.DataFrame(rows)
    # ordena: sobreviventes primeiro, depois por degradacao desc
    order = {"SOBREVIVE": 0, "OVERFIT": 1}
    out["_o"] = out["status"].map(lambda s: order.get(s, 2))
    out = out.sort_values(["_o", "degradation"], ascending=[True, False]).drop(columns="_o")

    cols = [c for c in ["strategy_id", "name", "asset", "oos_sharpe_stored",
                        "research_sharpe", "holdout_sharpe", "degradation",
                        "holdout_trades", "holdout_max_dd", "status"] if c in out.columns]
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(out[cols].to_string(index=False))

    out.to_csv(args.out, index=False)

    n_surv = int((out["status"] == "SOBREVIVE").sum())
    n_over = int((out["status"] == "OVERFIT").sum())
    n_err = len(out) - n_surv - n_over
    print("\n" + "=" * 60)
    print(f"VEREDITO: {n_surv} SOBREVIVEM | {n_over} OVERFIT | {n_err} sem-dados/erro")
    print(f"de {len(out)} estrategias validadas auditadas.")
    print(f"Relatorio salvo em: {args.out}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
