"""Diagnostico do braco MANUAL — roda no Windows: python check_mt5.py

Separa as quatro perguntas que o erro unico do MT5 mistura:
  1. o pacote Python existe?
  2. o terminal existe no disco?
  3. o initialize() conecta?
  4. a conta esta LOGADA e o historico e legivel?
"""
import os, glob, sys

SEP = "=" * 68
print(SEP)

# ---- 0. contexto: elevacao e arquitetura -------------------------------
# O erro -6 e frequentemente causado por DESCASAMENTO DE PRIVILEGIO: se o
# Python roda elevado (PowerShell "Administrador") e o terminal MT5 roda como
# usuario comum, a autorizacao entre os processos falha. Os dois precisam
# estar no MESMO nivel.
try:
    import ctypes, platform, struct
    _admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
except Exception:
    _admin = None
_bits = struct.calcsize("P") * 8 if "struct" in dir() else "?"
print(f"[0] contexto")
print(f"       python .......... {sys.version.split()[0]} ({_bits} bits)")
print(f"       elevado (admin) . {_admin}")
if _admin:
    print("       AVISO: este processo esta ELEVADO. Se o MT5 estiver aberto como")
    print("              usuario comum, a API falha com -6. Teste num PowerShell")
    print("              NORMAL (sem 'Executar como administrador').")
try:
    import subprocess as _sp
    _o = _sp.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/FO", "CSV", "/NH"],
                 capture_output=True, text=True, timeout=10).stdout.strip()
    print(f"       terminal64.exe .. {'RODANDO' if 'terminal64' in _o else 'NAO esta aberto'}")
    if "terminal64" not in _o:
        print("              O initialize() vai TENTAR abrir o terminal. Um terminal")
        print("              aberto por ele, sem conta salva, devolve exatamente -6.")
    else:
        # ELEVACAO DO TERMINAL. A restricao do Windows (UIPI) bloqueia o sentido
        # BAIXO -> ALTO: um Python comum NAO consegue falar com um terminal
        # elevado. Se o MT5 foi aberto por um script rodando como Administrador
        # (ou pela tarefa agendada com RunLevel Highest), fica exatamente assim.
        _ps = (
            "$p = Get-Process terminal64 -ErrorAction SilentlyContinue | Select -First 1; "
            "if ($p) { try { "
            "$h=[System.Diagnostics.Process]::GetProcessById($p.Id).Handle; "
            "'handle_ok' } catch { 'ACESSO NEGADO' } } else { 'sem processo' }"
        )
        _r = _sp.run(["powershell", "-NoProfile", "-Command", _ps],
                     capture_output=True, text=True, timeout=20).stdout.strip()
        if "ACESSO NEGADO" in _r:
            print("       elevacao ........ TERMINAL ELEVADO (rodando como Administrador)")
            print("              Este Python NAO e elevado -> o Windows bloqueia a")
            print("              comunicacao no sentido baixo->alto. Feche o MT5 e")
            print("              reabra com DUPLO CLIQUE normal (sem admin).")
        else:
            print(f"       elevacao ........ terminal acessivel (mesmo nivel)")
            print("              Entao o -6 nao e privilegio: e CONTA NAO CONECTADA.")
            print("              No MT5, olhe o canto INFERIOR DIREITO: precisa mostrar")
            print("              o servidor da corretora e o ping. Se disser 'Sem")
            print("              conexao' ou 'Conta invalida', faca Arquivo > Login")
            print("              para conta de negociacao.")
except Exception as _ctx_e:
    print(f"       (contexto indisponivel: {_ctx_e})")
print(SEP)

# ---- 1. pacote -----------------------------------------------------------
try:
    import MetaTrader5 as mt5
    print(f"[1] pacote MetaTrader5 ......... OK (v{mt5.__version__})")
except ImportError as e:
    print(f"[1] pacote MetaTrader5 ......... AUSENTE ({e})")
    print("    ACAO: pip install MetaTrader5")
    raise SystemExit(1)

