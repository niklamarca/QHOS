"""Extrai os modulos JavaScript embutidos no index.html do terminal para a pasta modulos/.

O index.html e a fonte: um arquivo so, com os modulos em base64 dentro do importmap. A pasta modulos/ e uma
copia legivel, para revisar o codigo. Nao edite modulos/ a mao; rode este script de novo.

  python3 extrair_modulos.py             escreve modulos/ a partir do index.html
  python3 extrair_modulos.py --conferir  so confere; sai com erro se modulos/ nao bate com o index.html
"""
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
HTML = PASTA / "index.html"
MODULOS = PASTA / "modulos"
PREFIXO = "data:text/javascript;base64,"
NOME = re.compile(r"^qhos-module/([a-z0-9-]+\.js)$")


class TerminalError(Exception):
    pass


def modulos_do_html(texto):
    """Devolve {arquivo: bytes} com cada modulo do importmap. Recusa importacao de fora do arquivo."""
    blocos = re.findall(r'<script type="importmap">(.*?)</script>', texto, re.S)
    if len(blocos) != 1:
        raise TerminalError(f"esperado 1 importmap, achados {len(blocos)}")
    importacoes = json.loads(blocos[0]).get("imports")
    if not isinstance(importacoes, dict) or not importacoes:
        raise TerminalError("importmap sem imports")
    saida = {}
    for chave, url in importacoes.items():
        achado = NOME.match(chave)
        if not achado:
            raise TerminalError(f"nome de modulo inesperado: {chave!r}")
        if not isinstance(url, str) or not url.startswith(PREFIXO):
            raise TerminalError(f"{chave} nao esta embutido no arquivo (importacao externa)")
        saida[achado.group(1)] = base64.b64decode(url[len(PREFIXO):], validate=True)
    return saida


def resumo(dados):
    return hashlib.sha256(dados).hexdigest()[:16]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    conferir = argv == ["--conferir"]
    if argv and not conferir:
        print(__doc__)
        return 2
    try:
        esperados = modulos_do_html(HTML.read_text(encoding="utf-8"))
    except (OSError, ValueError, TerminalError) as erro:
        print(f"PARE: {erro}")
        return 1
    if conferir:
        presentes = {arq.name: arq.read_bytes() for arq in MODULOS.glob("*.js")} if MODULOS.is_dir() else {}
        if presentes != esperados:
            faltam = sorted(set(esperados) - set(presentes))
            sobram = sorted(set(presentes) - set(esperados))
            diferentes = sorted(n for n in set(esperados) & set(presentes) if esperados[n] != presentes[n])
            print(f"PARE: modulos/ nao bate com o index.html; faltam {faltam}, sobram {sobram}, diferentes {diferentes}")
            return 1
        print(f"ok: {len(esperados)} modulos batem com o index.html {resumo(HTML.read_bytes())}")
        return 0
    MODULOS.mkdir(exist_ok=True)
    for arq in MODULOS.glob("*.js"):
        if arq.name not in esperados:
            arq.unlink()
    for nome, dados in sorted(esperados.items()):
        (MODULOS / nome).write_bytes(dados)
        print(f"{resumo(dados)} {len(dados):>7} modulos/{nome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
