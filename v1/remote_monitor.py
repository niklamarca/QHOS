#!/usr/bin/env python3
"""
QUANT HPC OS — Monitor Remoto (Discord)
=======================================
Posta no seu Discord um snapshot HONESTO do sistema para voce checar a
veracidade de longe (viagem). Le APENAS artefatos em disco (nav.csv,
live_performance.json) — nao abre o DuckDB travado, nao toca no sistema.

Filosofia (a mesma, inegociavel): mede paper-forward; nunca declara
"lucrativo"; compara com o SPY; e o mais importante para quem esta longe —
funciona como HEARTBEAT: se as mensagens PARAM de chegar, o PC caiu (queda de
luz). E se o PC esta ligado mas o nav.csv congelou, avisa que travou.

USO:
  python remote_monitor.py --once      # envia UM snapshot e sai (ideal p/ agendador)
  python remote_monitor.py             # loop: heartbeat a cada N horas + digest diario

CONFIG (variaveis de ambiente / .env):
  DISCORD_WEBHOOK_URL   URL do webhook (SEGREDO — nunca no codigo)
  HEARTBEAT_INTERVAL_H  intervalo do heartbeat em horas (default 6)
  DIGEST_HOUR           hora local do digest diario 0-23 (default 8)
  STALL_HOURS           horas sem atualizar nav.csv p/ alertar travamento (default 26)
  LIVENESS_STALL_MIN    min sem heartbeat do servidor p/ declarar CAIDO (default 3)
  LIVENESS_POLL_SEC     intervalo de verificacao da vigia em segundos (default 30)
"""
from __future__ import annotations
import argparse
import json
import math
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from monitoring.discord_notifier import send, make_embed, field, _read_env_file

ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(ROOT, "data_lake", "live_performance.json")
NAV = os.path.join(ROOT, "data_lake", "track_record", "nav.csv")
STATE = os.path.join(ROOT, "data_lake", "remote_monitor_state.json")
LOCK = os.path.join(ROOT, "data_lake", "remote_monitor.lock")
HEARTBEAT_FILE = os.path.join(ROOT, "data_lake", "server_heartbeat.txt")
CURRENT_STATE = os.path.join(ROOT, "data_lake", "governance", "CURRENT_STATE.json")
ACTIVATION = 10
PPY = 252

COLOR_RED = 0xE03131
COLOR_YELLOW = 0xF1C40F
COLOR_GREEN = 0x2FB344
COLOR_GRAY = 0x868E96

def _cfg(key: str, default: str) -> str:
    """Le uma config: variavel de ambiente primeiro, depois o .env, senao default."""
    v = (os.environ.get(key, "") or "").strip()
    if v:
        return v
    v = _read_env_file(key)
    return v if v else default



def _sharpe(a):
    a = np.asarray(a, float)
    return float(a.mean() / (a.std() + 1e-9) * math.sqrt(PPY)) if len(a) > 1 and a.std() > 0 else 0.0


def _segments(df):
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    segs, cur = [], [0]
    for i in range(1, len(df)):
        reset = df["n_fills_cum"].iloc[i] < df["n_fills_cum"].iloc[i - 1]
        gap = (df["date"].iloc[i] - df["date"].iloc[i - 1]).days > 4
        if reset or gap:
            segs.append(cur); cur = [i]
        else:
            cur.append(i)
    segs.append(cur)
    return df, segs


def _server_liveness():
    """Idade em segundos do heartbeat do servidor. None se o arquivo nao existe."""
    try:
        if not os.path.exists(HEARTBEAT_FILE):
            return None
        try:
            beat = float(open(HEARTBEAT_FILE).read().strip())
        except Exception:
            beat = os.path.getmtime(HEARTBEAT_FILE)
        return max(0.0, time.time() - beat)
    except Exception:
        return None


def _srv_str(snap) -> str:
    up = snap.get("server_up")
    age = snap.get("hb_age_sec")
    if up is True:
        return f"\U0001F7E2 no ar (beat {int(age)}s)" if age is not None else "\U0001F7E2 no ar"
    if up is False:
        return f"\U0001F534 CAIDO (sem beat ha {int(age)}s)" if age is not None else "\U0001F534 CAIDO"
    return "\u2754 desconhecido (sem heartbeat)"


