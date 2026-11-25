"""Arquivador de capturas: comprime o pregao fechado, confere pelo manifesto e nunca perde um byte.

O script mora em operacao/windows (roda no WSL com python3 -I, so biblioteca padrao) e nao entra em
src/qhos2: nao muda a identidade do codigo do pre-registro. Estes testes usam o gravador real do projeto
para fabricar capturas fechadas e o catalogo real para provar que a captura restaurada continua valida.
"""
import hashlib
import importlib.util
import json
import lzma
import os
import sys
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from qhos2 import catalogo
from qhos2.gravador import Gravador
from qhos2.operacao import ler_identidade_captura

CAMINHO = Path(__file__).resolve().parents[1] / "operacao" / "windows" / "arquivar_capturas.py"
ARQ, EST = "capturas_arquivadas", ".arquivador"
ARQUIVOS = ("market_frames.jsonl", "manifest.json", "capture_identity.json")
ARQUIVO_FINAL = ["ARQUIVO.json", "capture_identity.json", "manifest.json", "market_frames.jsonl.xz"]
IDENT = {"system_name": "Rithmic Paper Trading", "requested_symbol": "ESZ6", "exchange": "CME"}
SSBOE = 1_760_000_000
STATUS = {"estado": "FECHADA", "logout_confirmado": True}
AGORA = datetime.now(timezone.utc)
MEIA_HORA = AGORA + timedelta(minutes=30)
DUAS_HORAS = AGORA + timedelta(hours=2)
TRES_DIAS = AGORA + timedelta(days=3)
DEPOIS = AGORA + timedelta(days=10)


@pytest.fixture(scope="module")
def arq():
    spec = importlib.util.spec_from_file_location("arquivar_capturas_sob_teste", CAMINHO)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _v(n):
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def _quadro(i, template):
    dados = _v(154467 << 3) + _v(template) + _v(150100 << 3) + _v(SSBOE) + _v(150101 << 3) + _v(i)
    return dados, SSBOE * 10 ** 9 + i * 1000 + 70_000_000


def fazer(raiz, nome, quadros=300):
    """Captura fechada de verdade: gravador real, manifesto e identidade reais."""
    gravador = Gravador(raiz / "capturas", identidade=IDENT, captura_id=nome)
    for i in range(quadros):
        template = (116, 160, 161)[i % 3]
        dados, ns = _quadro(i, template)
        gravador(dados, template, ns)
    gravador.fechar(STATUS, fechamento_normal=True)
    return raiz / "capturas" / nome


def sha(caminho):
    return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()


def foto(raiz):
    saida = {}
    for p in sorted(Path(raiz).rglob("*")):
        rel = p.relative_to(raiz).as_posix()
        saida[rel] = sha(p) if p.is_file() else None
    return saida


def pasta_arq(base, id_):
    """A pasta do arquivo de `id_` em `base` (ou numa subpasta selada); o nome e AAAA-MM-DD_<id>."""
    achadas = [p for p in Path(base).rglob("*_" + id_) if p.is_dir() and not p.name.startswith(".")]
    assert len(achadas) == 1, f"esperava 1 pasta para {id_} em {base}, achei {achadas}"
    return achadas[0]


def sem_arq(base, id_):
    return not [p for p in Path(base).rglob("*_" + id_) if p.is_dir()] if Path(base).exists() else True


def dia_do_pregao(pasta):
    """Data de Brasilia do fechamento, lida do manifesto (e o prefixo do nome da pasta arquivada)."""
    criado = json.loads((Path(pasta) / "manifest.json").read_text())["status"]["criado_em"]
    return datetime.fromisoformat(criado).astimezone(timezone(timedelta(hours=-3))).date().isoformat()


def dataset(tmp_path):
    pasta = tmp_path / "DataSet"
    pasta.mkdir()
    return pasta