# ---- 2. terminal no disco ------------------------------------------------
pats = [r"C:\Program Files\MetaTrader 5*\terminal64.exe",
        r"C:\Program Files\*MetaTrader*\terminal64.exe",
        r"C:\Program Files (x86)\*MetaTrader*\terminal64.exe",
        os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\*\terminal64.exe")]
disc = []
for pt in pats:
    disc += glob.glob(pt)
disc = list(dict.fromkeys(disc))
if disc:
    print(f"[2] terminal no disco .......... OK ({len(disc)} encontrado(s))")
    for d in disc[:5]:
        print(f"       {d}")
else:
    print("[2] terminal no disco .......... NENHUM ENCONTRADO")
    print("    ACAO: instale o MetaTrader 5 ou informe o caminho manualmente")

CODIGOS = {
    -1: ("falha generica", "reinicie o terminal e tente de novo"),
    -2: ("parametros invalidos", "erro de chamada — reportar"),
    -4: ("terminal nao encontrado", "verifique o caminho do terminal64.exe"),
    -5: ("versao incompativel", "atualize o terminal ou o pacote Python"),
    -6: ("TERMINAL SEM AUTORIZACAO",
         "abra o MT5 e FACA LOGIN numa conta (Arquivo > Login para conta de negociacao). "
         "NAO e o AlgoTrading — esse e o codigo -8"),
    -7: ("nao suportado", "build do terminal nao suporta a API Python"),
    -8: ("AlgoTrading desligado",
         "Ferramentas > Opcoes > Expert Advisors > marcar 'Permitir trading algoritmico'"),
    -10003: ("IPC initialize failed", "terminal64.exe nao encontrado ou falha ao criar processo"),
    -10004: ("IPC recusado", "terminal recusou a conexao"),
    -10005: ("IPC timeout",
             "terminal ocupado, OU nivel de privilegio diferente "
             "(servidor como admin e MT5 como usuario comum, ou vice-versa)"),
}

# ---- 3. initialize -------------------------------------------------------
print(SEP)
conectado = False
for path in [None] + disc[:5]:
    rot = "(automatico)" if path is None else path
    try:
        ok = mt5.initialize() if path is None else mt5.initialize(path=path)
    except Exception as e:
        print(f"[3] initialize {rot}\n       EXCECAO: {e}")
        continue
    if ok:
        print(f"[3] initialize ................. OK  via {rot}")
        conectado = True
        break
    code, desc = mt5.last_error()
    nome, acao = CODIGOS.get(code, ("desconhecido", "ver mql5.com/en/docs/python_metatrader5"))
    print(f"[3] initialize {rot}")
    print(f"       FALHOU [{code}] {desc}")
    print(f"       significa: {nome}")
    print(f"       ACAO     : {acao}")

if not conectado:
    print(SEP)
    print("VEREDITO: braco MANUAL continua dormente. Resolva a ACAO acima e rode de novo.")
    raise SystemExit(1)

# ---- 4. conta e historico ------------------------------------------------
print(SEP)
info = mt5.account_info()
if info is None:
    code, desc = mt5.last_error()
    print(f"[4] conta ...................... NAO LOGADA [{code}] {desc}")
    print("    ACAO: no MT5, Arquivo > Login para conta de negociacao")
    mt5.shutdown(); raise SystemExit(1)
print(f"[4] conta ...................... OK")
print(f"       login {info.login} | {info.server} | {info.currency}")
print(f"       saldo {info.balance:,.2f} | equity {info.equity:,.2f}")

import datetime as dt
fim = dt.datetime.now(); ini = fim - dt.timedelta(days=365)
deals = mt5.history_deals_get(ini, fim)
n = 0 if deals is None else len(deals)
print(f"[5] historico 12 meses ......... {n} operacoes")
if n == 0:
    print("       (conta sem operacoes no periodo — o braco vai gravar NAV plano)")
print(SEP)
print("VEREDITO: braco MANUAL PRONTO. Reinicie o servidor (QHOS_RESTART.bat).")
mt5.shutdown()