def compute_snapshot() -> dict:
    """Le os artefatos e devolve um dict com a verdade forward. Sem rede."""
    snap = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "has_nav": os.path.exists(NAV),
        "nav_now": None, "seg_ret": None, "seg_days": 0, "seg_start": None,
        "spy_ret": None, "excess": None, "n_boots": 0,
        "fb_total": 0, "fb_active": 0, "fb_recs": 0,
        "churn_last": None, "nav_age_h": None, "stale": False,
        "trend_ret": None, "symb1_ret": None, "trend_days": 0, "symb1_days": 0,
        "hb_age_sec": None, "server_up": None,
        "verdict": "sem dados", "emoji": "🟡", "color": COLOR_GRAY,
        "truth_health": {}, "evidence_status": "NO_EVIDENCE",
    }

    try:
        truth = json.load(open(CURRENT_STATE, encoding="utf-8"))
        snap["truth_health"] = truth.get("health", {})
        snap["evidence_status"] = truth.get("evidence", {}).get("status", "NO_EVIDENCE")
    except Exception:
        pass

    # ---- liveness do servidor (heartbeat frequente ~30s) ----
    hb_age = _server_liveness()
    snap["hb_age_sec"] = hb_age
    stall_min = float(_cfg("LIVENESS_STALL_MIN", "3"))
    if hb_age is not None:
        snap["server_up"] = hb_age <= stall_min * 60
        if snap["server_up"] is False:
            snap["verdict"] = "SERVIDOR CAIDO — sem heartbeat"
            snap["emoji"] = "🔴"
            snap["color"] = COLOR_RED

    # ---- bracos paper (TREND / SYMB-1) ----
    for _nm, _f in (("trend", "trend_nav.csv"), ("symb1", "symb1_nav.csv")):
        try:
            _pth = os.path.join(ROOT, "data_lake", "track_record", _f)
            if os.path.exists(_pth):
                _d = pd.read_csv(_pth)
                if not _d.empty:
                    snap[_nm + "_ret"] = float(_d["nav"].iloc[-1]) / 100000.0 - 1.0
                    snap[_nm + "_days"] = len(_d)
        except Exception:
            pass

    # ---- feedback ----
    if os.path.exists(LIVE):
        try:
            d = json.load(open(LIVE))
            snap["fb_total"] = len(d)
            snap["fb_active"] = sum(1 for v in d.values() if len(v) >= ACTIVATION)
            snap["fb_recs"] = sum(len(v) for v in d.values())
        except Exception:
            pass

    if not snap["has_nav"]:
        return snap

    try:
        df = pd.read_csv(NAV)
    except Exception:
        return snap
    if df.empty:
        return snap

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    from core.market_clock import is_trading_day
    df = df[df["date"].notna() & df["date"].dt.date.map(is_trading_day)]
    df = df.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    if df.empty:
        return snap
    last_seg = df
    snap["nav_now"] = float(df["nav"].iloc[-1])
    # retorno do SEGMENTO CONTINUO atual (nao soma pedacos separados)
    snap["seg_ret"] = float((last_seg["nav"].iloc[-1] / 100000 - 1) * 100)
    snap["seg_days"] = int(len(last_seg))
    snap["seg_start"] = str(last_seg["date"].iloc[0].date())

    # vs SPY no segmento atual
    sp = last_seg.dropna(subset=["spy_close"])
    if len(sp) > 1 and sp["spy_close"].iloc[0] > 0:
        spy_ret = (sp["spy_close"].iloc[-1] / sp["spy_close"].iloc[0] - 1) * 100
        snap["spy_ret"] = float(spy_ret)
        snap["excess"] = float(snap["seg_ret"] - spy_ret)

    # churn: fills no ultimo dia (diff de n_fills_cum vs ~1 dia atras)
    try:
        last_date = df["date"].iloc[-1]
        prev = df[df["date"] <= last_date - pd.Timedelta(days=1)]
        if len(prev):
            snap["churn_last"] = int(df["n_fills_cum"].iloc[-1] - prev["n_fills_cum"].iloc[-1])
    except Exception:
        pass

    # frescor / travamento: idade do arquivo nav.csv
    try:
        age_h = (time.time() - os.path.getmtime(NAV)) / 3600.0
        snap["nav_age_h"] = round(age_h, 1)
        stall_h = float(_cfg("STALL_HOURS", "26"))
        snap["stale"] = age_h > stall_h
    except Exception:
        pass

    # ---- veredito: liveness primeiro (mais critico), depois forward ----
    r = snap["seg_ret"]
    if snap["server_up"] is False:
        pass  # ja marcado como SERVIDOR CAIDO
    elif snap["stale"]:
        snap["verdict"] = "possivel TRAVAMENTO — nav.csv parado"
        snap["emoji"] = "⚠️"; snap["color"] = COLOR_RED
    elif r is not None and r < -1.0:
        snap["verdict"] = "SANGRANDO — forward negativo"
        snap["emoji"] = "🔴"; snap["color"] = COLOR_RED
    elif r is not None and r > 0.5 and (snap["excess"] is None or snap["excess"] > 0):
        snap["verdict"] = "sinal positivo PRELIMINAR (amostra insuficiente)"
        snap["emoji"] = "🟢"; snap["color"] = COLOR_GREEN
    else:
        snap["verdict"] = "DE LADO — flat, sem edge demonstrado"
        snap["emoji"] = "🟡"; snap["color"] = COLOR_YELLOW
    return snap


