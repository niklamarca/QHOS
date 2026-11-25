"""Gravacao minima de uma captura da V2, no mesmo formato do V1 (JSONL + manifesto), verificavel pelo catalogo.

Uma pasta por captura, com `market_frames.jsonl` (uma linha por quadro: index, template_id, received_ns e
os bytes brutos em base64, escrita e descarga a cada quadro) e `manifest.json`, escrito de forma atomica
SO no fim. Sem manifesto, a captura e incompleta e o catalogo a recusa: nada meio gravado passa por pronto.

O gravador e o `ao_receber` da sessao: se ele levantar erro (limite de bytes, disco quase cheio, template
fora de mercado), a sessao falha. Sem rotacao nem compressao: isso e da peca 5.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from qhos2 import __version__, disco
from qhos2 import rprotocol as p
from qhos2.catalogo import _escrever_atomico
from qhos2.config import validar_data_root
from qhos2.operacao import _atomico, IDENTIDADE_ARQUIVO, IDENTIDADE_ESQUEMA, ler_identidade_captura, OperacaoError

ESQUEMA = "qhos_rithmic_depth_capture_v1"
ARQUIVO = "market_frames.jsonl"
MAX_BYTES_PADRAO = 1024 ** 3
MIN_LIVRE_PADRAO = 512 * 1024 ** 2
VERIFICAR_DISCO_A_CADA = 1000
SINCRONIZAR_A_CADA = 1000
_TEMPLATES_DE_MERCADO = frozenset({p.RESP_RETRATO_PROFUNDIDADE, p.PROFUNDIDADE_ORDEM, p.PROFUNDIDADE_FIM,
                                   p.ULTIMO_NEGOCIO})
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class GravadorError(RuntimeError):
    """Falha de gravacao, com codigo curto e estatico."""


class Gravador:
    def __init__(self, raiz, *, identidade: dict, max_bytes: int = MAX_BYTES_PADRAO,
                 min_livre_bytes: int = MIN_LIVRE_PADRAO, captura_id: Optional[str] = None):
        if not isinstance(identidade, dict):
            raise GravadorError("identidade_incompleta")
        for chave in ("system_name", "requested_symbol", "exchange"):
            if not isinstance(identidade.get(chave), str) or not identidade[chave]:
                raise GravadorError("identidade_incompleta")
        self.id = uuid.uuid4().hex if captura_id is None else captura_id
        if not isinstance(self.id, str) or not _ID.fullmatch(self.id):
            raise GravadorError("captura_id_invalido")
        if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0 or
                not isinstance(min_livre_bytes, int) or isinstance(min_livre_bytes, bool) or min_livre_bytes < 0):
            raise GravadorError("limites_invalidos")
        self.identidade, self.max_bytes, self.min_livre_bytes = dict(identidade), max_bytes, min_livre_bytes
        raiz = validar_data_root(raiz)
        raiz.mkdir(parents=True, exist_ok=True)
        self.raiz = raiz
        self._checar_disco()
        self.pasta = raiz / self.id
        self.pasta.mkdir(mode=0o700)  # FileExistsError se a captura ja existe: nunca sobrescreve
        self._arquivo = open(self.pasta / ARQUIVO, "xb")
        try:
            info = os.fstat(self._arquivo.fileno())
            cabecalho = {"schema": IDENTIDADE_ESQUEMA, "capture_id": self.id,
                         "raw_dev": info.st_dev, "raw_ino": info.st_ino,
                         **{c: self.identidade[c] for c in ("system_name", "requested_symbol", "exchange")}}
            _atomico(self.pasta / IDENTIDADE_ARQUIVO, cabecalho)
            ler_identidade_captura(self.pasta)
            self._identidade_sha256 = hashlib.sha256((self.pasta / IDENTIDADE_ARQUIVO).read_bytes()).hexdigest()
        except Exception:
            self._arquivo.close()
            raise GravadorError("identidade_de_captura_nao_persistida") from None
        self._digest = hashlib.sha256()
        self.quadros = self.bytes = 0
        self._fechado, self._manifesto = False, None

    def _checar_disco(self) -> None:
        if disco.livre_bytes(self.raiz) < self.min_livre_bytes:
            raise GravadorError("disco_quase_cheio")

    def __call__(self, dados: bytes, template: int, recebido_ns: int) -> None:
        if self._fechado:
            raise GravadorError("gravador_fechado")
        if template not in _TEMPLATES_DE_MERCADO:
            raise GravadorError("template_de_mercado_esperado")
        linha = (json.dumps({"index": self.quadros, "template_id": template, "received_ns": recebido_ns,
                             "protobuf_base64": base64.b64encode(dados).decode("ascii")},
                            separators=(",", ":")) + "\n").encode("utf-8")
        if self.bytes + len(linha) > self.max_bytes:
            raise GravadorError("limite_de_bytes")
        if self.quadros and self.quadros % VERIFICAR_DISCO_A_CADA == 0:
            self._checar_disco()
        self._arquivo.write(linha)
        self._arquivo.flush()
        if (self.quadros + 1) % SINCRONIZAR_A_CADA == 0:
            os.fsync(self._arquivo.fileno())
        self._digest.update(linha)
        self.quadros += 1
        self.bytes += len(linha)

    def fechar(self, status_sessao: dict, *, fechamento_normal: bool) -> Path:
        """Fecha o arquivo e escreve o manifesto (atomico). Pode ser chamado de novo sem efeito."""
        if self._fechado:
            if self._manifesto is None:
                raise GravadorError("fechamento_anterior_falhou")
            return self._manifesto
        self._fechado = True
        try:
            self._arquivo.flush()
            os.fsync(self._arquivo.fileno())
        finally:
            self._arquivo.close()
        manifesto = {
            "schema": ESQUEMA, "capture_id": self.id,
            "capture_identity_sha256": self._identidade_sha256, "frames": self.quadros, "bytes": self.bytes, "sha256": self._digest.hexdigest(),
            "transport_closed_normally": bool(fechamento_normal), "research_only": True, "can_execute": False,
            "persistence": {"arquivo": ARQUIVO, "formato": "jsonl_base64"},
            "status": {"system_name": self.identidade["system_name"], "plant": "TICKER_PLANT",
                       "requested_symbol": self.identidade["requested_symbol"],
                       "exchange": self.identidade["exchange"],
                       "criado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "versao_qhos2": __version__, "sessao": status_sessao},
        }
        try:
            ler_identidade_captura(self.pasta, manifesto=manifesto)
        except OperacaoError:
            raise GravadorError("identidade_de_captura_diverge") from None
        caminho_manifesto = self.pasta / "manifest.json"
        _escrever_atomico(caminho_manifesto, json.dumps(manifesto, indent=2, sort_keys=True, allow_nan=False) + "\n")
        descritor = os.open(self.pasta, os.O_RDONLY)
        try:
            os.fsync(descritor)
        finally:
            os.close(descritor)
        self._manifesto = caminho_manifesto
        return self._manifesto
