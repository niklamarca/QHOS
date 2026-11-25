"""Aviso ao Discord do autor pelo canal que ja existe (chamar_autor.ps1, webhook na variavel do usuario Windows).

O webhook nunca passa por aqui: o script do Windows o le da variavel QHOS_AVISOS_WEBHOOK_URL. Funciona de dentro dos
servicos do systemd (interop do WSL testado em 29/10 com o mesmo isolamento das unidades). Falha no aviso nunca
derruba quem avisa: devolve False e quem chamou registra.
"""
from __future__ import annotations

import subprocess

POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
SCRIPT = r"C:\Users\usuario\Downloads\Rithmic\avisos_discord\chamar_autor.ps1"


def avisar(mensagem: str, *, chamar: bool = True, executar=subprocess.run) -> bool:
    """Posta `mensagem` (com @everyone se `chamar`, para notificar o celular). True se o script saiu com 0."""
    comando = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT, "-Mensagem", mensagem[:1900]]
    if chamar:
        comando.append("-Chamar")
    try:
        return executar(comando, capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def login_recusado(quem: str, motivo: str, quando: str, consequencia: str) -> str:
    rotulo = "LOGIN RECUSADO" if motivo == "login_recusado" else "LOGOUT FORCADO"
    return (f"QHOS: {rotulo} pela Rithmic no {quem} ({quando}). {consequencia} Nada foi tentado de novo. "
            f"Para voltar: conferir a conta na Rithmic e liberar a quarentena a mao.")
