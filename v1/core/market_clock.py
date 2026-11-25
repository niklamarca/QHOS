"""market_clock — calendario NYSE sem dependencias externas.

POR QUE ISTO EXISTE
-------------------
O sistema e de barra DIARIA. Antes deste modulo ele ingeria e AGIA 24/7,
inclusive com o pregao aberto — quando o provider devolve a barra do dia
AINDA INCOMPLETA. Consequencia medida em 2026-08-12: o mesmo dia foi gravado
com fechamento 772.49 no nav.csv e 772.687 nos ledgers dos bracos. Uma barra
que muda enquanto e lida nao e uma observacao; e ruido com data.

Regra central: so se age sobre BARRA LIQUIDADA (sessao encerrada + folga).

Sem pytz/pandas_market_calendars: DST e feriados sao calculados por regra.
"""
from __future__ import annotations
from datetime import datetime, date, time, timedelta, timezone

__all__ = ["now_et", "is_trading_day", "session_state", "bar_is_final",
           "last_settled_session", "should_ingest", "should_act",
           "next_open_et", "describe"]

# folga apos o sino para o provider consolidar a barra final
SETTLE_MINUTES = 30
# janela de ingestao: abre um pouco antes do sino, fecha depois da liquidacao
PRE_OPEN_MINUTES = 15
POST_INGEST_HOURS = 3

_OPEN = time(9, 30)
_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)


# ---------------------------------------------------------------- fuso
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-esimo <weekday> (0=segunda) do mes. n negativo = ultimo."""
    if n > 0:
        d = date(year, month, 1)
        delta = (weekday - d.weekday()) % 7
        return d + timedelta(days=delta + 7 * (n - 1))
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    delta = (d.weekday() - weekday) % 7
    return d - timedelta(days=delta)


def _dst_bounds(year: int) -> tuple[date, date]:
    """EUA desde 2007: 2o domingo de marco -> 1o domingo de novembro."""
    return _nth_weekday(year, 3, 6, 2), _nth_weekday(year, 11, 6, 1)


def _et_offset(utc_dt: datetime) -> timedelta:
    """UTC-4 (EDT) no horario de verao, UTC-5 (EST) fora dele.

    A transicao ocorre as 2h locais; aproximamos pelas 07:00 UTC do dia da
    virada, o que so difere da regra oficial numa janela de poucas horas por
    ano — irrelevante para um sistema de barra diaria.
    """
    y = utc_dt.year
    start, end = _dst_bounds(y)
    start_dt = datetime(start.year, start.month, start.day, 7, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 6, tzinfo=timezone.utc)
    return timedelta(hours=-4) if start_dt <= utc_dt < end_dt else timedelta(hours=-5)


def now_et(utc_dt: datetime | None = None) -> datetime:
    """Agora em horario de Nova York (naive, ja convertido)."""
    u = utc_dt or datetime.now(timezone.utc)
    if u.tzinfo is None:
        u = u.replace(tzinfo=timezone.utc)
    return (u + _et_offset(u)).replace(tzinfo=None)


# ------------------------------------------------------------ feriados
def _easter(year: int) -> date:
    """Computus gregoriano (algoritmo anonimo)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo, dy = divmod(h + l - 7 * m + 114, 31)
    return date(year, mo, dy + 1)


