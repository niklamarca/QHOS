"""Partida sem terminal pelo systemd: usuario e senha vem da credencial do systemd, nunca de argumento ou ambiente.

    python -m coleta_multi.servico agenda rodar --calendario ... --symbol ESZ6 --exchange CME --online --agreements-accepted
    python -m coleta_multi.servico coletor --online --agreements-accepted
    python -m coleta_multi.servico agenda-continuo     (calendario sem prazo e contrato do ES pela rolagem)
    python -m coleta_multi.servico coletor-continuo

A unidade declara `LoadCredential=rithmic-paper:<arquivo>` e o systemd poe uma copia so de leitura em
$CREDENTIALS_DIRECTORY/rithmic-paper (linha 1 usuario, linha 2 senha). Este lancador so le esse arquivo e chama o
`main` de qhos2.agenda (sem mudar nada nele) ou de coleta_multi.coletor, no lugar do prompt. Nada e impresso sobre a
credencial; erro e codigo curto. Saida 2 = recusa antes do login (o systemd nao repete).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

NOME = "rithmic-paper"


def ler_credencial(ambiente=os.environ) -> tuple:
    pasta = ambiente.get("CREDENTIALS_DIRECTORY")
    if not pasta:
        raise ValueError("credencial_ausente")
    try:
        texto = (Path(pasta) / NOME).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ValueError("credencial_ilegivel") from None
    linhas = texto.split("\n")
    usuario = linhas[0].strip() if linhas else ""
    senha = linhas[1].rstrip("\r") if len(linhas) > 1 else ""
    if not usuario or not senha:
        raise ValueError("credencial_incompleta")
    return usuario, senha


def _continuo(alvo: str, ambiente=os.environ) -> list:
    """Gera o calendario do dia (sem prazo) e monta os argumentos; para o ES, escolhe o contrato pela rolagem."""
    from qhos2 import agenda
    from coleta_multi import calendario_continuo
    raiz = Path(ambiente.get("QHOS2_DATA_ROOT") or Path.home() / "qhos-v2-data")
    # Agendador e coletor seguem o mesmo pregao padrao (09:00-16:15 NY), do mesmo calendario.
    caminho, calendario = calendario_continuo.atualizar(raiz / "coleta_multi" / "calendario",
                                                        datetime.now(agenda.NY).date())
    print(f"calendario {caminho}: {len(calendario['janelas'])} janelas ate {calendario['valido_ate']}; "
          f"ES {calendario['contrato_es']} (rola em {calendario['proxima_rolagem_es']})", flush=True)
    if alvo == "agenda-continuo":
        return ["rodar", "--calendario", str(caminho), "--symbol", calendario["contrato_es"], "--exchange", "CME",
                "--online", "--agreements-accepted"]
    return ["--calendario", str(caminho), "--online", "--agreements-accepted"]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in {"agenda", "coletor", "agenda-continuo", "coletor-continuo"}:
        print("uso: servico agenda|coletor|agenda-continuo|coletor-continuo <argumentos>")
        return 2
    alvo, resto = argv[0], argv[1:]
    try:
        usuario, senha = ler_credencial()
    except ValueError as erro:
        print(f"servico recusado antes do login: {erro}", flush=True)
        return 2
    if alvo.endswith("-continuo"):
        try:
            resto = _continuo(alvo) + resto
        except (OSError, ValueError) as erro:
            print(f"servico recusado antes do login: calendario_{type(erro).__name__}", flush=True)
            return 2
        alvo = alvo.split("-")[0]
    if alvo == "agenda":
        _guarda_do_es_volta_sozinha()
        from qhos2 import agenda as modulo
    else:
        from coleta_multi import coletor as modulo
    try:
        codigo = modulo.main(resto, entrada=lambda _prompt: usuario, senha=lambda _prompt: senha)
    finally:
        del usuario, senha
    if alvo == "agenda" and codigo == 3:
        _avisar_login_do_es()
    return codigo


def _estado_agenda(ambiente=os.environ) -> Path:
    return Path(ambiente.get("QHOS2_DATA_ROOT") or Path.home() / "qhos-v2-data") / "agenda" / "estado.json"


def _guarda_do_es_volta_sozinha(dormir=None) -> str:
    """Depois de queda ou reinicio (nunca de login recusado), libera a guarda do ES antes da partida."""
    import time
    from qhos2.operacao import estado_padrao
    from coleta_multi import guarda_es
    try:
        estado = json.loads((estado_padrao() / "estado.json").read_text(encoding="utf-8")).get("estado")
    except (OSError, ValueError):
        estado = "LIVRE"
    if estado == "LIVRE":
        return "livre"
    # Pode ter acabado de cair (reinicio no meio do pregao): espera o servidor descartar a sessao morta.
    (dormir or time.sleep)(guarda_es.ESPERA_S)
    resultado = guarda_es.liberar_se_queda(estado_padrao(), _estado_agenda(), espera_s=guarda_es.ESPERA_S)
    print(f"guarda do ES ao partir: {estado} -> {resultado}", flush=True)
    return resultado


def _avisar_login_do_es() -> bool:
    from coleta_multi import aviso
    try:
        estado = json.loads(_estado_agenda().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        estado = {}
    motivo = estado.get("motivo") or "login_recusado"
    ok = aviso.avisar(aviso.login_recusado(
        "agendador do ES", motivo, str(estado.get("dia") or estado.get("atualizado_utc") or "?"),
        "O agendador do ES parou e nao tenta de novo, nem depois de reiniciar; o coletor dos outros ativos segue."))
    print(f"aviso ao Discord: {'enviado' if ok else 'FALHOU'}", flush=True)
    return ok


if __name__ == "__main__":
    sys.exit(main())
