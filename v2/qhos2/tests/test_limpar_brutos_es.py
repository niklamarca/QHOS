"""Limpeza dos brutos do ES: so sai de capturas/ o que tem copia conferida no DataSet e uma segunda copia local.

A ferramenta (operacao/windows/limpar_brutos_es.py, so biblioteca padrao, roda no WSL) le o DataSet no formato da
coleta multiativo: DataSet/<AAAA-MM-DD>/<BOLSA>_<CONTRATO>[_HHMM].tar.xz, INDEX.json e SHA256SUMS.txt.
Os testes fabricam capturas com o gravador real e montam o DataSet no mesmo formato.
"""
import hashlib
import importlib.util
import io
import json
import lzma
import os
import sys
import tarfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from qhos2.gravador import Gravador

CAMINHO = Path(__file__).resolve().parents[1] / "operacao" / "windows" / "limpar_brutos_es.py"
IDENT = {"system_name": "Rithmic Paper Trading", "requested_symbol": "ESZ6", "exchange": "CME"}
SSBOE = 1_760_000_000
STATUS = {"estado": "FECHADA", "logout_confirmado": True}
BRT = timezone(timedelta(hours=-3))
NY = ZoneInfo("America/New_York")
AGORA = datetime.now(timezone.utc)
DEPOIS = AGORA + timedelta(days=10)
LIBERACAO = datetime(2027, 1, 1, tzinfo=BRT)  # decisao do autor em 30/10/2026


@pytest.fixture(scope="module")
def lim():
    spec = importlib.util.spec_from_file_location("limpar_brutos_es_sob_teste", CAMINHO)
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


def ns_ny(*instante):
    """received_ns de um instante de Nova York (ano, mes, dia, hora, ...)."""
    return int(datetime(*instante, tzinfo=NY).timestamp()) * 10 ** 9