def adulterar(caminho):
    dados = bytearray(Path(caminho).read_bytes())
    dados[len(dados) // 2] ^= 0xFF
    Path(caminho).write_bytes(bytes(dados))


# ------------------------------------------------------------------ estados e simulacao

def test_listar_classifica_cada_pasta(arq, tmp_path):
    fazer(tmp_path, "velha")
    aberta = fazer(tmp_path, "aberta")
    (aberta / "manifest.json").unlink()  # captura em curso: ainda sem manifesto
    estranha = fazer(tmp_path, "estranha")
    (estranha / "sobra.txt").write_text("x")
    estados = {c["id"]: (c["estado"], c["motivo"]) for c in arq.listar(tmp_path, agora=DEPOIS)}
    assert estados == {"velha": ("elegivel", None), "aberta": ("aberta", None),
                       "estranha": ("ignorada", "conteudo_inesperado")}
    assert {c["id"]: c["estado"] for c in arq.listar(tmp_path, agora=MEIA_HORA)}["velha"] == "recente"


def test_estados_de_quem_ja_tem_arquivo(arq, tmp_path):
    fazer(tmp_path, "c1")
    arq.arquivar(tmp_path, executar=True, manter_original=True, agora=DUAS_HORAS)
    assert [(c["estado"], c["motivo"]) for c in arq.listar(tmp_path, agora=TRES_DIAS)] == [("com_original", "no_prazo")]
    assert [(c["estado"], c["motivo"]) for c in arq.listar(tmp_path, agora=DEPOIS)] == [("com_original", "vencida")]


def test_simulacao_nao_muda_nada(arq, tmp_path):
    fazer(tmp_path, "c1")
    antes = foto(tmp_path)
    relatorio = arq.arquivar(tmp_path, executar=False, agora=DEPOIS)
    assert relatorio["simulacao"] is True
    assert relatorio["arquivadas"] == [] and relatorio["elegiveis"] == ["c1"]
    assert foto(tmp_path) == antes
    assert not (tmp_path / ARQ).exists() and not (tmp_path / EST).exists()


def test_nao_mexe_em_captura_fechada_ha_menos_de_uma_hora(arq, tmp_path):
    fazer(tmp_path, "c1")
    antes = foto(tmp_path)
    relatorio = arq.arquivar(tmp_path, executar=True, agora=MEIA_HORA)
    assert relatorio["recentes"] == ["c1"] and relatorio["arquivadas"] == []
    assert foto(tmp_path) == antes


def test_captura_aberta_nunca_e_tocada(arq, tmp_path):
    fazer(tmp_path, "velha")
    aberta = fazer(tmp_path, "aberta")
    (aberta / "manifest.json").unlink()
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == ["velha"] and "aberta" in relatorio["abertas"]
    assert (tmp_path / "capturas" / "aberta" / "market_frames.jsonl").is_file()


# ------------------------------------------------------------------ arquivar, publicar e liberar disco

def test_arquivar_move_a_pasta_e_o_xz_reproduz_os_bytes(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    antes = {n: sha(pasta / n) for n in ARQUIVOS}
    manifesto = json.loads((pasta / "manifest.json").read_text())
    dia = dia_do_pregao(pasta)
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == ["c1"] and relatorio["removidas"] == ["c1"]
    assert not pasta.exists()
    destino = tmp_path / ARQ / f"{dia}_c1"
    assert sorted(p.name for p in destino.iterdir()) == ARQUIVO_FINAL
    with lzma.open(destino / "market_frames.jsonl.xz") as f:
        bruto = f.read()
    assert hashlib.sha256(bruto).hexdigest() == manifesto["sha256"] == antes["market_frames.jsonl"]
    assert len(bruto) == manifesto["bytes"]
    for nome in ("manifest.json", "capture_identity.json"):
        assert sha(destino / nome) == antes[nome]
    lateral = json.loads((destino / "ARQUIVO.json").read_text())
    assert lateral["captura_id"] == "c1" and lateral["data_pregao"] == dia
    assert lateral["original"]["sha256"] == manifesto["sha256"]
    assert lateral["original"]["bytes"] == manifesto["bytes"] and lateral["original"]["frames"] == manifesto["frames"]
    assert lateral["comprimido"]["bytes"] == (destino / "market_frames.jsonl.xz").stat().st_size
    assert lateral["comprimido"]["sha256"] == sha(destino / "market_frames.jsonl.xz")
    assert lateral["comprimido"]["bytes"] < manifesto["bytes"]
    assert "c1" not in [p.name for p in catalogo.listar_capturas(tmp_path / "capturas")]
    assert not list((tmp_path / EST / "parcial").glob("*"))


def test_publica_no_mesmo_dia_guarda_o_original_e_libera_depois_do_prazo(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DUAS_HORAS)
    assert relatorio["arquivadas"] == ["c1"] and relatorio["removidas"] == []
    assert pasta.is_dir() and (pasta_arq(tmp_path / ARQ, "c1") / "ARQUIVO.json").is_file()
    relatorio = arq.arquivar(tmp_path, executar=True, agora=TRES_DIAS)  # no prazo: nada a fazer
    assert relatorio["arquivadas"] == [] and relatorio["removidas"] == [] and pasta.is_dir()
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)  # venceu: tira a pasta bruta
    assert relatorio["removidas"] == ["c1"] and not pasta.exists()
    assert arq.verificar_arquivadas(tmp_path) == {"ok": ["c1"], "divergentes": []}


def test_manter_original_nunca_remove(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    relatorio = arq.arquivar(tmp_path, executar=True, manter_original=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == ["c1"] and relatorio["removidas"] == []
    relatorio = arq.arquivar(tmp_path, executar=True, manter_original=True, agora=DEPOIS)
    assert relatorio["removidas"] == [] and pasta.is_dir()
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)  # sem o freio, a vencida sai
    assert relatorio["removidas"] == ["c1"] and not pasta.exists()


def test_vencida_nao_sai_se_o_arquivo_estiver_ruim(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    arq.arquivar(tmp_path, executar=True, agora=DUAS_HORAS)
    adulterar(pasta_arq(tmp_path / ARQ, "c1") / "market_frames.jsonl.xz")
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["removidas"] == [] and [d["id"] for d in relatorio["divergentes"]] == ["c1"]
    assert (pasta / "market_frames.jsonl").is_file()


def test_arquivo_bruto_adulterado_nao_e_arquivado(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    bruto = pasta / "market_frames.jsonl"
    dados = bytearray(bruto.read_bytes())
    dados[50] ^= 0x01  # mesmo tamanho, um bit trocado
    bruto.write_bytes(bytes(dados))
    antes = {k: v for k, v in foto(tmp_path).items() if k.startswith("capturas/")}
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == []
    assert [d["id"] for d in relatorio["divergentes"]] == ["c1"]
    assert relatorio["divergentes"][0]["motivo"] == "sha256_diverge_do_manifesto"
    depois = foto(tmp_path)
    assert {k: v for k, v in depois.items() if k.startswith("capturas/")} == antes  # nada apagado nem alterado
    assert not [k for k in depois if k.startswith(ARQ + "/")]  # nada publicado
    assert not [k for k in depois if k.startswith(EST + "/parcial/")]  # nada meio feito
    diario = [json.loads(l) for l in (tmp_path / EST / "arquivamento.jsonl").read_text().splitlines()]
    assert [(d["acao"], d["id"], d["motivo"]) for d in diario] == [("divergente", "c1", "sha256_diverge_do_manifesto")]


def test_conteudo_inesperado_e_link_ficam_de_fora(arq, tmp_path):
    estranha = fazer(tmp_path, "estranha")
    (estranha / "sobra.txt").write_text("x")
    real = fazer(tmp_path, "real")
    try:
        os.symlink(real, tmp_path / "capturas" / "atalho", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("sem permissao para criar link simbolico")
    antes = foto(tmp_path / "capturas" / "estranha")
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    motivos = {i["id"]: i["motivo"] for i in relatorio["ignoradas"]}
    assert motivos["estranha"] == "conteudo_inesperado" and motivos["atalho"] == "link_simbolico"
    assert relatorio["arquivadas"] == ["real"]
    assert foto(tmp_path / "capturas" / "estranha") == antes
    assert (tmp_path / "capturas" / "atalho").is_symlink()


def test_guarda_em_andamento_suspende_tudo(arq, tmp_path):
    fazer(tmp_path, "c1")
    guarda = tmp_path / "estado.json"
    guarda.write_text(json.dumps({"schema": "qhos2_operacao_v1", "estado": "EM_ANDAMENTO", "geracao": "x"}))
    antes = foto(tmp_path)
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS, guarda=guarda)
    assert relatorio["parada"] == "captura_em_andamento" and relatorio["arquivadas"] == []
    assert foto(tmp_path) == antes
    guarda.write_text(json.dumps({"schema": "qhos2_operacao_v1", "estado": "LIVRE"}))
    assert arq.arquivar(tmp_path, executar=True, agora=DEPOIS, guarda=guarda)["arquivadas"] == ["c1"]


def test_falha_ao_comprimir_preserva_o_original(arq, tmp_path, monkeypatch):
    fazer(tmp_path, "c1")

    def falha(*_a, **_k):
        raise OSError("disco cheio")

    monkeypatch.setattr(arq, "_comprimir", falha)
    antes = foto(tmp_path)
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == [] and relatorio["divergentes"][0]["motivo"] == "falha_ao_arquivar"
    depois = foto(tmp_path)
    assert {k: v for k, v in depois.items() if k.startswith("capturas/")} == \
        {k: v for k, v in antes.items() if k.startswith("capturas/")}
    assert not [k for k in depois if k.startswith(ARQ + "/")]
    assert not [k for k in depois if k.startswith(EST + "/parcial/")]


def test_sem_espaco_para_o_arquivo_nao_tenta(arq, tmp_path, monkeypatch):
    fazer(tmp_path, "c1")
    uso = namedtuple("uso", "total used free")
    monkeypatch.setattr(arq.shutil, "disk_usage", lambda _p: uso(10 ** 12, 10 ** 12 - 1024, 1024))
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == [] and relatorio["ignoradas"][0]["motivo"] == "sem_espaco"
    assert (tmp_path / "capturas" / "c1" / "market_frames.jsonl").is_file()


def test_resto_de_execucao_interrompida_e_limpo(arq, tmp_path):
    fazer(tmp_path, "c1")
    lixo = tmp_path / EST / "parcial" / "c1"
    lixo.mkdir(parents=True)
    (lixo / "market_frames.jsonl.xz").write_bytes(b"meio arquivo")
    outro = tmp_path / EST / "restaurando" / "x"
    outro.mkdir(parents=True)
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == ["c1"] and not lixo.exists() and not outro.exists()


def test_segunda_rodada_nao_faz_nada(arq, tmp_path):
    fazer(tmp_path, "c1")
    arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    antes = foto(tmp_path)
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == [] and relatorio["removidas"] == []
    assert foto(tmp_path) == antes  # sem trabalho, nao escreve nem o diario


def test_rodada_concorrente_e_recusada(arq, tmp_path):
    fcntl = pytest.importorskip("fcntl")
    fazer(tmp_path, "c1")
    (tmp_path / EST).mkdir()
    with open(tmp_path / EST / "trava", "w") as trava:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
        relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["parada"] == "ocupado" and relatorio["arquivadas"] == []
    assert (tmp_path / "capturas" / "c1").is_dir()


# ------------------------------------------------------------------ restaurar e verificar

def test_restaurar_devolve_a_captura_identica_e_valida(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    antes = {n: sha(pasta / n) for n in ARQUIVOS}
    manifesto = json.loads((pasta / "manifest.json").read_text())
    arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    restaurada = arq.restaurar(tmp_path, "c1")
    assert restaurada == tmp_path / "capturas" / "c1"
    assert {n: sha(restaurada / n) for n in ARQUIVOS} == antes
    assert catalogo.verificar_captura(restaurada)["verificada"] is True
    ler_identidade_captura(restaurada, manifesto=manifesto)  # inode novo nao atrapalha captura encerrada
    assert (pasta_arq(tmp_path / ARQ, "c1") / "ARQUIVO.json").is_file()  # restaurar nao gasta o arquivo
    with pytest.raises(arq.ArquivamentoError, match="destino_ja_existe"):
        arq.restaurar(tmp_path, "c1")


def test_rodada_diaria_nao_apaga_a_captura_restaurada_de_proposito(arq, tmp_path):
    fazer(tmp_path, "c1")
    arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    restaurada = arq.restaurar(tmp_path, "c1")
    antes = foto(restaurada)
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS + timedelta(days=30))
    assert relatorio["removidas"] == [] and relatorio["com_original"] == ["c1"] and relatorio["vencidas"] == []
    assert foto(restaurada) == antes
    relatorio = arq.arquivar(tmp_path, executar=True, recuperar=True, agora=DEPOIS)  # terminou de usar
    assert relatorio["removidas"] == ["c1"] and not restaurada.exists()
    assert not (tmp_path / EST / "restauradas" / "c1.json").exists()


def test_recuperar_tira_a_pasta_mesmo_no_prazo(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    arq.arquivar(tmp_path, executar=True, agora=DUAS_HORAS)
    assert pasta.is_dir()
    relatorio = arq.arquivar(tmp_path, executar=True, recuperar=True, agora=DUAS_HORAS)
    assert relatorio["removidas"] == ["c1"] and not pasta.exists()


def test_restaurar_em_outra_pasta(arq, tmp_path):
    fazer(tmp_path, "c1")
    arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    aux = tmp_path / "avaliacao"
    restaurada = arq.restaurar(tmp_path, "c1", para=aux)
    assert restaurada == aux / "c1"
    assert catalogo.verificar_captura(restaurada)["verificada"] is True
    assert not (tmp_path / "capturas" / "c1").exists()
    assert not (tmp_path / EST / "restauradas" / "c1.json").exists()  # fora de capturas/ nao precisa de marca


def test_verificar_acusa_arquivo_adulterado(arq, tmp_path):
    fazer(tmp_path, "boa")
    fazer(tmp_path, "ruim")
    arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert arq.verificar_arquivadas(tmp_path) == {"ok": ["boa", "ruim"], "divergentes": []}
    adulterar(pasta_arq(tmp_path / ARQ, "ruim") / "market_frames.jsonl.xz")
    resultado = arq.verificar_arquivadas(tmp_path)
    assert resultado["ok"] == ["boa"] and [d["id"] for d in resultado["divergentes"]] == ["ruim"]
    with pytest.raises(arq.ArquivamentoError):
        arq.restaurar(tmp_path, "ruim")
    assert not (tmp_path / "capturas" / "ruim").exists() and not list((tmp_path / "capturas").glob("*.restaurando"))


def _xz_valido_mas_diferente(arq, monkeypatch):
    """Compressor falho: devolve os hashes do original, mas grava um xz valido de OUTRO conteudo."""
    real = arq._comprimir

    def falho(origem, destino):
        resultado = real(origem, destino)
        Path(destino).write_bytes(lzma.compress(b"conteudo diferente\n" * 50))
        return resultado

    monkeypatch.setattr(arq, "_comprimir", falho)


def test_xz_valido_de_outro_conteudo_nao_e_publicado(arq, tmp_path, monkeypatch):
    pasta = fazer(tmp_path, "c1")
    _xz_valido_mas_diferente(arq, monkeypatch)
    relatorio = arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == []
    assert relatorio["divergentes"][0]["motivo"] == "arquivo_comprimido_nao_confere"
    assert (pasta / "market_frames.jsonl").is_file()
    assert sem_arq(tmp_path / ARQ, "c1") and not list((tmp_path / EST / "parcial").glob("*"))


def test_restaurar_reconfere_o_conteudo_mesmo_sem_a_verificacao_previa(arq, tmp_path, monkeypatch):
    fazer(tmp_path, "c1")
    arq.arquivar(tmp_path, executar=True, agora=DEPOIS)
    (pasta_arq(tmp_path / ARQ, "c1") / "market_frames.jsonl.xz").write_bytes(lzma.compress(b"conteudo diferente\n" * 50))
    monkeypatch.setattr(arq, "_verificar_uma", lambda *_a, **_k: None)  # finge que a verificacao previa passou
    with pytest.raises(arq.ArquivamentoError, match="conteudo_nao_confere"):
        arq.restaurar(tmp_path, "c1")
    assert not (tmp_path / "capturas" / "c1").exists()
    assert not list((tmp_path / EST / "restaurando").glob("*"))


# ------------------------------------------------------------------ destino fora do disco das capturas (a pasta DataSet)

def test_destino_em_outro_disco_copia_confere_e_deixa_o_marcador_por_ultimo(arq, tmp_path, monkeypatch):
    fazer(tmp_path, "c1")
    pasta_dados = dataset(tmp_path)
    monkeypatch.setattr(arq, "_mesmo_disco", lambda *_a: False)  # como o /mnt/c visto do WSL
    ordem = []
    real = arq._copiar_arquivo

    def espiao(origem, destino):
        ordem.append(Path(destino).name)
        return real(origem, destino)

    monkeypatch.setattr(arq, "_copiar_arquivo", espiao)
    relatorio = arq.arquivar(tmp_path, destino=pasta_dados, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == ["c1"] and relatorio["removidas"] == ["c1"]
    assert ordem[-1] == "ARQUIVO.json" and set(ordem) == set(ARQUIVO_FINAL)
    assert sorted(p.name for p in pasta_arq(pasta_dados, "c1").iterdir()) == ARQUIVO_FINAL
    assert not (tmp_path / ARQ).exists()  # o padrao nao e usado quando ha destino
    assert not list((tmp_path / EST / "parcial").glob("*"))
    assert arq.verificar_arquivadas(tmp_path, destino=pasta_dados) == {"ok": ["c1"], "divergentes": []}
    restaurada = arq.restaurar(tmp_path, "c1", destino=pasta_dados)
    assert catalogo.verificar_captura(restaurada)["verificada"] is True


def test_destino_inexistente_nao_e_criado(arq, tmp_path):
    fazer(tmp_path, "c1")
    antes = foto(tmp_path)
    with pytest.raises(arq.ArquivamentoError, match="destino_inexistente"):
        arq.arquivar(tmp_path, destino=tmp_path / "DataSet_com_erro_de_digitacao", executar=True, agora=DEPOIS)
    assert foto(tmp_path) == antes and not (tmp_path / "DataSet_com_erro_de_digitacao").exists()


def test_copia_corrompida_no_destino_e_detectada(arq, tmp_path, monkeypatch):
    pasta = fazer(tmp_path, "c1")
    pasta_dados = dataset(tmp_path)
    monkeypatch.setattr(arq, "_mesmo_disco", lambda *_a: False)
    real = arq._copiar_arquivo

    def corrompe_depois(origem, destino):
        real(origem, destino)
        if Path(destino).name == "market_frames.jsonl.xz":
            adulterar(destino)  # o que o disco de rede ou a sincronizacao poderiam fazer

    monkeypatch.setattr(arq, "_copiar_arquivo", corrompe_depois)
    relatorio = arq.arquivar(tmp_path, destino=pasta_dados, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == [] and relatorio["removidas"] == []
    assert relatorio["divergentes"][0]["motivo"].startswith("destino_nao_confere_")
    assert (pasta / "market_frames.jsonl").is_file()
    assert list(pasta_dados.iterdir()) == []  # nada fica no destino
    assert not list((tmp_path / EST / "parcial").glob("*"))


def test_pasta_incompleta_no_destino_e_refeita(arq, tmp_path):
    fazer(tmp_path, "c1")
    pasta_dados = dataset(tmp_path)
    incompleta = pasta_dados / f"{dia_do_pregao(tmp_path / 'capturas' / 'c1')}_c1"
    incompleta.mkdir()
    (incompleta / "market_frames.jsonl.xz").write_bytes(b"corte no meio da copia")  # sem ARQUIVO.json
    relatorio = arq.arquivar(tmp_path, destino=pasta_dados, executar=True, agora=DEPOIS)
    assert relatorio["arquivadas"] == ["c1"]
    assert arq.verificar_arquivadas(tmp_path, destino=pasta_dados) == {"ok": ["c1"], "divergentes": []}


def test_pasta_do_usuario_no_destino_nao_e_tocada_nem_listada(arq, tmp_path):
    fazer(tmp_path, "c1")
    pasta_dados = dataset(tmp_path)
    (pasta_dados / "notas").mkdir()
    (pasta_dados / "notas" / "ideias.txt").write_text("meus experimentos")
    (pasta_dados / "LEIA-ME.txt").write_text("x")
    antes = foto(pasta_dados)
    arq.arquivar(tmp_path, destino=pasta_dados, executar=True, agora=DEPOIS)
    depois = foto(pasta_dados)
    assert {k: v for k, v in depois.items() if k.startswith(("notas", "LEIA"))} == antes
    assert arq.verificar_arquivadas(tmp_path, destino=pasta_dados) == {"ok": ["c1"], "divergentes": []}
    assert arq.resumo_arquivadas(tmp_path, destino=pasta_dados)["total"] == 1


# ------------------------------------------------------------------ nome com a data e publicacao de um pregao so

def test_nome_da_pasta_leva_a_data_do_pregao_e_a_restauracao_volta_ao_id_original(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    dia = dia_do_pregao(pasta)
    arq.arquivar(tmp_path, executar=True, manter_original=True, agora=DUAS_HORAS)
    assert [p.name for p in (tmp_path / ARQ).iterdir()] == [f"{dia}_c1"]
    assert [c["estado"] for c in arq.listar(tmp_path, agora=DUAS_HORAS)] == ["com_original"]  # acha pelo id, nao pelo nome
    assert arq.verificar_arquivadas(tmp_path) == {"ok": ["c1"], "divergentes": []}
    arq.arquivar(tmp_path, executar=True, agora=DEPOIS)  # vence e sai de capturas/
    restaurada = arq.restaurar(tmp_path, "c1")
    assert restaurada.name == "c1" and catalogo.verificar_captura(restaurada)["verificada"] is True  # o nome da captura nao muda


def test_data_do_nome_e_a_do_fechamento_em_brasilia_nao_a_do_relogio_nem_a_utc(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    caminho = pasta / "manifest.json"
    manifesto = json.loads(caminho.read_text())
    manifesto["status"]["criado_em"] = "2026-10-21T01:30:00+00:00"  # 22:30 de 20/10 em Brasilia, ja 21/10 em UTC
    caminho.write_text(json.dumps(manifesto, indent=2, sort_keys=True) + "\n")
    relatorio = arq.arquivar(tmp_path, executar=True, manter_original=True, agora=datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc))
    assert relatorio["arquivadas"] == ["c1"]
    assert [p.name for p in (tmp_path / ARQ).iterdir()] == ["2026-10-20_c1"]
    assert json.loads((tmp_path / ARQ / "2026-10-20_c1" / "ARQUIVO.json").read_text())["data_pregao"] == "2026-10-20"


def test_so_o_pedido_e_publicado(arq, tmp_path):
    fazer(tmp_path, "c1")
    fazer(tmp_path, "c2")
    antes_c1 = foto(tmp_path / "capturas" / "c1")
    relatorio = arq.arquivar(tmp_path, executar=True, manter_original=True, agora=DUAS_HORAS, apenas=["c2"])
    assert relatorio["arquivadas"] == ["c2"] and relatorio["elegiveis"] == ["c2"]
    assert sem_arq(tmp_path / ARQ, "c1") and foto(tmp_path / "capturas" / "c1") == antes_c1
    assert [c["id"] for c in arq.listar(tmp_path, agora=DUAS_HORAS) if c["estado"] == "elegivel"] == ["c1"]


def test_so_com_id_que_nao_existe_e_recusado(arq, tmp_path):
    fazer(tmp_path, "c1")
    antes = foto(tmp_path)
    with pytest.raises(arq.ArquivamentoError, match="captura_inexistente"):
        arq.arquivar(tmp_path, executar=True, agora=DEPOIS, apenas=["c1", "c9"])
    assert foto(tmp_path) == antes


def test_linha_de_comando_so(arq, tmp_path):
    fazer(tmp_path, "c1")
    fazer(tmp_path, "c2")
    codigo = arq.main(["arquivar", "--raiz", str(tmp_path), "--executar", "--manter-original", "--so", "c1",
                       "--agora", DUAS_HORAS.isoformat(), "--guarda", str(tmp_path / "sem-guarda.json")])
    assert codigo == 0 and not sem_arq(tmp_path / ARQ, "c1") and sem_arq(tmp_path / ARQ, "c2")


def _fechada_em(pasta, quando):
    caminho = Path(pasta) / "manifest.json"
    manifesto = json.loads(caminho.read_text())
    manifesto["status"]["criado_em"] = quando
    caminho.write_text(json.dumps(manifesto, indent=2, sort_keys=True) + "\n")


def test_desde_deixa_os_pregoes_antigos_de_fora(arq, tmp_path):
    antiga = fazer(tmp_path, "antiga")
    _fechada_em(antiga, "2026-10-20T20:13:00+00:00")  # 17:13 de 20/10 em Brasilia
    fazer(tmp_path, "nova")
    antes = foto(antiga)
    relatorio = arq.arquivar(tmp_path, executar=True, manter_original=True, agora=DUAS_HORAS, desde=date(2026, 10, 25))
    assert relatorio["arquivadas"] == ["nova"] and relatorio["elegiveis"] == ["nova"]
    assert sem_arq(tmp_path / ARQ, "antiga") and foto(antiga) == antes


def test_desde_inclui_o_proprio_dia_em_brasilia(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    _fechada_em(pasta, "2026-10-21T01:30:00+00:00")  # 22:30 de 20/10 em Brasilia
    agora = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    assert arq.arquivar(tmp_path, executar=False, agora=agora, desde=date(2026, 10, 21))["elegiveis"] == []
    assert arq.arquivar(tmp_path, executar=False, agora=agora, desde=date(2026, 10, 20))["elegiveis"] == ["c1"]


def test_linha_de_comando_desde(arq, tmp_path):
    antiga = fazer(tmp_path, "antiga")
    _fechada_em(antiga, "2026-10-20T20:13:00+00:00")
    fazer(tmp_path, "nova")
    codigo = arq.main(["arquivar", "--raiz", str(tmp_path), "--executar", "--manter-original", "--desde", "2026-10-25",
                       "--agora", DUAS_HORAS.isoformat(), "--guarda", str(tmp_path / "sem-guarda.json")])
    assert codigo == 0 and sem_arq(tmp_path / ARQ, "antiga") and not sem_arq(tmp_path / ARQ, "nova")
    assert arq.main(["arquivar", "--raiz", str(tmp_path), "--desde", "29/10/2026"]) == 2  # formato errado nao passa


def _conteudo_por_id(arq, destino):
    """{id: {arquivo: sha256}} de cada pasta COMPLETA do destino, esteja onde estiver (raiz ou selada)."""
    return {id_: {p.name: sha(p) for p in pasta.iterdir()} for id_, pasta in arq._entradas(destino)
            if (pasta / "ARQUIVO.json").is_file()}


def test_dataset_e_permanente_nenhuma_operacao_apaga_ou_altera_uma_pasta_completa(arq, tmp_path):
    """O DataSet e o armazem permanente: liberar disco, recuperar, restaurar, verificar e o selo cair nunca tiram nada dele."""
    for nome in ("c1", "c2", "c3"):
        fazer(tmp_path, nome)
    pasta_dados = dataset(tmp_path)
    _hoje, selo = _selo_cobrindo_hoje()
    arq.arquivar(tmp_path, destino=pasta_dados, executar=True, manter_original=True, agora=DUAS_HORAS, selo=selo)
    publicado = _conteudo_por_id(arq, pasta_dados)
    assert sorted(publicado) == ["c1", "c2", "c3"]
    depois_do_selo = AGORA + timedelta(days=10)
    passos = [
        lambda: arq.arquivar(tmp_path, destino=pasta_dados, executar=True, agora=depois_do_selo, selo=selo),  # libera disco e o selo cai
        lambda: arq.restaurar(tmp_path, "c1", destino=pasta_dados, agora=depois_do_selo),
        lambda: arq.arquivar(tmp_path, destino=pasta_dados, executar=True, recuperar=True, agora=depois_do_selo),
        lambda: arq.verificar_arquivadas(tmp_path, destino=pasta_dados),
        lambda: arq.arquivar(tmp_path, destino=pasta_dados, executar=True, agora=depois_do_selo + timedelta(days=400)),
    ]
    for passo in passos:
        passo()
        assert _conteudo_por_id(arq, pasta_dados) == publicado
    assert not list((tmp_path / "capturas").iterdir())  # os brutos sairam, o DataSet ficou inteiro


# ------------------------------------------------------------------ selo da janela de teste

def _selo_cobrindo_hoje():
    hoje = (AGORA.astimezone(timezone(timedelta(hours=-3)))).date()
    return hoje, {"de": hoje - timedelta(days=1), "ate": hoje + timedelta(days=1), "libera": hoje + timedelta(days=8)}


def test_pregao_na_janela_do_selo_vai_para_a_subpasta_e_nao_se_restaura(arq, tmp_path):
    fazer(tmp_path, "c1")
    pasta_dados = dataset(tmp_path)
    hoje, selo = _selo_cobrindo_hoje()
    relatorio = arq.arquivar(tmp_path, destino=pasta_dados, executar=True, agora=DEPOIS - timedelta(days=5), selo=selo)
    nome_selo = "SELADO_ate_" + selo["libera"].isoformat()
    assert relatorio["arquivadas"] == ["c1"]
    assert (pasta_arq(pasta_dados / nome_selo, "c1") / "ARQUIVO.json").is_file()
    assert not [p for p in pasta_dados.glob("*_c1")]  # nada na raiz do DataSet enquanto selado
    assert arq.verificar_arquivadas(tmp_path, destino=pasta_dados)["ok"] == ["c1"]
    assert arq.resumo_arquivadas(tmp_path, destino=pasta_dados)["seladas"] == 1
    with pytest.raises(arq.ArquivamentoError, match="captura_selada_ate_" + selo["libera"].isoformat()):
        arq.restaurar(tmp_path, "c1", destino=pasta_dados, agora=DEPOIS - timedelta(days=5))
    assert (tmp_path / "capturas" / "c1").is_dir()  # 5 dias: ainda no prazo, a pasta bruta continua em capturas/


def test_selo_cai_na_data_e_o_pregao_sobe_para_a_raiz(arq, tmp_path):
    fazer(tmp_path, "c1")
    pasta_dados = dataset(tmp_path)
    _hoje, selo = _selo_cobrindo_hoje()
    arq.arquivar(tmp_path, destino=pasta_dados, executar=True, manter_original=True, agora=DUAS_HORAS, selo=selo)
    nome_selo = pasta_dados / ("SELADO_ate_" + selo["libera"].isoformat())
    assert pasta_arq(nome_selo, "c1").is_dir()
    antes_da_data = AGORA + timedelta(days=4)  # a data do selo e daqui a 8 dias: nada cai antes
    relatorio = arq.arquivar(tmp_path, destino=pasta_dados, executar=True, manter_original=True, agora=antes_da_data, selo=selo)
    assert relatorio["liberadas"] == [] and pasta_arq(nome_selo, "c1").is_dir() and not list(pasta_dados.glob("*_c1"))
    with pytest.raises(arq.ArquivamentoError, match="captura_selada"):
        arq.restaurar(tmp_path, "c1", para=tmp_path / "aval", destino=pasta_dados, agora=antes_da_data)
    depois_da_data = AGORA + timedelta(days=9)
    relatorio = arq.arquivar(tmp_path, destino=pasta_dados, executar=True, manter_original=True, agora=depois_da_data, selo=selo)
    assert relatorio["liberadas"] == ["c1"]
    assert (pasta_arq(pasta_dados, "c1") / "ARQUIVO.json").is_file() and not nome_selo.exists()
    arq.restaurar(tmp_path, "c1", para=tmp_path / "aval", destino=pasta_dados, agora=depois_da_data)  # agora pode


def test_liberacao_que_falha_nao_derruba_a_rodada(arq, tmp_path, monkeypatch):
    fazer(tmp_path, "c1")
    pasta_dados = dataset(tmp_path)
    _hoje, selo = _selo_cobrindo_hoje()
    arq.arquivar(tmp_path, destino=pasta_dados, executar=True, manter_original=True, agora=DUAS_HORAS, selo=selo)
    real = os.rename

    def em_uso(origem, destino):
        if Path(destino).parent == pasta_dados and Path(destino).name.endswith("_c1"):
            raise OSError("arquivo em uso pela sincronizacao")
        return real(origem, destino)

    monkeypatch.setattr(os, "rename", em_uso)
    relatorio = arq.arquivar(tmp_path, destino=pasta_dados, executar=True, manter_original=True,
                             agora=AGORA + timedelta(days=9), selo=selo)
    assert relatorio["liberadas"] == [] and [d["motivo"] for d in relatorio["divergentes"]] == ["liberacao_falhou"]
    assert (pasta_arq(pasta_dados / ("SELADO_ate_" + selo["libera"].isoformat()), "c1") / "ARQUIVO.json").is_file()
    monkeypatch.setattr(os, "rename", real)  # a proxima rodada, ja sem o bloqueio, termina o trabalho
    relatorio = arq.arquivar(tmp_path, destino=pasta_dados, executar=True, manter_original=True,
                             agora=AGORA + timedelta(days=9), selo=selo)
    assert relatorio["liberadas"] == ["c1"] and (pasta_arq(pasta_dados, "c1") / "ARQUIVO.json").is_file()


def test_pregao_fora_da_faixa_do_selo_vai_para_a_raiz(arq, tmp_path):
    fazer(tmp_path, "c1")
    pasta_dados = dataset(tmp_path)
    hoje, _ = _selo_cobrindo_hoje()
    selo = {"de": hoje + timedelta(days=2), "ate": hoje + timedelta(days=4), "libera": hoje + timedelta(days=9)}
    arq.arquivar(tmp_path, destino=pasta_dados, executar=True, agora=DEPOIS, selo=selo)
    assert (pasta_arq(pasta_dados, "c1") / "ARQUIVO.json").is_file()
    assert not [p for p in pasta_dados.iterdir() if p.name.startswith("SELADO")]


def test_selo_invalido_e_recusado(arq, tmp_path):
    fazer(tmp_path, "c1")
    with pytest.raises(arq.ArquivamentoError, match="selo_invalido"):
        arq.arquivar(tmp_path, executar=False, agora=DEPOIS,
                     selo={"de": date(2026, 11, 9), "ate": date(2026, 11, 20), "libera": date(2026, 11, 20)})


# ------------------------------------------------------------------ linha de comando

def test_linha_de_comando_e_codigos_de_saida(arq, tmp_path, capsys):
    fazer(tmp_path, "c1")
    sem_guarda = str(tmp_path / "sem-guarda.json")
    agora = DEPOIS.isoformat()
    assert arq.main(["listar", "--raiz", str(tmp_path), "--agora", agora]) == 0
    assert "c1" in capsys.readouterr().out
    assert arq.main(["arquivar", "--raiz", str(tmp_path), "--agora", agora]) == 0  # simulacao
    assert (tmp_path / "capturas" / "c1").is_dir()
    assert arq.main(["arquivar", "--raiz", str(tmp_path), "--executar", "--agora", agora, "--guarda", sem_guarda]) == 0
    assert not (tmp_path / "capturas" / "c1").exists()
    capsys.readouterr()
    assert arq.main(["listar", "--raiz", str(tmp_path)]) == 0
    assert "ja arquivadas: 1" in capsys.readouterr().out
    assert arq.resumo_arquivadas(tmp_path)["total"] == 1
    assert arq.main(["verificar", "--raiz", str(tmp_path)]) == 0
    assert arq.main(["restaurar", "c1", "--raiz", str(tmp_path)]) == 0
    assert arq.main(["arquivar", "--raiz", str(tmp_path), "--manter-dias", "-1"]) == 2
    assert arq.main(["arquivar", "--raiz", str(tmp_path), "--selar", "2026-11-09", "2026-11-20"]) == 2  # falta --liberar-em
    assert arq.main(["restaurar", "naoexiste", "--raiz", str(tmp_path)]) == 1


def test_linha_de_comando_com_destino_e_selo(arq, tmp_path):
    fazer(tmp_path, "c1")
    pasta_dados = dataset(tmp_path)
    _hoje, selo = _selo_cobrindo_hoje()
    libera = (DEPOIS.date() + timedelta(days=30)).isoformat()
    codigo = arq.main(["arquivar", "--raiz", str(tmp_path), "--destino", str(pasta_dados), "--executar",
                       "--agora", DEPOIS.isoformat(), "--guarda", str(tmp_path / "sem-guarda.json"),
                       "--selar", selo["de"].isoformat(), selo["ate"].isoformat(), "--liberar-em", libera])
    assert codigo == 0
    assert [p.name for p in pasta_dados.iterdir()] == ["SELADO_ate_" + libera]
    assert (pasta_arq(pasta_dados / ("SELADO_ate_" + libera), "c1") / "ARQUIVO.json").is_file()


def test_codigo_de_saida_1_quando_ha_divergencia(arq, tmp_path):
    pasta = fazer(tmp_path, "c1")
    bruto = pasta / "market_frames.jsonl"
    dados = bytearray(bruto.read_bytes())
    dados[50] ^= 0x01
    bruto.write_bytes(bytes(dados))
    assert arq.main(["arquivar", "--raiz", str(tmp_path), "--executar", "--agora", DEPOIS.isoformat(),
                     "--guarda", str(tmp_path / "sem-guarda.json")]) == 1