def _fmt(x, suf="%", nd=2):
    return "n/d" if x is None else f"{x:+.{nd}f}{suf}"


def _bar(pct, width=10):
    """Barra visual simples: ▰▰▰▱▱ (para leitura rapida no celular)."""
    n = max(0, min(width, int(round(abs(pct) * width))))
    return ("▰" * n) + ("▱" * (width - n))


def build_message(snap: dict, mode: str = "heartbeat", extra: str = "") -> dict:
    """Mensagem escrita para ser entendida de relance, no celular, sem jargao."""
    # ---------- servidor fora do ar ----------
    if snap.get("server_up") is False:
        mins = int((snap.get("hb_age_sec") or 0) / 60)
        e = make_embed(
            "🔴 O sistema PAROU",
            f"Sem sinal de vida há **{mins} min**.\n\n"
            "**Se foi você que desligou**, pode ignorar.\n"
            "**Se não foi**, o PC travou, foi reiniciado ou faltou energia.\n\n"
            "▶️ *Para voltar: abra `QHOS_RESTART.bat` quando puder. "
            "Nada se perde — a carteira e o histórico continuam salvos.*",
            COLOR_RED)
        e["footer"] = {"text": snap["ts"]}
        return e

    if not snap["has_nav"]:
        return make_embed("⏳ Sistema ligado, ainda sem histórico",
                          "O servidor está no ar, mas ainda não gravou nenhum dia de "
                          "resultado. Normal logo após o primeiro boot.", COLOR_GRAY)

    # ---------- números ----------
    ret = snap["seg_ret"] or 0.0          # % do sistema
    spy = snap["spy_ret"]
    exc = snap["excess"]
    days = snap["seg_days"] or 0
    nav = snap["nav_now"] or 100000.0
    falta = max(0, 180 - days)

    # ---------- veredito em linguagem humana ----------
    if exc is not None and exc > 0 and ret > 0:
        titulo = "🟢 À frente do índice (amostra ainda curta)"
        resumo = (f"Em **{days} dias** o sistema rendeu **{ret:+.2f}%** e o índice "
                  f"**{spy:+.2f}%** — está **ganhando por {exc:+.2f}%**.\n"
                  "É cedo para concluir: só com meses de histórico isso vira evidência.")
        cor = COLOR_GREEN
    elif ret < -1.0:
        titulo = "🔴 Perdendo dinheiro"
        resumo = (f"Em **{days} dias** o sistema caiu **{ret:+.2f}%**. "
                  "Vale checar custo de transação, excesso de trades ou ausência de vantagem.")
        cor = COLOR_RED
    else:
        titulo = "🟡 Andando de lado — sem vantagem"
        if spy is not None:
            resumo = (f"Em **{days} dias** o sistema fez **{ret:+.2f}%** enquanto o índice "
                      f"fez **{spy:+.2f}%**.\n➡️ *Traduzindo: ter comprado S&P 500 e não "
                      "feito nada teria dado mais dinheiro.*")
        else:
            resumo = f"Em **{days} dias** o sistema está praticamente parado ({ret:+.2f}%)."
        cor = COLOR_YELLOW

    if extra:
        resumo = extra + "\n\n" + resumo

    # ---------- campos ----------
    fields = []

    comp = f"`Sistema  {ret:+6.2f}%`\n"
    if spy is not None:
        comp += f"`Índice   {spy:+6.2f}%`\n"
    if exc is not None:
        sinal = "ganhando" if exc > 0 else "perdendo"
        comp += f"`Diferença {exc:+5.2f}%` ← {sinal}"
    fields.append(field(f"📊 Resultado em {days} sessões", comp, inline=False))

    bracos = []
    if snap.get("trend_ret") is not None:
        bracos.append(f"`TREND    {snap['trend_ret']*100:+6.2f}%`  ({snap.get('trend_days',0)} sessões)")
    if snap.get("symb1_ret") is not None:
        bracos.append(f"`SYMB-1   {snap['symb1_ret']*100:+6.2f}%`  ({snap.get('symb1_days',0)} sessões)")
    if bracos:
        fields.append(field("🤖 Estratégias em teste", "\n".join(bracos), inline=False))

    fb_a, fb_t = snap["fb_active"], snap["fb_total"]
    pct = (fb_a / fb_t) if fb_t else 0
    fields.append(field("🧾 Marcações por candidato",
                        f"{_bar(pct)} **{fb_a} de {fb_t}**\n"
                        f"IDs com 10+ observações; isto não prova aprendizado"))

    health = snap.get("truth_health") or {}
    if health:
        fields.append(field("🔎 Integridade declarada",
                            f"Processo `{health.get('process','UNKNOWN')}` · "
                            f"Dados `{health.get('data_integrity','UNKNOWN')}`\n"
                            f"Pesquisa `{health.get('research_integrity','UNKNOWN')}` · "
                            f"Execução `{health.get('execution_readiness','UNKNOWN')}`\n"
                            f"Evidência `{snap.get('evidence_status','NO_EVIDENCE')}`",
                            inline=False))

    horas = "n/d" if snap["nav_age_h"] is None else f"{snap['nav_age_h']:.0f}h"
    # estado do mercado: explica POR QUE nada se moveu (fim de semana/feriado)
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from core.market_clock import describe as _mkt
        _m = _mkt()
        if _m["state"] == "POST":
            _linha = "🟢 Pregão encerrado — sistema livre para agir"
        elif _m["state"] == "OPEN":
            _linha = "🔵 Pregão aberto — sistema aguarda o fechamento da vela"
        elif _m["state"] in ("WEEKEND", "HOLIDAY"):
            _linha = f"⚪ {_m['label'].capitalize()} — nada a fazer até {_m['next_open']}"
        else:
            _linha = f"⚪ {_m['label'].capitalize()}"
    except Exception:
        _linha = ""
    fields.append(field("⚙️ Situação",
                        f"Carteira **${nav:,.0f}**\n"
                        f"Último registro há {horas}"
                        + (f"\n{_linha}" if _linha else "")))

    # ---------- o que fazer ----------
    if falta > 0:
        acao = (f"**Nada a fazer.** Faltam ~**{falta} sessões** de teste antes de "
                "qualquer conclusão valer.")
    else:
        acao = ("**Período de teste completo.** Vale rodar a avaliação formal "
                "(`consolidation_scorecard.py`) e revisar as metas pré-definidas.")
    fields.append(field("✅ Precisa de ação?", acao, inline=False))

    e = make_embed(titulo, resumo, cor, fields)
    e["footer"] = {"text": f"paper-forward · {mode} · {snap['ts'][:16].replace('T', ' ')} · "
                           "sem fill real / sem edge demonstrado"}
    return e


