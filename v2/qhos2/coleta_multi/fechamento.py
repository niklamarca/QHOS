"""Fechamento diario: compacta, rele e confere, publica no DataSet, copia o ES e escreve INDEX.json e SHA256SUMS.txt.

Para cada captura fechada do dia (staging no WSL) e para a captura do ES do agendador:
  1. catalogo.verificar_captura; so o que passa segue (o resto entra no INDEX com o motivo);
  2. <BOLSA>_<CONTRATO>.tar.xz no staging, com manifest.json, capture_identity.json e market_frames.jsonl
     dentro de uma pasta <BOLSA>_<CONTRATO>/;
  3. le o xz de volta: SHA-256, bytes e quadros do JSONL batem com o manifesto, e manifesto e identidade
     batem byte a byte com os originais;
  4. copia para <destino>/<AAAA-MM-DD>/ como .parcial, rele e confere o SHA-256 do xz, e so entao renomeia.
     Nunca sobrescreve, nunca apaga original.
O ES e so lido (os arquivos dele sao abertos em modo leitura; nada e escrito na pasta dele).
O ES que o coletor gravou como reserva (o ES do agendador fora do pregao) vai como <BOLSA>_<CONTRATO>_reserva[_pN],
com origem "reserva_do_coletor", para o mesmo lugar do ES do agendador (inclusive a pasta selada).
INDEX.json e SHA256SUMS.txt sao os ultimos a aparecer, de forma atomica.
"""
from __future__ import annotations

import hashlib
import io
import json
import lzma
import multiprocessing
import os
import shutil
import tarfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from qhos2 import catalogo

ESQUEMA_INDEX = "qhos2.dataset.index.v1"
PRESET = 3                     # o mesmo do arquivador (razao ~9x nas capturas do ES)
BLOCO = 4 * 1024 ** 2
ARQUIVOS = ("manifest.json", "capture_identity.json", "market_frames.jsonl")
# O catalogo recusa acima de 8 GiB por padrao; o NQ de um pregao inteiro pode passar disso (teto por ativo 16 GiB).
LIMITE_VERIFICACAO = 32 * 1024 ** 3
ORIGEM_RESERVA = "reserva_do_coletor"


class FechamentoError(RuntimeError):
    """Codigo curto e estatico."""


def nome_da_reserva(captura_id: str, bolsa: Optional[str], contrato: Optional[str]) -> str:
    """CME_ESZ6 -> CME_ESZ6_reserva; CME_ESZ6_p2 -> CME_ESZ6_reserva_p2. Nunca o nome do ES do agendador."""
    prefixo = f"{bolsa}_{contrato}"
    if bolsa and contrato and captura_id.startswith(prefixo):
        return f"{prefixo}_reserva{captura_id[len(prefixo):]}"
    return f"{captura_id}_reserva"


def _sha(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(BLOCO), b""):
            h.update(bloco)
    return h.hexdigest()


def _fsync(caminho: Path) -> None:
    fd = os.open(caminho, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomico(destino: Path, texto: str) -> None:
    tmp = destino.with_name("." + destino.name + f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texto)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, destino)


