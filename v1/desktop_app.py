"""
QHOS TERMINAL — app desktop (estilo MetaTrader: janela nativa, sem browser)
===========================================================================
Embrulha a dashboard (localhost:8000) numa janela desktop via pywebview.
Se o servidor nao estiver no ar, SOBE o main.py automaticamente e espera.

Requisito (uma vez):  pip install pywebview
Uso:                  python desktop_app.py   (ou 2 cliques no QHOS.bat)
"""
from __future__ import annotations
import os
import socket
import subprocess
import sys
import time

# Raiz canonica vinda do config (root_dir). Antes cada modulo recalculava o
# caminho com dirname(abspath(__file__)) — mesma classe de problema dos valores
# duplicados: uma verdade decidida em varios lugares.
try:
    from core.config import config as _cfg
    ROOT = str(_cfg.root_dir)
except Exception:
    ROOT = os.path.dirname(os.path.abspath(__file__))
URL = "http://localhost:8000"
PORT = 8000


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.6)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def ensure_server(timeout: int = 90) -> bool:
    """Se a porta 8000 estiver fechada, sobe o main.py e espera ficar pronto."""
    if port_open(PORT):
        try:
            hb = os.path.join(ROOT, "data_lake", "server_heartbeat.txt")
            age_min = (time.time() - float(open(hb).read().strip())) / 60
            print(f"[QHOS] servidor JA no ar (heartbeat {age_min:.1f} min).")
            print("[QHOS] AVISO: se voce mudou o codigo, use QHOS_RESTART.bat p/ recarregar.")
        except Exception:
            print("[QHOS] servidor ja no ar.")
        return True
    print("[QHOS] servidor fora do ar — subindo main.py ...")
    # CREATE_NEW_CONSOLE: o servidor ganha a PROPRIA janela de console, visivel,
    # com o log rolando ao vivo (diagnostico). Use QHOS_HIDE=1 p/ esconder.
    if os.name == "nt":
        flags = 0x08000000 if os.environ.get("QHOS_HIDE") == "1" else 0x00000010
    else:
        flags = 0
    subprocess.Popen([sys.executable, os.path.join(ROOT, "main.py")],
                     cwd=ROOT, creationflags=flags)
    print("[QHOS] console do servidor aberto em janela separada (log ao vivo).")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_open(PORT):
            print("[QHOS] servidor no ar.")
            time.sleep(2.0)  # da tempo do dashboard montar
            return True
        time.sleep(1.5)
    print("[QHOS] servidor nao subiu a tempo — abrindo a janela mesmo assim.")
    return False


def mt5_running() -> bool:
    """Ha um terminal MT5 em execucao?"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
                             capture_output=True, text=True, timeout=10).stdout.lower()
        return "terminal64" in out
    except Exception:
        return False


def ensure_mt5():
    """Abre o MetaTrader 5 (necessario para o braco MANUAL ler a conta)."""
    if os.name != "nt":
        return
    if mt5_running():
        print("[QHOS] MetaTrader 5 ja esta aberto.")
        return
    import glob
    pats = [r"C:\Program Files\MetaTrader 5*\terminal64.exe",
            r"C:\Program Files\*MetaTrader*\terminal64.exe",
            os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\*\terminal64.exe")]
    found = []
    for pt in pats:
        found += glob.glob(pt)
    if not found:
        print("[QHOS] MetaTrader 5 nao encontrado no disco (braco MANUAL ficara dormente).")
        return
    try:
        subprocess.Popen([found[0]], cwd=os.path.dirname(found[0]))
        print(f"[QHOS] MetaTrader 5 aberto: {found[0]}")
        time.sleep(6)
    except Exception as e:
        print(f"[QHOS] falha ao abrir o MT5: {e}")


def main():
    ensure_mt5()
    ensure_server()
    try:
        import webview
    except ImportError:
        print("[QHOS] pywebview ausente — instalando automaticamente...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install",
                                   "pywebview", "MetaTrader5"])
            import webview
        except Exception as e:
            print(f"[QHOS] instalacao falhou ({e}). Rode: pip install pywebview MetaTrader5")
            print(f"[QHOS] alternativa: abra {URL} no browser.")
            return
    print(f"[QHOS] abrindo janela do terminal em {URL} ...")
    try:
        webview.create_window("QHOS ▸ TERMINAL", URL, width=1600, height=950,
                              background_color="#000000", maximized=True)
        webview.start()
    except Exception as e:
        print(f"[QHOS] ERRO na janela: {type(e).__name__}: {e}")
        print(f"[QHOS] abra {URL} no navegador como alternativa.")
    print("[QHOS] janela fechada. (O servidor continua rodando na outra janela.)")


if __name__ == "__main__":
    main()