def _observed(d: date) -> date:
    """NYSE: feriado no sabado -> sexta anterior; no domingo -> segunda."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _holidays(year: int) -> set[date]:
    h = {
        _observed(date(year, 1, 1)),                  # Ano Novo
        _nth_weekday(year, 1, 0, 3),                  # MLK
        _nth_weekday(year, 2, 0, 3),                  # Presidents
        _easter(year) - timedelta(days=2),            # Sexta-feira Santa
        _nth_weekday(year, 5, 0, -1),                 # Memorial
        _observed(date(year, 7, 4)),                  # Independencia
        _nth_weekday(year, 9, 0, 1),                  # Labor
        _nth_weekday(year, 11, 3, 4),                 # Thanksgiving
        _observed(date(year, 12, 25)),                # Natal
    }
    if year >= 2022:
        h.add(_observed(date(year, 6, 19)))           # Juneteenth
    return h


def _early_close_days(year: int) -> set[date]:
    """Pregoes de meio periodo (fecham 13:00 ET)."""
    out = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}   # dia apos Thanksgiving
    jul3 = date(year, 7, 3)
    if jul3.weekday() < 5 and _observed(date(year, 7, 4)) == date(year, 7, 4):
        out.add(jul3)
    dec24 = date(year, 12, 24)
    if dec24.weekday() < 5:
        out.add(dec24)
    return {d for d in out if d.weekday() < 5}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _holidays(d.year)


def _close_time(d: date) -> time:
    return _EARLY_CLOSE if d in _early_close_days(d.year) else _CLOSE


# ------------------------------------------------------------- estados
def session_state(utc_dt: datetime | None = None) -> str:
    """WEEKEND | HOLIDAY | PRE | OPEN | SETTLING | POST."""
    et = now_et(utc_dt)
    d, t = et.date(), et.time()
    if d.weekday() >= 5:
        return "WEEKEND"
    if not is_trading_day(d):
        return "HOLIDAY"
    close = _close_time(d)
    settle = (datetime.combine(d, close) + timedelta(minutes=SETTLE_MINUTES)).time()
    if t < _OPEN:
        return "PRE"
    if t < close:
        return "OPEN"
    if t < settle:
        return "SETTLING"
    return "POST"


def bar_is_final(d: date, utc_dt: datetime | None = None) -> bool:
    """A barra diaria de <d> ja e uma observacao definitiva?"""
    if not is_trading_day(d):
        return False
    et = now_et(utc_dt)
    settle = datetime.combine(d, _close_time(d)) + timedelta(minutes=SETTLE_MINUTES)
    return et >= settle


def last_settled_session(utc_dt: datetime | None = None) -> date:
    """Ultimo pregao cuja barra ja pode ser consumida com seguranca."""
    et = now_et(utc_dt)
    d = et.date()
    for _ in range(15):
        if bar_is_final(d, utc_dt):
            return d
        d -= timedelta(days=1)
    return d


def next_open_et(utc_dt: datetime | None = None) -> datetime:
    et = now_et(utc_dt)
    d = et.date()
    if is_trading_day(d) and et.time() < _OPEN:
        return datetime.combine(d, _OPEN)
    for _ in range(1, 15):
        d += timedelta(days=1)
        if is_trading_day(d):
            return datetime.combine(d, _OPEN)
    return datetime.combine(d, _OPEN)


# ------------------------------------------------------------- portoes
def should_ingest(utc_dt: datetime | None = None) -> bool:
    """Baixar dados faz sentido agora?

    Sim das 09:15 ET ate ~3h apos a liquidacao, em dia de pregao. Fora disso
    o provider nao tem nada novo — pedir e so gastar quota e arriscar rate-limit.
    """
    et = now_et(utc_dt)
    d, t = et.date(), et.time()
    if not is_trading_day(d):
        return False
    pre = (datetime.combine(d, _OPEN) - timedelta(minutes=PRE_OPEN_MINUTES)).time()
    fim = (datetime.combine(d, _close_time(d))
           + timedelta(minutes=SETTLE_MINUTES, hours=POST_INGEST_HOURS)).time()
    return pre <= t <= fim


def should_act(utc_dt: datetime | None = None) -> bool:
    """Gerar sinal / mandar ordem / mover ledger faz sentido agora?

    Somente com a barra do dia LIQUIDADA. Antes disso qualquer decisao usa
    uma vela que ainda vai mudar — nao e informacao, e ruido com data.
    """
    return session_state(utc_dt) == "POST"


def describe(utc_dt: datetime | None = None) -> dict:
    et = now_et(utc_dt)
    st = session_state(utc_dt)
    rotulo = {"OPEN": "pregao aberto", "PRE": "pre-abertura", "SETTLING": "liquidando barra",
              "POST": "pos-fechamento", "WEEKEND": "fim de semana", "HOLIDAY": "feriado NYSE"}[st]
    return {"state": st, "label": rotulo, "et": et.strftime("%Y-%m-%d %H:%M ET"),
            "trading_day": is_trading_day(et.date()),
            "last_settled": last_settled_session(utc_dt).isoformat(),
            "next_open": next_open_et(utc_dt).strftime("%Y-%m-%d %H:%M ET"),
            "ingest": should_ingest(utc_dt), "act": should_act(utc_dt)}


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), indent=2, ensure_ascii=False))
