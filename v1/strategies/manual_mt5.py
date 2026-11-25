"""
QUANT HPC OS — Braço MANUAL (MetaTrader 5, READ-ONLY)
=====================================================
Mede o trading MANUAL da conta MT5 com a MESMA regua dos outros braços.
Estritamente leitura: account_info() + history_deals_get(). NUNCA envia ordem.

Requisitos NA MAQUINA (Windows): terminal MT5 desktop instalado e logado na
conta, e `pip install MetaTrader5`. Sem isso, o braço fica dormente (sem erro).

Ledger diario append-only: data_lake/track_record/manual_nav.csv
  date, equity, ret (vs dia anterior), n_deals_dia
"""
from __future__ import annotations
import json, os
from datetime import datetime
from core.config import config
from core.logger import get_logger

log = get_logger("ManualArm")


class ManualMT5Arm:
    def __init__(self):
        lake = str(config.data_lake_dir)
        self.state_path = os.path.join(lake, "manual_state.json")
        self.ledger_path = os.path.join(lake, "track_record", "manual_nav.csv")
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
        self.baseline = None
        self.last_equity = None
        self.last_date = None
        self._warned = False
        self._load_state()

    def _load_state(self):
        try:
            if os.path.exists(self.state_path):
                st = json.load(open(self.state_path))
                self.baseline = st.get("baseline")
                self.last_equity = st.get("last_equity")
                self.last_date = st.get("last_date")
        except Exception as e:
            log.warning(f"ManualArm: estado corrompido: {e}")

    def _save_state(self):
        try:
            tmp = self.state_path + ".tmp"
            json.dump({"baseline": self.baseline, "last_equity": self.last_equity,
                       "last_date": self.last_date}, open(tmp, "w"))
            os.replace(tmp, self.state_path)
        except Exception as e:
            log.warning(f"ManualArm: falha ao salvar estado: {e}")

    @staticmethod
    def _discover_terminals():
        """Varre os locais usuais do Windows por terminal64.exe (nomes variam por corretora)."""
        import glob as _g
        pats = [r"C:\Program Files\MetaTrader 5*\terminal64.exe",
                r"C:\Program Files\*MetaTrader*\terminal64.exe",
                r"C:\Program Files (x86)\*MetaTrader*\terminal*.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Programs\*MetaTrader*\terminal64.exe"),
                os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\*\terminal64.exe")]
        found = []
        for pt in pats:
            try:
                found += _g.glob(pt)
            except Exception:
                pass
        seen, out = set(), []
        for f in found:
            if f not in seen:
                seen.add(f); out.append(f)
        return out[:6]

    def step(self) -> bool:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            if not self._warned:
                log.info("ManualArm dormente: pacote MetaTrader5 ausente (pip install MetaTrader5)")
                self._warned = True
            return False
        try:
            # tenta o caminho explicito do terminal (mais confiavel que o auto-detect)
            ok = False
            paths = [None] + self._discover_terminals()
            for _p in paths:
                try:
                    ok = mt5.initialize() if _p is None else mt5.initialize(path=_p)
                except Exception:
                    ok = False
                if ok:
                    break
            if not ok:
                # log DETALHADO a cada 10 tentativas (nao so uma vez) p/ diagnostico
                self._fail_n = getattr(self, "_fail_n", 0) + 1
                if self._fail_n <= 3 or self._fail_n % 10 == 0:
                    try:
                        code, desc = mt5.last_error()
                    except Exception:
                        code, desc = "?", "?"
                    # Mapeamento REAL dos codigos do MetaTrader5 Python API.
                    # A mensagem antiga dizia "ligue o AlgoTrading" para QUALQUER
                    # erro — mandava o operador mexer na coisa errada. -8 e o
                    # AlgoTrading; -6 e autorizacao do terminal (conta nao logada).
                    _acao = {
                        -1:  "falha generica — reinicie o terminal",
                        -2:  "parametros invalidos na chamada",
                        -4:  "terminal nao encontrado — verifique o caminho",
                        -5:  "versao do terminal incompativel com o pacote Python",
                        -6:  "TERMINAL SEM AUTORIZACAO: abra o MT5 e FACA LOGIN "
                             "numa conta (Arquivo > Login). Nao e o AlgoTrading",
                        -7:  "operacao nao suportada por esta build",
                        -8:  "AlgoTrading desligado: Ferramentas > Opcoes > "
                             "Expert Advisors > permitir trading algoritmico",
                        -10003: "terminal64.exe nao encontrado / falha ao criar processo",
                        -10004: "conexao IPC recusada",
                        -10005: "IPC timeout — terminal ocupado ou elevado "
                                "(servidor admin x MT5 usuario comum?)",
                    }.get(code, "causa desconhecida — veja mql5.com/en/docs/python_metatrader5")
                    log.info(f"ManualArm dormente (tentativa {self._fail_n}): initialize falhou "
                             f"[{code}] {desc} -> {_acao}")
                return False
            self._fail_n = 0
            info = mt5.account_info()
            if info is None:
                mt5.shutdown()
                return False
            eq = float(info.equity)
            day0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            deals = mt5.history_deals_get(day0, datetime.now()) or []
            n_deals = len(deals)
            mt5.shutdown()
        except Exception as e:
            log.warning(f"ManualArm: leitura falhou: {e}")
            try:
                mt5.shutdown()
            except Exception:
                pass
            return False

        today = str(datetime.now().date())
        if self.baseline is None:
            self.baseline = eq
        wrote = False
        if self.last_date != today:
            prev = self.last_equity if self.last_equity else eq
            ret = eq / prev - 1.0 if prev else 0.0
            header = not os.path.exists(self.ledger_path)
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                if header:
                    f.write("date,equity,ret,n_deals\n")
                f.write(f"{today},{eq:.2f},{ret:.6f},{n_deals}\n")
            self.last_date = today
            wrote = True
            log.info(f"ManualArm: {today} equity=${eq:,.2f} ({(eq/self.baseline-1)*100:+.2f}% vs baseline, {n_deals} deals hoje)")
        self.last_equity = eq
        self._save_state()
        return wrote

    def status(self) -> dict:
        return {"equity": self.last_equity, "baseline": self.baseline,
                "ret": (self.last_equity / self.baseline - 1.0) if (self.last_equity and self.baseline) else None}