def _load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def _save_state(st):
    try:
        tmp = STATE + ".tmp"
        json.dump(st, open(tmp, "w"))
        os.replace(tmp, STATE)
    except Exception:
        pass


def send_snapshot(mode="heartbeat", extra=""):
    snap = compute_snapshot()
    e = build_message(snap, mode=mode, extra=extra)
    ok = send(embeds=[e])
    _t = (e.get("title") or "").strip()
    print(f"[remote_monitor] {mode}: {'enviado' if ok else 'NAO enviado (dry-run/falha)'} — {_t}")
    return snap, ok


def _boot_count_today():
    """Conta boots de hoje pelo log (linhas OPERACIONAL). Detecta restart RAPIDO
    que o heartbeat de 3min nao pega (e que a persistencia tornou invisivel no nav)."""
    try:
        import glob as _g, datetime as _dt
        day = _dt.datetime.now().strftime("%Y-%m-%d")
        hits = _g.glob(os.path.join(ROOT, "logs", f"quant_hpc_{day}.log"))
        if not hits:
            return 0
        n = 0
        with open(hits[0], "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "OPERACIONAL" in line:
                    n += 1
        return n
    except Exception:
        return -1


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return True


def _acquire_lock() -> bool:
    """Garante instancia unica. Se ja houver um monitor vivo, retorna False."""
    try:
        if os.path.exists(LOCK):
            old = int((open(LOCK).read().strip() or "0"))
            if old and old != os.getpid() and _pid_alive(old):
                print(f"[remote_monitor] ja ha uma instancia rodando (pid {old}) - saindo.")
                return False
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        open(LOCK, "w").write(str(os.getpid()))
        return True
    except Exception:
        return True


def _release_lock():
    try:
        if os.path.exists(LOCK):
            cur = int((open(LOCK).read().strip() or "0"))
            if cur == os.getpid():
                os.remove(LOCK)
    except Exception:
        pass


def loop():
    if not _acquire_lock():
        return
    import atexit
    atexit.register(_release_lock)
    interval_h = float(_cfg("HEARTBEAT_INTERVAL_H", "6"))
    digest_hour = int(_cfg("DIGEST_HOUR", "8"))
    poll_sec = float(_cfg("LIVENESS_POLL_SEC", "30"))
    print(f"[remote_monitor] vigia desacoplada: liveness a cada {poll_sec:.0f}s, "
          f"status a cada {interval_h}h, digest {digest_hour}h. Ctrl+C p/ sair.")
    st = _load_state()
    last_hb = 0.0
    while True:
        try:
            snap = compute_snapshot()
            now = time.time()

            # ---- TRANSICOES DE LIVENESS (alerta INSTANTANEO) ----
            prev = st.get("server_state")   # "up" | "down" | None
            cur_up = snap["server_up"]      # True | False | None
            if cur_up is True:
                if prev == "down":
                    send_snapshot(mode="RECUPEROU", extra="🟢 **O sistema voltou a funcionar.** Os testes continuam de onde pararam.")
                    last_hb = now
                st["server_state"] = "up"
            elif cur_up is False:
                if prev == "up":
                    age = int(snap.get("hb_age_sec") or 0)
                    send_snapshot(mode="ALERTA",
                                  extra=f"O sistema parou de responder há {age//60} min.")
                    last_hb = now
                # so registra 'down' se ja conheciamos o servidor (evita nag no 1o boot)
                if prev is not None:
                    st["server_state"] = "down"
            # cur_up None => desconhecido: nao alerta, nao muda estado

            # ---- RESTART RAPIDO (boots no log de hoje) ----
            bc = _boot_count_today()
            prev_bc = st.get("boot_count")
            bc_day = st.get("boot_count_day")
            import datetime as _dt2
            today = _dt2.datetime.now().strftime("%Y-%m-%d")
            if bc_day != today:
                st["boot_count_day"] = today; prev_bc = None
            if bc >= 0 and prev_bc is not None and bc > prev_bc:
                send_snapshot(mode="ALERTA",
                              extra=f"🔁 **O sistema foi reiniciado** ({bc}ª vez hoje). Se não foi você, pode ter havido queda de energia.")
                last_hb = now
            if bc >= 0:
                st["boot_count"] = bc

            # ---- RESTART (novos segmentos), so se o servidor esta no ar ----
            prev_boots = st.get("n_boots")
            if (prev_boots is not None and snap["n_boots"] > prev_boots
                    and cur_up is not False):
                send_snapshot(mode="ALERTA",
                              extra="🔁 **O sistema reiniciou.** O histórico continua salvo.")
                last_hb = now

            # ---- STATUS periodico (a cada interval_h) + digest diario ----
            if now - last_hb >= interval_h * 3600:
                is_digest = (datetime.now().hour == digest_hour
                             and st.get("digest_day") != datetime.now().strftime("%Y-%m-%d"))
                if is_digest:
                    send_snapshot(mode="digest diario")
                    st["digest_day"] = datetime.now().strftime("%Y-%m-%d")
                else:
                    send_snapshot(mode="status")
                last_hb = now

            st["n_boots"] = snap["n_boots"]
            _save_state(st)
        except Exception as ex:
            print(f"[remote_monitor] erro no loop: {ex}")
        time.sleep(poll_sec)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="envia um snapshot e sai")
    args = ap.parse_args()
    if args.once:
        send_snapshot(mode="manual/--once")
    else:
        loop()
