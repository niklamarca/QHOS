"""Catalogo verificado de capturas: recalcula tudo e nao altera nada.

Uma captura e uma pasta com `manifest.json` e as linhas de quadros (`market_frames.jsonl`,
ou segmentos `frames/00000.jsonl`...). Cada linha e um JSON com `index`, `template_id`,
`received_ns` e `protobuf_base64`. O catalogo NAO decodifica protobuf: so confere contagens,
bytes, SHA-256, continuidade do indice e mede o ritmo de chegada.

O ambiente (paper ou test) vem do `system_name` gravado dentro do manifesto, nunca do nome
do esquema: o V1 chama de "test" ate capturas do Paper, e dado de Test e sintetico.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

MANIFESTO_MAX_BYTES = 8 * 1024 ** 2
LINHA_MAX_BYTES = 2 * 1024 ** 2
SEGMENTOS_MAX = 4096
CAPTURA_MAX_BYTES = 8 * 1024 ** 3
ARQUIVO_LEGADO = "market_frames.jsonl"
PASTA_SEGMENTOS = "frames"
ESQUEMA_CATALOGO = "qhos2.catalogo.v1"
_ESQUEMA_CAPTURA = re.compile(r"^qhos_rithmic_[a-z0-9_]+_capture_v[12]$")
_AMBIENTES = {"Rithmic Paper Trading": "paper", "Rithmic Test": "test_sintetico"}


class CatalogoError(ValueError):
    """Uso invalido do catalogo (nao confundir com captura divergente, que vira relatorio)."""


def classificar_ambiente(system_name) -> str:
    return _AMBIENTES.get(system_name, "desconhecido")


def listar_capturas(raiz) -> List[Path]:
    raiz = Path(raiz)
    if not raiz.is_dir():
        raise CatalogoError("raiz_nao_e_pasta")
    achadas = []
    for pasta in sorted(p for p in raiz.iterdir() if p.is_dir()):
        if ((pasta / "manifest.json").is_file() or (pasta / PASTA_SEGMENTOS).is_dir()
                or (pasta / ARQUIVO_LEGADO).is_file()):
            achadas.append(pasta)
    return achadas


def verificar_captura(pasta, *, limite_bytes: int = CAPTURA_MAX_BYTES) -> dict:
    """Relatorio de uma captura. `verificada` so e True sem nenhuma divergencia."""
    pasta = Path(pasta).resolve()
    relatorio = {"captura_id": pasta.name, "caminho": str(pasta), "verificada": False,
                 "divergencias": []}
    div = relatorio["divergencias"]

    manifesto = _ler_manifesto(pasta, div)
    if manifesto is None:
        return relatorio
    status = manifesto.get("status") if isinstance(manifesto.get("status"), dict) else {}
    esquema = manifesto.get("schema")
    relatorio.update({
        "esquema": esquema,
        "sistema": status.get("system_name"),
        "ambiente": classificar_ambiente(status.get("system_name")),
        "plant": status.get("plant"),
        "simbolo": status.get("requested_symbol"),
        "bolsa": status.get("exchange"),
    })
    if not isinstance(esquema, str) or not _ESQUEMA_CAPTURA.match(esquema):
        div.append("esquema_desconhecido")
        return relatorio

    arquivos = _arquivos_da_captura(pasta, manifesto, div)
    if arquivos is None:
        return relatorio

    total = _Totais()
    for arquivo, esperado in arquivos:
        parcial = _Totais()
        if not _ler_arquivo(arquivo, parcial, total, div, limite_bytes):
            return relatorio  # leitura interrompida: os totais parciais nao dizem nada
        if esperado is not None:
            nome = esperado["name"]
            if (parcial.quadros, parcial.bytes) != (esperado.get("frames"), esperado.get("bytes")):
                div.append(f"segmento_diverge:{nome}")
            elif parcial.digest.hexdigest() != esperado.get("sha256"):
                div.append(f"segmento_sha256_diverge:{nome}")

    if total.quadros != manifesto.get("frames"):
        div.append("frames_divergem")
    if total.bytes != manifesto.get("bytes"):
        div.append("bytes_divergem")
    if total.digest.hexdigest() != manifesto.get("sha256"):
        div.append("sha256_diverge")
    if not total.contiguo:
        div.append("indice_com_buracos")

    relatorio.update(total.resumo())
    relatorio["fechamento_normal"] = manifesto.get("transport_closed_normally")
    relatorio["verificada"] = not div
    return relatorio


def montar_catalogo(raiz, destino, *, gerado_em: Optional[str] = None) -> dict:
    """Verifica todas as capturas de `raiz` e escreve `destino/catalogo.json` de forma atomica."""
    raiz, destino = Path(raiz).resolve(), Path(destino).resolve()
    if destino == raiz or raiz in destino.parents:
        raise CatalogoError("destino_nao_pode_ficar_dentro_da_raiz_das_capturas")
    capturas = [verificar_captura(p) for p in listar_capturas(raiz)]
    quadros = sum(c.get("quadros") or 0 for c in capturas)
    catalogo = {
        "esquema": ESQUEMA_CATALOGO,
        "gerado_em": gerado_em or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raiz": str(raiz),
        "resumo": {"total": len(capturas),
                   "verificadas": sum(1 for c in capturas if c["verificada"]),
                   "quadros": quadros,
                   "bytes": sum(c.get("bytes") or 0 for c in capturas)},
        "capturas": capturas,
    }
    destino.mkdir(parents=True, exist_ok=True)
    _escrever_atomico(destino / "catalogo.json",
                      json.dumps(catalogo, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return catalogo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.catalogo", description=__doc__.splitlines()[0])
    ap.add_argument("raiz", help="pasta com as capturas (uma subpasta por captura)")
    ap.add_argument("destino", nargs="?", help="pasta do catalogo (padrao: <data_root>/catalogo)")
    args = ap.parse_args(argv)
    if args.destino:
        destino = Path(args.destino)
    else:
        from qhos2.config import Settings
        destino = Settings.from_environ().data_root / "catalogo"
    catalogo = montar_catalogo(args.raiz, destino)
    for c in catalogo["capturas"]:
        pico = (c.get("quadros_por_segundo") or {}).get("pico")
        print(f"{c['captura_id']}  verificada={c['verificada']}  ambiente={c.get('ambiente')}  "
              f"quadros={c.get('quadros')}  pico_por_s={pico}  divergencias={c['divergencias']}")
    print(json.dumps(catalogo["resumo"], sort_keys=True))
    print(f"catalogo: {destino / 'catalogo.json'}")
    return 0 if catalogo["resumo"]["verificadas"] == catalogo["resumo"]["total"] else 2


# ---- internos ---------------------------------------------------------------------------

class _Totais:
    def __init__(self):
        self.quadros = 0
        self.bytes = 0
        self.digest = hashlib.sha256()
        self.contiguo = True
        self.primeiro_ns = None
        self.ultimo_ns = None
        self.minimo_ns = self.maximo_ns = None
        self.retrocessos = 0  # quantas vezes o relogio local voltou no tempo entre dois quadros
        self.por_template = Counter()
        self.por_segundo = Counter()
        self.por_template_segundo = Counter()

    def somar(self, linha: bytes, indice, template, recebido_ns):
        if indice != self.quadros:
            self.contiguo = False
        self.digest.update(linha)
        self.quadros += 1
        self.bytes += len(linha)
        if recebido_ns is not None:
            if self.ultimo_ns is not None and recebido_ns < self.ultimo_ns:
                self.retrocessos += 1
            self.minimo_ns = recebido_ns if self.minimo_ns is None else min(self.minimo_ns, recebido_ns)
            self.maximo_ns = recebido_ns if self.maximo_ns is None else max(self.maximo_ns, recebido_ns)
            self.primeiro_ns = recebido_ns if self.primeiro_ns is None else self.primeiro_ns
            self.ultimo_ns = recebido_ns
            segundo = recebido_ns // 1_000_000_000
            self.por_segundo[segundo] += 1
            self.por_template_segundo[(template, segundo)] += 1
        self.por_template[template] += 1

    def resumo(self) -> dict:
        saida = {"quadros": self.quadros, "bytes": self.bytes, "sha256": self.digest.hexdigest(),
                 "indice_continuo": self.contiguo, "por_template": {}, "quadros_por_segundo": None,
                 "primeiro_ns": self.primeiro_ns, "ultimo_ns": self.ultimo_ns, "duracao_s": None,
                 "relogio_voltou": self.retrocessos}
        saida["por_template"] = {str(t): n for t, n in sorted(self.por_template.items())}
        if self.primeiro_ns is not None:
            # minimo e maximo, nao primeiro e ultimo: o relogio de parede pode voltar (ressincronizacao do WSL)
            saida["duracao_s"] = round((self.maximo_ns - self.minimo_ns) / 1e9, 3)
            primeiro, ultimo = self.minimo_ns // 10 ** 9, self.maximo_ns // 10 ** 9
            segundos = ultimo - primeiro + 1
            contagens = sorted(self.por_segundo.get(s, 0) for s in range(primeiro, ultimo + 1))
            p99 = contagens[min(len(contagens) - 1, int(0.99 * len(contagens)))]
            por_template = {}
            for (template, _), n in self.por_template_segundo.items():
                por_template[str(template)] = max(por_template.get(str(template), 0), n)
            saida["quadros_por_segundo"] = {"media": round(self.quadros / segundos, 2),
                                            "p99": p99, "pico": max(contagens),
                                            "pico_por_template": dict(sorted(por_template.items()))}
        return saida


def _ler_manifesto(pasta: Path, div: list) -> Optional[dict]:
    caminho = pasta / "manifest.json"
    if not caminho.is_file():
        div.append("manifesto_ausente")
        return None
    with caminho.open("rb") as f:
        bruto = f.read(MANIFESTO_MAX_BYTES + 1)
    if len(bruto) > MANIFESTO_MAX_BYTES:
        div.append("manifesto_grande_demais")
        return None
    try:
        manifesto = json.loads(bruto)
    except ValueError:
        div.append("manifesto_invalido")
        return None
    if not isinstance(manifesto, dict):
        div.append("manifesto_invalido")
        return None
    return manifesto


def _arquivos_da_captura(pasta: Path, manifesto: dict, div: list):
    """Lista (arquivo, esperado) na ordem do manifesto; recusa caminhos fora da captura."""
    segmentos = manifesto.get("segments")
    if segmentos is None:
        alvo = pasta / ARQUIVO_LEGADO
        if not alvo.is_file():
            div.append("arquivo_ausente")
            return None
        return [(alvo, None)]
    if not isinstance(segmentos, list) or not 1 <= len(segmentos) <= SEGMENTOS_MAX:
        div.append("segmentos_invalidos")
        return None
    saida = []
    for seg in segmentos:
        nome = seg.get("name") if isinstance(seg, dict) else None
        if not isinstance(nome, str) or not nome or os.path.isabs(nome):
            div.append("segmentos_invalidos")
            return None
        alvo = (pasta / nome).resolve()
        if pasta not in alvo.parents:
            div.append("segmento_fora_da_captura")
            return None
        if not alvo.is_file():
            div.append("arquivo_ausente")
            return None
        saida.append((alvo, seg))
    return saida


def _ler_arquivo(arquivo: Path, parcial: _Totais, total: _Totais, div: list, limite: int) -> bool:
    """Le linhas completas; devolve False se algo impede de continuar."""
    with arquivo.open("rb") as f:
        while True:
            linha = f.readline(LINHA_MAX_BYTES + 1)
            if not linha:
                return True
            if len(linha) > LINHA_MAX_BYTES:
                div.append("linha_grande_demais")
                return False
            if not linha.endswith(b"\n"):
                div.append("linha_truncada")
                return False
            try:
                linha_json = json.loads(linha)
                indice, template = linha_json["index"], linha_json["template_id"]
                recebido = linha_json["received_ns"]
                if not all(isinstance(x, int) and not isinstance(x, bool)
                           for x in (indice, template, recebido)):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                div.append("linha_invalida")
                return False
            total.somar(linha, indice, template, recebido)
            parcial.digest.update(linha)
            parcial.quadros += 1
            parcial.bytes += len(linha)
            if total.bytes > limite:
                div.append("limite_de_bytes_excedido")
                return False


def _escrever_atomico(destino: Path, texto: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=destino.parent, prefix=".catalogo-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, destino)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


if __name__ == "__main__":
    sys.exit(main())