def fazer(raiz, nome, quadros=300, sem_identidade=False, inicio_ns=SSBOE * 10 ** 9 + 70_000_000, simbolo="ESZ6"):
    gravador = Gravador(raiz / "capturas", identidade={**IDENT, "requested_symbol": simbolo}, captura_id=nome)
    for i in range(quadros):
        template = (116, 160, 161)[i % 3]
        dados = _v(154467 << 3) + _v(template) + _v(150100 << 3) + _v(SSBOE) + _v(150101 << 3) + _v(i)
        gravador(dados, template, inicio_ns + i * 1000)
    gravador.fechar(STATUS, fechamento_normal=True)
    pasta = raiz / "capturas" / nome
    if sem_identidade:  # captura antiga, anterior ao capture_identity.json
        (pasta / "capture_identity.json").unlink()
        m = json.loads((pasta / "manifest.json").read_text())
        m.pop("capture_identity_sha256", None)
        (pasta / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    return pasta


def sha(b):
    return hashlib.sha256(b).hexdigest()


def dia_de(pasta):
    criado = json.loads((Path(pasta) / "manifest.json").read_text())["status"]["criado_em"]
    return datetime.fromisoformat(criado).astimezone(BRT).date().isoformat()


def tar_xz(pasta, nome, trocar_jsonl=None, incluir_identidade=None, preset=3, trocar_manifesto=None):
    """Mesmo formato do coleta_multi.fechamento.compactar: <nome>/manifest.json, capture_identity.json, market_frames.jsonl."""
    buf = io.BytesIO()
    with lzma.open(buf, "wb", preset=preset) as xz, tarfile.open(fileobj=xz, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for arquivo in ("manifest.json", "capture_identity.json", "market_frames.jsonl"):
            caminho = Path(pasta) / arquivo
            if arquivo == "capture_identity.json":
                tem = caminho.is_file() if incluir_identidade is None else incluir_identidade
                if not tem:
                    continue
                dados = caminho.read_bytes() if caminho.is_file() else b'{"falsa": true}\n'
            else:
                dados = caminho.read_bytes()
            if arquivo == "market_frames.jsonl" and trocar_jsonl is not None:
                dados = trocar_jsonl(dados)
            if arquivo == "manifest.json" and trocar_manifesto is not None:
                dados = trocar_manifesto(dados)
            info = tarfile.TarInfo(f"{nome}/{arquivo}")
            info.size, info.mode = len(dados), 0o444
            tar.addfile(info, io.BytesIO(dados))
    return buf.getvalue()


def publicar(pasta, dataset, nome="CME_ESZ6", dia=None, conteudo=None, sha_index=None, sha_somas=None, estado="publicado"):
    """Acrescenta a captura ao dia do DataSet: .tar.xz, entrada do INDEX.json e linha do SHA256SUMS.txt."""
    dia = dia or dia_de(pasta)
    destino = Path(dataset) / dia
    destino.mkdir(parents=True, exist_ok=True)
    conteudo = tar_xz(pasta, nome) if conteudo is None else conteudo
    (destino / f"{nome}.tar.xz").write_bytes(conteudo)
    caminho_index = destino / "INDEX.json"
    index = json.loads(caminho_index.read_text()) if caminho_index.exists() else {"schema": "qhos2.dataset.index.v1", "dia": dia, "ativos": []}
    index["ativos"].append({"arquivo": f"{nome}.tar.xz", "estado": estado, "sha256_xz": sha_index or sha(conteudo),
                            "sha256_manifesto": sha((Path(pasta) / "manifest.json").read_bytes()),
                            "bolsa": "CME", "simbolo_pedido": "ES", "origem": "copia_da_captura_do_agendador"})
    caminho_index.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    linhas = [l for l in (destino / "SHA256SUMS.txt").read_text().splitlines() if not l.endswith("  INDEX.json")] \
        if (destino / "SHA256SUMS.txt").exists() else []
    linhas.append(f"{sha_somas or sha(conteudo)}  {nome}.tar.xz")
    linhas.append(f"{sha(caminho_index.read_bytes())}  INDEX.json")
    (destino / "SHA256SUMS.txt").write_text("\n".join(linhas) + "\n")
    return destino / f"{nome}.tar.xz"


def foto(raiz):
    return {p.relative_to(raiz).as_posix(): (sha(p.read_bytes()) if p.is_file() else None)
            for p in sorted(Path(raiz).rglob("*"))}


@pytest.fixture
def cenario(tmp_path):
    raiz, dataset = tmp_path / "dados", tmp_path / "DataSet"
    dataset.mkdir(parents=True)
    return raiz, dataset


# ------------------------------------------------------------------ o caminho feliz

def test_remove_o_bruto_vencido_com_copia_conferida_e_cria_a_segunda_copia_local(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    xz = publicar(pasta, dataset)
    antes_dataset = foto(dataset)
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["removidas"] == ["c1"] and rel["divergentes"] == []
    assert not pasta.exists()
    assert foto(dataset) == antes_dataset  # o DataSet so e lido
    copia = raiz / "dataset_copia_local" / dia_de_xz(xz) / "CME_ESZ6.tar.xz"
    assert copia.read_bytes() == xz.read_bytes() and rel["copias_criadas"] == ["c1"]
    assert rel["bytes_liberados"] > 0


def dia_de_xz(xz):
    return Path(xz).parent.name


def test_usa_a_copia_da_coleta_multiativo_quando_existe(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    xz = publicar(pasta, dataset)
    da_coleta = raiz / "coleta_multi" / "staging" / dia_de_xz(xz) / ".xz" / "CME_ESZ6.tar.xz"
    da_coleta.parent.mkdir(parents=True)
    da_coleta.write_bytes(xz.read_bytes())
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["removidas"] == ["c1"] and rel["copias_criadas"] == []
    assert not (raiz / "dataset_copia_local").exists()


def test_simulacao_nao_muda_nada(lim, cenario):
    raiz, dataset = cenario
    publicar(fazer(raiz, "c1"), dataset)
    antes = (foto(raiz), foto(dataset))
    rel = lim.limpar(raiz, dataset=dataset, executar=False, agora=DEPOIS)
    assert rel["simulacao"] is True and rel["prontas"] == ["c1"] and rel["removidas"] == []
    assert (foto(raiz), foto(dataset)) == antes


def test_captura_antiga_sem_identidade(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1", sem_identidade=True)
    publicar(pasta, dataset)
    assert lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)["removidas"] == ["c1"]


def test_varias_capturas_no_mesmo_dia_cada_uma_com_o_seu_arquivo(lim, cenario):
    raiz, dataset = cenario
    a, b = fazer(raiz, "c1"), fazer(raiz, "c2")
    publicar(a, dataset, nome="CME_ESZ6_1625")
    publicar(b, dataset, nome="CME_ESZ6_1627")
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["removidas"] == ["c1", "c2"]


# ------------------------------------------------------------------ o que fica

def test_no_prazo_fica(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    publicar(pasta, dataset)
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=AGORA + timedelta(days=6))
    assert rel["no_prazo"] == ["c1"] and pasta.is_dir()


def test_captura_aberta_nunca_e_tocada(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    publicar(pasta, dataset)
    (pasta / "manifest.json").unlink()
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["abertas"] == ["c1"] and (pasta / "market_frames.jsonl").is_file()


def test_sem_copia_no_dataset_fica_sem_erro(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")  # ex.: ES da janela selada, que a coleta nao copia
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert [s["id"] for s in rel["sem_copia"]] == ["c1"] and rel["divergentes"] == [] and pasta.is_dir()
    assert lim.main(["--raiz", str(raiz), "--dataset", str(dataset), "--executar", "--agora", DEPOIS.isoformat(),
                     "--guarda", str(raiz / "sem-guarda.json")]) == 0


def test_index_de_outra_captura_nao_serve(lim, cenario):
    raiz, dataset = cenario
    pasta, outra = fazer(raiz, "c1"), fazer(raiz, "c2")
    publicar(outra, dataset, dia=dia_de(pasta))  # so a c2 esta no dia
    (raiz / "capturas" / "c2" / "manifest.json").unlink()  # tira a c2 do jogo
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert [s["id"] for s in rel["sem_copia"]] == ["c1"] and pasta.is_dir()


def test_entrada_nao_publicada_nao_serve(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    publicar(pasta, dataset, estado="nao_publicado")
    assert [s["id"] for s in lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)["sem_copia"]] == ["c1"]
    assert pasta.is_dir()


@pytest.mark.parametrize("estrago", ["byte_trocado", "jsonl_diferente", "somas_divergem", "index_diverge",
                                     "identidade_a_mais", "arquivo_sumiu", "recompactado", "manifesto_diferente",
                                     "membro_faltando"])
def test_copia_que_nao_confere_deixa_o_bruto_e_sai_com_erro(lim, cenario, estrago):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1", sem_identidade=(estrago == "identidade_a_mais"))
    if estrago == "jsonl_diferente":  # xz valido, hashes do DataSet coerentes, mas o conteudo nao e o da captura
        xz = publicar(pasta, dataset, conteudo=tar_xz(pasta, "CME_ESZ6", trocar_jsonl=lambda d: d.replace(b"160", b"161", 1)))
    elif estrago == "manifesto_diferente":  # o INDEX aponta o manifesto certo, mas o do pacote foi alterado
        xz = publicar(pasta, dataset, conteudo=tar_xz(pasta, "CME_ESZ6", trocar_manifesto=lambda d: d + b" "))
    elif estrago == "membro_faltando":  # a captura tem identidade, o pacote nao
        xz = publicar(pasta, dataset, conteudo=tar_xz(pasta, "CME_ESZ6", incluir_identidade=False))
    elif estrago == "somas_divergem":
        xz = publicar(pasta, dataset, sha_somas="0" * 64)
    elif estrago == "index_diverge":
        xz = publicar(pasta, dataset, sha_index="0" * 64)
    elif estrago == "identidade_a_mais":
        xz = publicar(pasta, dataset, conteudo=tar_xz(pasta, "CME_ESZ6", incluir_identidade=True))
    else:
        xz = publicar(pasta, dataset)
    if estrago == "byte_trocado":
        dados = bytearray(xz.read_bytes())
        dados[len(dados) // 2] ^= 0xFF
        xz.write_bytes(bytes(dados))
    if estrago == "arquivo_sumiu":
        xz.unlink()
    if estrago == "recompactado":  # mesmo conteudo, outro preset: o arquivo nao e o que o DataSet declara
        xz.write_bytes(tar_xz(pasta, "CME_ESZ6", preset=6))
    antes = foto(pasta)
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["removidas"] == [] and [d["id"] for d in rel["divergentes"]] == ["c1"]
    assert foto(pasta) == antes
    assert not (raiz / "dataset_copia_local").exists()  # copia ruim nao vira segunda copia
    assert lim.main(["--raiz", str(raiz), "--dataset", str(dataset), "--executar", "--agora", DEPOIS.isoformat(),
                     "--guarda", str(raiz / "sem-guarda.json")]) == 1


def test_membro_repetido_no_pacote_nao_passa(lim, cenario):
    """Dois JSONL no mesmo pacote: o adulterado primeiro, o certo depois. Ambiguo: nao serve de copia."""
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    buf = io.BytesIO()
    with lzma.open(buf, "wb", preset=3) as xz, tarfile.open(fileobj=xz, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for arquivo, dados in (("manifest.json", (pasta / "manifest.json").read_bytes()),
                               ("capture_identity.json", (pasta / "capture_identity.json").read_bytes()),
                               ("market_frames.jsonl", b"adulterado\n"),
                               ("market_frames.jsonl", (pasta / "market_frames.jsonl").read_bytes())):
            info = tarfile.TarInfo(f"CME_ESZ6/{arquivo}")
            info.size, info.mode = len(dados), 0o444
            tar.addfile(info, io.BytesIO(dados))
    publicar(pasta, dataset, conteudo=buf.getvalue())
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["removidas"] == [] and rel["divergentes"][0]["motivo"] == "membro_inesperado" and pasta.is_dir()


def test_segunda_copia_corrompida_ao_gravar_nao_libera_o_bruto(lim, cenario, monkeypatch):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    publicar(pasta, dataset)
    real = lim._sha_arquivo
    monkeypatch.setattr(lim, "_sha_arquivo", lambda c: "0" * 64 if str(c).endswith(".parcial") else real(c))
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["removidas"] == [] and rel["divergentes"][0]["motivo"] == "copia_local_diverge"
    assert pasta.is_dir() and not list((raiz / "dataset_copia_local").rglob("*.tar.xz*"))


def test_copia_da_coleta_divergente_nao_conta_e_cria_a_propria(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    xz = publicar(pasta, dataset)
    da_coleta = raiz / "coleta_multi" / "staging" / dia_de_xz(xz) / ".xz" / "CME_ESZ6.tar.xz"
    da_coleta.parent.mkdir(parents=True)
    da_coleta.write_bytes(b"outra coisa")
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["removidas"] == ["c1"] and rel["copias_criadas"] == ["c1"]
    assert da_coleta.read_bytes() == b"outra coisa"  # nunca escreve na pasta da coleta


def test_conteudo_inesperado_e_link_ficam_de_fora(lim, cenario):
    raiz, dataset = cenario
    estranha = fazer(raiz, "estranha")
    publicar(estranha, dataset)
    (estranha / "sobra.txt").write_text("x")
    real = fazer(raiz, "real")
    publicar(real, dataset, nome="CME_ESZ6_2")
    try:
        os.symlink(real, raiz / "capturas" / "atalho", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("sem link simbolico")
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    motivos = {i["id"]: i["motivo"] for i in rel["ignoradas"]}
    assert motivos == {"estranha": "conteudo_inesperado", "atalho": "link_simbolico"}
    assert rel["removidas"] == ["real"] and (estranha / "market_frames.jsonl").is_file()


def test_data_do_dia_e_a_de_brasilia(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    m = json.loads((pasta / "manifest.json").read_text())
    m["status"]["criado_em"] = "2026-10-21T01:30:00+00:00"  # 22:30 de 20/10 em Brasilia
    (pasta / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    publicar(pasta, dataset, dia="2026-10-21")  # no dia UTC: errado
    agora = datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert [s["id"] for s in lim.limpar(raiz, dataset=dataset, executar=True, agora=agora)["sem_copia"]] == ["c1"]
    publicar(pasta, dataset, dia="2026-10-20", nome="CME_ESZ6")
    assert lim.limpar(raiz, dataset=dataset, executar=True, agora=agora)["removidas"] == ["c1"]


# ------------------------------------------------------------------ Etapa 3: o ESZ6 de 26/10 a 20/11 fica ate 1/1/2027
# O passo 11 e a avaliacao so leem <raiz>/capturas: calibracao 26/10 a 6/11, teste 9 a 20/11, avaliacao depois.

def test_protecao_e_a_combinada_com_o_autor(lim):
    p = lim.PROTECAO_ETAPA3
    assert (p.contrato, p.de, p.ate, p.liberacao) == ("ESZ6", date(2026, 10, 26), date(2026, 11, 20), LIBERACAO)


@pytest.mark.parametrize("inicio", [(2026, 10, 26, 0, 0, 1), (2026, 10, 26, 9, 0, 1), (2026, 11, 6, 15, 59),
                                    (2026, 11, 9, 9, 0), (2026, 11, 20, 23, 59, 59)])
def test_esz6_da_janela_fica_ate_a_liberacao(lim, cenario, inicio):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1", inicio_ns=ns_ny(*inicio))
    publicar(pasta, dataset)
    antes = foto(pasta)
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=LIBERACAO - timedelta(seconds=1))
    assert rel["removidas"] == rel["prontas"] == rel["divergentes"] == []
    assert rel["protegidas"] == [{"id": "c1", "motivo": "etapa3", "dia_ny": date(*inicio[:3]).isoformat()}]
    assert foto(pasta) == antes
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=max(LIBERACAO, DEPOIS))
    assert rel["removidas"] == ["c1"] and rel["protegidas"] == []


@pytest.mark.parametrize("inicio, simbolo", [((2026, 10, 25, 23, 59, 59), "ESZ6"),  # ja e 26/10 em UTC, nao em NY
                                             ((2026, 11, 21, 0, 0, 1), "ESZ6"),
                                             ((2026, 10, 28, 9, 0), "ESH7")])
def test_fora_da_janela_ou_outro_contrato_segue_a_regra_normal(lim, cenario, inicio, simbolo):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1", inicio_ns=ns_ny(*inicio), simbolo=simbolo)
    publicar(pasta, dataset)
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["protegidas"] == [] and rel["removidas"] == ["c1"]


@pytest.mark.parametrize("estrago", ["jsonl_vazio", "sem_received_ns", "linha_quebrada", "manifesto_sem_contrato"])
def test_na_duvida_a_captura_fica(lim, cenario, estrago):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1", inicio_ns=ns_ny(2026, 10, 28, 9, 0))
    publicar(pasta, dataset)
    jsonl = pasta / "market_frames.jsonl"
    if estrago == "jsonl_vazio":
        jsonl.write_bytes(b"")
    elif estrago == "sem_received_ns":
        jsonl.write_bytes(b'{"index": 0}\n' + jsonl.read_bytes())
    elif estrago == "linha_quebrada":
        jsonl.write_bytes(b"{nao e json\n" + jsonl.read_bytes())
    else:  # sem o contrato no manifesto pode ser o ESZ6
        m = json.loads((pasta / "manifest.json").read_text())
        del m["status"]["requested_symbol"]
        (pasta / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    antes = foto(pasta)
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    motivo = "etapa3" if estrago == "manifesto_sem_contrato" else "etapa3_primeiro_quadro_ilegivel"
    assert [(p["id"], p["motivo"]) for p in rel["protegidas"]] == [("c1", motivo)]
    assert rel["removidas"] == [] and foto(pasta) == antes


def test_resto_protegido_nao_e_apagado(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1", inicio_ns=ns_ny(2026, 10, 28, 9, 0))
    publicar(pasta, dataset)
    removendo = raiz / ".limpeza_es" / "removendo"
    removendo.mkdir(parents=True)
    pasta.rename(removendo / "c1")  # corte depois do rename: fora de capturas/, o passo 11 nao a ve
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert (removendo / "c1" / "market_frames.jsonl").is_file()
    assert rel["divergentes"] == [{"id": "c1", "motivo": "resto_protegido_etapa3"}]


def test_simulacao_das_19h30_dia_a_dia_ate_30_11(lim, cenario):
    """O que a tarefa faria de 30/10 a 30/11: nada da Etapa 3 sai; a captura de antes da janela segue a regra normal."""
    raiz, dataset = cenario
    da_etapa3 = {"cal_26_10": (2026, 10, 26, 9, 0), "cal_30_10": (2026, 10, 30, 9, 0), "cal_06_11": (2026, 11, 6, 9, 0),
                 "teste_09_11": (2026, 11, 9, 9, 0), "teste_20_11": (2026, 11, 20, 9, 0)}
    for nome, inicio in da_etapa3.items():
        publicar(fazer(raiz, nome, quadros=30, inicio_ns=ns_ny(*inicio)), dataset, nome=f"CME_ESZ6_{nome}")
    publicar(fazer(raiz, "antes_23_10", quadros=30, inicio_ns=ns_ny(2026, 10, 23, 15, 25)), dataset, nome="CME_ESZ6_0")
    antes = (foto(raiz), foto(dataset))
    dia = datetime(2026, 10, 30, 19, 30, tzinfo=BRT)
    while dia <= datetime(2026, 11, 30, 19, 30, tzinfo=BRT):
        rel = lim.limpar(raiz, dataset=dataset, executar=False, agora=dia)
        assert sorted(p["id"] for p in rel["protegidas"]) == sorted(da_etapa3), dia
        assert set(rel["prontas"]) <= {"antes_23_10"} and rel["divergentes"] == [], dia
        assert rel["protecao"]["ativa"] is True
        dia += timedelta(days=1)
    assert (foto(raiz), foto(dataset)) == antes  # simulacao: nada escrito
    rel = lim.limpar(raiz, dataset=dataset, executar=False, agora=max(LIBERACAO, DEPOIS))
    assert sorted(rel["prontas"]) == sorted([*da_etapa3, "antes_23_10"]) and rel["protecao"]["ativa"] is False


def test_linha_de_comando_mostra_a_protecao(lim, cenario, capsys):
    raiz, dataset = cenario
    publicar(fazer(raiz, "c1", inicio_ns=ns_ny(2026, 11, 10, 9, 0)), dataset)
    base = ["--raiz", str(raiz), "--dataset", str(dataset), "--guarda", str(raiz / "sem-guarda.json")]
    assert lim.main(base + ["--executar", "--agora", DEPOIS.isoformat()]) == 0
    saida = json.loads(capsys.readouterr().out)
    assert saida["protegidas"] == [{"id": "c1", "motivo": "etapa3", "dia_ny": "2026-11-10"}]
    assert saida["protecao"] == {"contrato": "ESZ6", "de": "2026-10-26", "ate": "2026-11-20",
                                 "liberacao": "2027-01-01T00:00:00-03:00", "ativa": True}
    assert (raiz / "capturas" / "c1").is_dir()


# ------------------------------------------------------------------ operacao

def test_guarda_em_andamento_suspende(lim, cenario):
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    publicar(pasta, dataset)
    guarda = raiz / "estado.json"
    guarda.write_text(json.dumps({"estado": "EM_ANDAMENTO"}))
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS, guarda=guarda)
    assert rel["parada"] == "captura_em_andamento" and pasta.is_dir()


def test_rodada_concorrente_e_recusada(lim, cenario):
    fcntl = pytest.importorskip("fcntl")
    raiz, dataset = cenario
    pasta = fazer(raiz, "c1")
    publicar(pasta, dataset)
    (raiz / ".limpeza_es").mkdir(parents=True)
    with open(raiz / ".limpeza_es" / "trava", "w") as trava:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
        rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert rel["parada"] == "ocupado" and pasta.is_dir()


def test_sem_trabalho_nao_escreve_nada(lim, cenario):
    raiz, dataset = cenario
    publicar(fazer(raiz, "c1"), dataset)
    antes = foto(raiz)
    lim.limpar(raiz, dataset=dataset, executar=True, agora=AGORA + timedelta(days=1))
    assert foto(raiz) == antes and not (raiz / ".limpeza_es").exists()


def test_resto_de_remocao_interrompida(lim, cenario):
    raiz, dataset = cenario
    boa, ruim = fazer(raiz, "boa"), fazer(raiz, "ruim")
    publicar(boa, dataset, nome="CME_ESZ6_1")
    removendo = raiz / ".limpeza_es" / "removendo"
    removendo.mkdir(parents=True)
    boa.rename(removendo / "boa")  # corte depois do rename: copia boa no DataSet, pode apagar
    ruim.rename(removendo / "ruim")  # sem copia no DataSet: nunca apagar
    rel = lim.limpar(raiz, dataset=dataset, executar=True, agora=DEPOIS)
    assert not (removendo / "boa").exists()
    assert (removendo / "ruim" / "market_frames.jsonl").is_file()
    assert [d["id"] for d in rel["divergentes"]] == ["ruim"]


def test_linha_de_comando(lim, cenario, capsys):
    raiz, dataset = cenario
    publicar(fazer(raiz, "c1"), dataset)
    base = ["--raiz", str(raiz), "--dataset", str(dataset), "--guarda", str(raiz / "sem-guarda.json")]
    assert lim.main(base + ["--agora", DEPOIS.isoformat()]) == 0  # simulacao
    assert json.loads(capsys.readouterr().out)["prontas"] == ["c1"]
    assert lim.main(base + ["--executar", "--agora", DEPOIS.isoformat()]) == 0
    assert not (raiz / "capturas" / "c1").exists()
    assert lim.main(base + ["--dias", "-1"]) == 2
    assert lim.main(["--raiz", str(raiz), "--dataset", str(raiz / "nao-existe")]) == 1