def compactar(pasta: Path, saida: Path, nome: str) -> dict:
    """Escreve `saida` (.tar.xz) a partir de `pasta`, que so e lida. Recusa se `saida` ja existe."""
    pasta = Path(pasta)
    manifesto_bruto = (pasta / "manifest.json").read_bytes()
    manifesto = json.loads(manifesto_bruto)
    if saida.exists():
        raise FechamentoError("arquivo_ja_existe")
    parcial = saida.with_name(saida.name + ".parcial")
    with open(parcial, "xb") as bruto:
        with lzma.open(bruto, "wb", preset=PRESET) as xz, tarfile.open(fileobj=xz, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for arquivo in ARQUIVOS:
                origem = pasta / arquivo
                info = tarfile.TarInfo(f"{nome}/{arquivo}")
                info.size, info.mtime, info.mode = origem.stat().st_size, int(origem.stat().st_mtime), 0o444
                with open(origem, "rb") as f:
                    tar.addfile(info, f)
        bruto.flush()
        os.fsync(bruto.fileno())
    conferido = conferir(parcial, nome, manifesto,
                         {a: hashlib.sha256((pasta / a).read_bytes()).hexdigest() for a in ARQUIVOS[:2]})
    os.replace(parcial, saida)
    _fsync(saida.parent)
    return {**conferido, "sha256_manifesto": hashlib.sha256(manifesto_bruto).hexdigest(), "manifesto": manifesto}


def conferir(xz: Path, nome: str, manifesto: dict, hashes_pequenos: dict) -> dict:
    """Le o .tar.xz de volta e confere contra o manifesto e os originais."""
    vistos = {}
    with tarfile.open(xz, mode="r|xz") as tar:
        for membro in tar:
            if not membro.isfile() or membro.name not in {f"{nome}/{a}" for a in ARQUIVOS} or membro.name in vistos:
                raise FechamentoError("membro_inesperado")
            f = tar.extractfile(membro)
            h, total, linhas = hashlib.sha256(), 0, 0
            for bloco in iter(lambda: f.read(BLOCO), b""):
                h.update(bloco)
                total += len(bloco)
                linhas += bloco.count(b"\n")
            vistos[membro.name] = (h.hexdigest(), total, linhas)
    if set(vistos) != {f"{nome}/{a}" for a in ARQUIVOS}:
        raise FechamentoError("membro_ausente")
    sha, total, linhas = vistos[f"{nome}/market_frames.jsonl"]
    if (sha, total, linhas) != (manifesto.get("sha256"), manifesto.get("bytes"), manifesto.get("frames")):
        raise FechamentoError("quadros_divergem_do_manifesto")
    for arquivo, esperado in hashes_pequenos.items():
        if vistos[f"{nome}/{arquivo}"][0] != esperado:
            raise FechamentoError(f"{arquivo}_diverge")
    return {"sha256_xz": _sha(xz), "bytes_xz": xz.stat().st_size, "quadros": linhas, "bytes_brutos": total}


def publicar(xz: Path, destino_dia: Path, sha256_xz: str) -> Path:
    destino_dia.mkdir(parents=True, exist_ok=True)
    final = destino_dia / xz.name
    if final.exists():
        raise FechamentoError("destino_ja_existe")
    parcial = destino_dia / (xz.name + ".parcial")
    with open(xz, "rb") as entrada, open(parcial, "xb") as saida:
        for bloco in iter(lambda: entrada.read(BLOCO), b""):
            saida.write(bloco)
        saida.flush()
        os.fsync(saida.fileno())
    if _sha(parcial) != sha256_xz:
        parcial.rename(parcial.with_name(parcial.name + ".divergente"))
        raise FechamentoError("copia_divergente")
    os.replace(parcial, final)
    return final


def _desvio(manifesto: dict) -> dict:
    sessao = (manifesto.get("status") or {}).get("sessao") or {}
    relogio = sessao.get("relogio") or {}
    return {"desvio_ms": relogio.get("desvio_ms"), "rtt_ms": relogio.get("rtt_ms"),
            "ultimo_desvio_ms": relogio.get("ultimo_desvio_ms"), "amostras": relogio.get("amostras"),
            "serie": sessao.get("relogio_serie"), "relogio_alerta": sessao.get("relogio_alerta"),
            "utc_qualificado": False}


def _um(tarefa: tuple) -> dict:
    """Processa uma captura (roda em processo separado). Nunca levanta: devolve a entrada do INDEX."""
    pasta, nome, staging_xz, destino_dia, base = tarefa
    entrada = dict(base, arquivo=f"{nome}.tar.xz")
    rel = catalogo.verificar_captura(pasta, limite_bytes=LIMITE_VERIFICACAO)
    entrada.update(verificada=rel["verificada"], fechamento_normal=rel.get("fechamento_normal"))
    if not rel["verificada"]:
        return dict(entrada, estado="nao_publicado", motivo="nao_verificada:" + ",".join(rel["divergencias"]))
    try:
        r = compactar(Path(pasta), Path(staging_xz) / f"{nome}.tar.xz", nome)
        publicar(Path(staging_xz) / f"{nome}.tar.xz", Path(destino_dia), r["sha256_xz"])
    except (FechamentoError, OSError, tarfile.TarError, lzma.LZMAError, ValueError) as erro:
        return dict(entrada, estado="nao_publicado",
                    motivo=str(erro) if isinstance(erro, FechamentoError) else type(erro).__name__)
    st = r["manifesto"].get("status") or {}
    entrada.update(bolsa=st.get("exchange"), contrato_resolvido=st.get("requested_symbol"), quadros=r["quadros"],
                   bytes_brutos=r["bytes_brutos"], bytes_xz=r["bytes_xz"], sha256_xz=r["sha256_xz"],
                   sha256_manifesto=r["sha256_manifesto"], relogio=_desvio(r["manifesto"]),
                   estado="publicado" if rel.get("fechamento_normal") else "publicado_dia_interrompido",
                   razao=round(r["bytes_brutos"] / r["bytes_xz"], 2) if r["bytes_xz"] else None)
    ativo = ((st.get("sessao") or {}).get("ativo")) or {}
    if ativo.get("motivo"):
        entrada["motivo"] = ativo["motivo"]
    entrada["profundidade"] = ("mbo_e_negocios" if ativo.get("mbo") == "ok" and ativo.get("negocios") == "ok" else
                               "so_negocios" if ativo.get("negocios") == "ok" else
                               "so_mbo" if ativo.get("mbo") == "ok" else ativo.get("profundidade", "mbo_e_negocios"))
    return entrada


def fechar_dia(dia: str, staging_dia: Path, destino_raiz: Path, *, ativos: list, captura_es: Optional[Path],
               prova_es: Optional[dict], processos: int = 8, selado_dia: Optional[Path] = None) -> dict:
    """`ativos` = status_multi()['ativos'] da sessao do dia. Devolve o INDEX escrito."""
    staging_dia, destino_dia = Path(staging_dia), Path(destino_raiz) / dia
    if (destino_dia / "INDEX.json").exists():
        raise FechamentoError("dia_ja_fechado")
    staging_xz = staging_dia / ".xz"
    staging_xz.mkdir(parents=True, exist_ok=True)
    es_destino = Path(selado_dia) if selado_dia is not None else destino_dia
    tarefas, entradas = [], []
    for a in ativos:
        base = {"bolsa": a.get("bolsa"), "simbolo_pedido": a["exibido"], "contrato_resolvido": a.get("contrato"),
                "descricao": a.get("descricao"), "tentativas": a.get("tentativas"), "parte": a.get("parte", "p1")}
        reserva = a["exibido"] == "ES"  # o ES so entra no coletor como reserva do agendador
        if reserva:
            base.update(origem=ORIGEM_RESERVA, captura_id=a.get("captura_id"))
        if a.get("captura_id") and (staging_dia / a["captura_id"] / "manifest.json").is_file():
            nome = nome_da_reserva(a["captura_id"], a.get("bolsa"), a.get("contrato")) if reserva else a["captura_id"]
            tarefas.append((str(staging_dia / a["captura_id"]), nome, str(staging_xz),
                            str(es_destino if reserva else destino_dia), base))
        else:
            entradas.append(dict(base, estado="excluido" if a.get("estado") == "excluido" else "sem_captura",
                                 motivo=a.get("motivo") or a.get("estado")))
    if captura_es is not None and (Path(captura_es) / "manifest.json").is_file():
        m = json.loads((Path(captura_es) / "manifest.json").read_text(encoding="utf-8"))
        st = m.get("status") or {}
        nome = f"{st.get('exchange')}_{st.get('requested_symbol')}"
        tarefas.append((str(captura_es), nome, str(staging_xz), str(es_destino),
                        {"bolsa": st.get("exchange"), "simbolo_pedido": "ES", "contrato_resolvido": st.get("requested_symbol"),
                         "origem": "copia_da_captura_do_agendador", "prova_agendador": prova_es}))
    else:
        entradas.append({"simbolo_pedido": "ES", "origem": "copia_da_captura_do_agendador", "estado": "sem_captura",
                         "motivo": (prova_es or {}).get("resultado", "captura_do_es_ausente"), "prova_agendador": prova_es})
    # spawn, nao fork: o coletor chama isto de uma thread do asyncio, e fork com threads pode travar o filho
    with ProcessPoolExecutor(max_workers=max(1, min(processos, len(tarefas) or 1)),
                             mp_context=multiprocessing.get_context("spawn")) as pool:
        resultados = list(pool.map(_um, tarefas))
    selados = []
    for entrada in resultados:
        if selado_dia is not None and entrada.get("origem"):
            # Janela selada: o ES (do agendador e da reserva) vai com INDEX proprio para a pasta selada; o INDEX do
            # dia so aponta, sem numeros.
            selados.append(entrada)
            entradas.append({"simbolo_pedido": "ES", "origem": entrada["origem"], "motivo": "janela_selada_do_pre_registro",
                             "estado": "selado" if entrada["estado"].startswith("publicado") else f"selado_{entrada['estado']}",
                             "local": f"{Path(selado_dia).parent.name}/{dia}/{entrada['arquivo']}"})
        else:
            entradas.append(entrada)
    if selados:
        escrever_index(Path(selado_dia), dia, selados, selado=True)
    return escrever_index(destino_dia, dia, entradas)


def escrever_index(pasta: Path, dia: str, entradas: list, *, selado: bool = False) -> dict:
    """INDEX.json e SHA256SUMS.txt (atomicos, nessa ordem) a partir das entradas dos ativos."""
    entradas = sorted(entradas, key=lambda e: (e.get("bolsa") or "~", e.get("contrato_resolvido") or e["simbolo_pedido"],
                                               e.get("parte") or "p1"))
    publicados = [e for e in entradas if str(e.get("estado", "")).startswith("publicado")]
    index = {"schema": ESQUEMA_INDEX, "dia": dia, "gerado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "ambiente": "Rithmic Paper Trading", "research_only": True,
             "resumo": {"ativos": len(entradas), "publicados": len(publicados),
                        "bytes_brutos": sum(e.get("bytes_brutos") or 0 for e in publicados),
                        "bytes_xz": sum(e.get("bytes_xz") or 0 for e in publicados)},
             "ativos": entradas}
    if selado:
        index["selado"] = "janela de teste do pre-registro: nao olhar antes da data da pasta"
    pasta.mkdir(parents=True, exist_ok=True)
    texto_index = json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    _atomico(pasta / "INDEX.json", texto_index)
    linhas = [f"{e['sha256_xz']}  {e['arquivo']}" for e in sorted(publicados, key=lambda e: e["arquivo"])]
    linhas.append(f"{hashlib.sha256(texto_index.encode('utf-8')).hexdigest()}  INDEX.json")
    _atomico(pasta / "SHA256SUMS.txt", "\n".join(linhas) + "\n")
    return index


def _somas(arquivo: Path) -> dict:
    saida = {}
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        sha, _, nome = linha.partition("  ")
        saida[nome] = sha
    return saida


def liberar_selados(destino_raiz: Path, selado_raiz: Path, hoje, liberar_em) -> list:
    """A partir de `liberar_em`, move cada dia selado para <destino>/<dia>/ conferindo o SHA-256 e atualizando o
    INDEX e o SHA256SUMS do dia. A pasta selada so some depois da copia conferida. Devolve [(dia, resultado)]."""
    selado_raiz, saida = Path(selado_raiz), []
    if hoje < liberar_em or not selado_raiz.is_dir():
        return saida
    for pasta in sorted(p for p in selado_raiz.iterdir() if p.is_dir() and len(p.name) == 10):
        try:
            somas = _somas(pasta / "SHA256SUMS.txt")
            texto = (pasta / "INDEX.json").read_bytes()
            if somas.get("INDEX.json") != hashlib.sha256(texto).hexdigest():
                raise FechamentoError("index_selado_diverge")
            selados = json.loads(texto)["ativos"]
            destino_dia = Path(destino_raiz) / pasta.name
            publicados = [e for e in selados if str(e.get("estado", "")).startswith("publicado")]
            # 1a passada: confere tudo antes de mover qualquer arquivo (o ES do agendador e as partes da reserva)
            for e in publicados:
                origem, final = pasta / e["arquivo"], destino_dia / e["arquivo"]
                if somas.get(e["arquivo"]) != e["sha256_xz"] or (origem.exists() and _sha(origem) != e["sha256_xz"]):
                    raise FechamentoError("xz_selado_diverge")
                if not origem.exists() and not final.exists():
                    raise FechamentoError("xz_selado_ausente")
                if final.exists() and _sha(final) != e["sha256_xz"]:  # nunca sobrescreve um arquivo diferente
                    raise FechamentoError("destino_ja_existe_e_diverge")
            # a pasta selada some no fim: nenhum .tar.xz dela pode ficar de fora do INDEX
            listados = {e["arquivo"] for e in publicados}
            if any(p.name.endswith(".tar.xz") and p.name not in listados for p in pasta.iterdir()):
                raise FechamentoError("arquivo_nao_listado_na_pasta_selada")
            # 2a passada: mesmo disco (as duas pastas estao no DataSet): move, sem duplicar, e confere no destino
            for e in publicados:
                final = destino_dia / e["arquivo"]
                if not final.exists():
                    destino_dia.mkdir(parents=True, exist_ok=True)
                    os.replace(pasta / e["arquivo"], final)
                    if _sha(final) != e["sha256_xz"]:
                        raise FechamentoError("movido_diverge")
            principal = destino_dia / "INDEX.json"
            atuais = json.loads(principal.read_text(encoding="utf-8"))["ativos"] if principal.is_file() else []
            atuais = [a for a in atuais if not (a.get("origem") and str(a.get("estado", "")).startswith("selado"))]
            escrever_index(destino_dia, pasta.name, atuais + [dict(e, liberado_de=selado_raiz.name) for e in selados])
            shutil.rmtree(pasta)
            saida.append((pasta.name, "liberado"))
        except (FechamentoError, OSError, ValueError, KeyError) as erro:
            saida.append((pasta.name, str(erro) if isinstance(erro, FechamentoError) else type(erro).__name__))
    return saida
