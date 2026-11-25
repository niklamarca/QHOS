"""Campanha de correcao (lote l5a-captura): guarda operacional, copia verificada e CLI de operacao.

IDs: QHOS-AUD-049, 081, 048, 055, 056, 096, 107, 109, 201, 206.
`src/qhos2` congelado (J0): defeitos confirmados em `xfail(strict=True)`, patch proposto em
`09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`. Guarda isolada pelo conftest (estado_padrao -> tmp_path).
"""
import contextlib
import json
import math
import os
import stat
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from qhos2 import catalogo, operacao
from qhos2.gravador import Gravador, GravadorError
from qhos2.operacao import GuardaCaptura, OperacaoError, copiar_captura, ler_identidade_captura, prazo_calendario

from test_gravador import IDENT, STATUS, gravar  # noqa: E402

CONFIRMO = "--confirmo-sessoes-encerradas"
so_linux = pytest.mark.skipif(sys.platform != "linux", reason="GuardaCaptura exige Linux (guarda_requer_linux)")


def captura_fechada(pasta, **kw):
    g = gravar(pasta, **kw)
    g.fechar(STATUS, fechamento_normal=True)
    return g


def saida_json(capsys):
    return json.loads(capsys.readouterr().out)


# ==== QHOS-AUD-049 ====
# operacao.main (estado/liberar/copiar/diagnosticar, operacao.py:325-352) sem nenhum teste.

@so_linux
def test_cli_estado_mostra_a_guarda_livre(capsys):
    assert operacao.main(["estado"]) == 0
    estado = saida_json(capsys)
    assert estado == {"schema": "qhos2_operacao_v1", "estado": "LIVRE"}


@so_linux
def test_cli_liberar_exige_a_confirmacao_pelo_argparse(capsys):
    with pytest.raises(SystemExit) as erro:
        operacao.main(["liberar", "--geracao", "a" * 32])
    assert erro.value.code == 2 and CONFIRMO in capsys.readouterr().err
    with pytest.raises(SystemExit) as erro:
        operacao.main(["liberar", CONFIRMO])
    assert erro.value.code == 2 and "--geracao" in capsys.readouterr().err


@so_linux
def test_cli_liberar_com_guarda_livre_e_recusado_com_codigo(capsys):
    assert operacao.main(["liberar", "--geracao", "a" * 32, CONFIRMO]) == 2
    assert capsys.readouterr().out.strip() == "operacao_recusada: confirmacao_de_liberacao_divergente"


@so_linux
def test_cli_estado_e_liberar_depois_de_uma_quarentena(capsys):
    with GuardaCaptura(operacao.estado_padrao()) as guarda:
        geracao = guarda.iniciar()
        guarda.finalizar(logout_confirmado=False, fechamento_normal=False)
    assert operacao.main(["estado"]) == 0
    estado = saida_json(capsys)
    assert estado["estado"] == "QUARENTENA" and estado["geracao"] == geracao
    assert estado["motivo"] == "encerramento_nao_confirmado"
    assert operacao.main(["liberar", "--geracao", "b" * 32, CONFIRMO]) == 2
    assert "confirmacao_de_liberacao_divergente" in capsys.readouterr().out
    assert operacao.main(["liberar", "--geracao", geracao, CONFIRMO]) == 0
    liberado = saida_json(capsys)
    assert liberado["estado"] == "LIVRE" and liberado["liberacao_anterior"].startswith("liberacao-")
    assert len(list(operacao.estado_padrao().glob("liberacao-*.json"))) == 1
    assert operacao.main(["liberar", "--geracao", geracao, CONFIRMO]) == 2  # nao e repetivel
    assert "confirmacao_de_liberacao_divergente" in capsys.readouterr().out
    with GuardaCaptura(operacao.estado_padrao()) as guarda:
        guarda.conferir()


def test_cli_copiar_copia_uma_vez_e_recusa_origem_sem_manifesto(tmp_path, capsys):
    g = captura_fechada(tmp_path / "dados" / "capturas")
    assert operacao.main(["copiar", str(g.pasta), str(tmp_path / "backup")]) == 0
    relatorio = saida_json(capsys)
    assert relatorio["copiada"] is True and relatorio["livro_qualificado"] == "nao_avaliado"
    assert set(relatorio["sha256"]) == {"market_frames.jsonl", "capture_identity.json", "manifest.json"}
    assert catalogo.verificar_captura(tmp_path / "backup")["verificada"] is True
    assert operacao.main(["copiar", str(g.pasta), str(tmp_path / "backup")]) == 2  # nunca sobrescreve
    assert capsys.readouterr().out.startswith("operacao_recusada: ")
    assert operacao.main(["copiar", str(tmp_path / "nao_existe"), str(tmp_path / "b2")]) == 2
    recusa = capsys.readouterr().out.strip()
    assert recusa.startswith("operacao_recusada: ") and str(tmp_path) not in recusa  # codigo, nunca o caminho
    assert not (tmp_path / "b2").exists()


def test_cli_diagnosticar_lista_capturas_incompletas_sem_reparar(tmp_path, capsys):
    captura_fechada(tmp_path / "dados" / "capturas")
    incompleta = gravar(tmp_path / "dados" / "capturas")
    try:
        assert operacao.main(["diagnosticar", "--raiz", str(tmp_path / "dados")]) == 0
        d = saida_json(capsys)
        assert d["capturas_incompletas"] == [incompleta.id] and d["data_root"] == str(tmp_path / "dados")
        assert d["livre_bytes"] > 0 and d["relogio_utc_qualificado"] is False and d["calendario_validado"] is False
        assert not (incompleta.pasta / "manifest.json").exists()  # nao promove
        assert operacao.main(["diagnosticar"]) == 0  # sem --raiz: QHOS2_DATA_ROOT (isolado pelo conftest)
        assert saida_json(capsys)["capturas_incompletas"] == []
        assert operacao.main(["diagnosticar", "--raiz", "relativa/sem/barra"]) == 2
        assert capsys.readouterr().out.startswith("operacao_recusada: ")
    finally:
        incompleta.fechar(STATUS, fechamento_normal=False)


# ==== QHOS-AUD-081 ====
# GuardaCaptura.__enter__:82-83: lock que nao e regular/do dono/0o600 -> lock_inseguro (unico ramo sem teste).

@so_linux
def test_lock_com_permissao_aberta_e_recusado(tmp_path):
    raiz = tmp_path / "estado"
    raiz.mkdir(mode=0o700)
    lock = raiz / "captura.lock"
    lock.touch()
    lock.chmod(0o644)
    with pytest.raises(OperacaoError, match="lock_inseguro"):
        with GuardaCaptura(raiz):
            pass
    assert stat.S_IMODE(lock.stat().st_mode) == 0o644  # nao "conserta" em silencio
    assert not (raiz / "estado.json").exists()
    lock.chmod(0o600)
    with GuardaCaptura(raiz) as guarda:
        guarda.conferir()


@so_linux  # FIFO e GuardaCaptura: so Linux
def test_lock_que_nao_e_arquivo_regular_e_recusado(tmp_path):
    raiz = tmp_path / "estado"
    raiz.mkdir(mode=0o700)
    os.mkfifo(raiz / "captura.lock", 0o600)
    with pytest.raises(OperacaoError, match="lock_inseguro"):
        with GuardaCaptura(raiz):
            pass


# ==== QHOS-AUD-048 ====
# copiar_captura:292 deixa vazar FileExistsError; ramo manifest.incompleto.json (:301-302) sem teste.

def test_copia_divergente_no_manifesto_deixa_manifest_incompleto(tmp_path, monkeypatch):
    g = captura_fechada(tmp_path / "origem")
    original = operacao.shutil.copyfileobj

    def mudar_so_o_manifesto(entrada, saida, length):
        original(entrada, saida, length)
        if entrada.name.endswith("manifest.json"):
            with open(entrada.name, "ab") as f:
                f.write(b"\n")
    monkeypatch.setattr(operacao.shutil, "copyfileobj", mudar_so_o_manifesto)
    with pytest.raises(OperacaoError, match="copia_divergente"):
        copiar_captura(g.pasta, tmp_path / "backup")
    assert not (tmp_path / "backup" / "manifest.json").exists()
    assert (tmp_path / "backup" / "manifest.incompleto.json").is_file()
    assert catalogo.verificar_captura(tmp_path / "backup")["divergencias"] == ["manifesto_ausente"]


def test_copiar_sobre_copia_identica_nunca_sobrescreve(tmp_path):
    """Caracterizacao: a segunda copia para um destino identico e recusada (hoje FileExistsError) e a copia fica
    intacta. Se a copia identica deve ser idempotente (copiada=True sem reescrever) e decisao pendente do autor."""
    g = captura_fechada(tmp_path / "origem")
    assert copiar_captura(g.pasta, tmp_path / "backup")["copiada"]
    antes = {p.name: p.read_bytes() for p in (tmp_path / "backup").iterdir()}
    with pytest.raises((OperacaoError, FileExistsError)):
        copiar_captura(g.pasta, tmp_path / "backup")
    assert {p.name: p.read_bytes() for p in (tmp_path / "backup").iterdir()} == antes
    assert catalogo.verificar_captura(tmp_path / "backup")["verificada"] is True


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-048: copiar_captura:292 (mkdir exist_ok=False) deixa vazar "
                                        "FileExistsError para destino existente (vazio ou com outro conteudo), "
                                        "sem codigo estatico proprio")
def test_copiar_sobre_destino_existente_nao_identico_recusa_com_codigo_proprio(tmp_path):
    g = captura_fechada(tmp_path / "origem")
    (tmp_path / "vazio").mkdir()
    with pytest.raises(OperacaoError, match="destino_existente"):  # mesmo vazio: nunca sobrescreve
        copiar_captura(g.pasta, tmp_path / "vazio")
    (tmp_path / "ocupado").mkdir()
    (tmp_path / "ocupado" / "outro.txt").write_text("de outra coisa")
    with pytest.raises(OperacaoError, match="destino_existente"):
        copiar_captura(g.pasta, tmp_path / "ocupado")
    assert list((tmp_path / "vazio").iterdir()) == [] and (tmp_path / "ocupado" / "outro.txt").read_text() == "de outra coisa"


# ==== QHOS-AUD-055 ====
# operacao.main:349-351 imprime type(erro).__name__ para ConfigError (ValueError) e OSError.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-055: operacao.main:350 imprime type(erro).__name__; o codigo "
                                        "data_root_dentro_de_repositorio_git de ConfigError e o errno do OSError se perdem")
def test_operacao_cli_mostra_codigo_de_config_error_e_errno(tmp_path, capsys):
    g = captura_fechada(tmp_path / "origem")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert operacao.main(["copiar", str(g.pasta), str(repo / "dados")]) == 2
    saida = capsys.readouterr().out
    assert "data_root_dentro_de_repositorio_git" in saida and "ConfigError" not in saida
    arquivo = tmp_path / "arquivo"
    arquivo.write_text("nao e pasta")
    assert operacao.main(["copiar", str(g.pasta), str(arquivo / "dentro")]) == 2  # ENOTDIR no mkdir do destino
    saida = capsys.readouterr().out
    assert "ENOTDIR" in saida


def test_operacao_cli_recusa_destino_em_git_sem_criar_nada(tmp_path, capsys):
    g = captura_fechada(tmp_path / "origem")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert operacao.main(["copiar", str(g.pasta), str(repo / "dados")]) == 2
    assert capsys.readouterr().out.startswith("operacao_recusada: ") and not (repo / "dados").exists()


# ==== QHOS-AUD-056 ====
# copiar_captura:282-291: captura_nao_verificada para manifesto ilegivel E para divergencias; a lista nunca e exposta.

def _alterar_um_byte_do_base64(pasta):
    linhas = (pasta / "market_frames.jsonl").read_bytes().split(b"\n")
    primeira = json.loads(linhas[0])
    b64 = primeira["protobuf_base64"]
    primeira["protobuf_base64"] = ("B" if b64[0] != "B" else "C") + b64[1:]
    nova = json.dumps(primeira, separators=(",", ":")).encode()
    assert len(nova) == len(linhas[0])
    (pasta / "market_frames.jsonl").write_bytes(b"\n".join([nova, *linhas[1:]]))


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-056: copiar_captura:290-291 esconde relatorio['divergencias'] e "
                                        ":282-283 usa o mesmo captura_nao_verificada para manifesto ausente/ilegivel")
def test_copiar_captura_informa_as_divergencias_e_distingue_manifesto_ausente(tmp_path):
    g = captura_fechada(tmp_path / "origem")
    _alterar_um_byte_do_base64(g.pasta)
    assert catalogo.verificar_captura(g.pasta)["divergencias"] == ["sha256_diverge"]
    with pytest.raises(OperacaoError) as exc:
        copiar_captura(g.pasta, tmp_path / "b1")
    assert "sha256_diverge" in str(exc.value)
    (g.pasta / "manifest.json").unlink()
    with pytest.raises(OperacaoError, match="manifesto_ausente"):
        copiar_captura(g.pasta, tmp_path / "b2")
    assert not (tmp_path / "b1").exists() and not (tmp_path / "b2").exists()


def test_copiar_captura_recusa_divergencia_e_manifesto_ausente_sem_criar_destino(tmp_path):
    g = captura_fechada(tmp_path / "origem")
    _alterar_um_byte_do_base64(g.pasta)
    with pytest.raises(OperacaoError, match="captura_nao_verificada"):
        copiar_captura(g.pasta, tmp_path / "b1")
    (g.pasta / "manifest.json").unlink()
    with pytest.raises(OperacaoError) as exc:  # sem manifesto: recusa com codigo estatico (hoje o mesmo da divergencia)
        copiar_captura(g.pasta, tmp_path / "b2")
    assert "sintetico" not in str(exc.value) and str(exc.value).isidentifier() or ":" in str(exc.value)
    assert not (tmp_path / "b1").exists() and not (tmp_path / "b2").exists()


# ==== QHOS-AUD-096 ====
# ler_identidade_captura:245-246 traduz ausente, ilegivel/adulterado e inode divergente no mesmo codigo.

def _cenarios_de_identidade(tmp_path):
    codigos = {}
    for caso in ("ausente", "adulterado", "inode"):
        g = Gravador(tmp_path / caso, identidade=IDENT)
        arquivo = g.pasta / "capture_identity.json"
        if caso == "ausente":
            arquivo.unlink()
        elif caso == "adulterado":
            arquivo.write_bytes(b"{nao e json")
        else:
            cabecalho = json.loads(arquivo.read_text())
            cabecalho["raw_ino"] += 1
            arquivo.write_text(json.dumps(cabecalho))
        with pytest.raises(OperacaoError) as exc:
            ler_identidade_captura(g.pasta)
        codigos[caso] = str(exc.value)
        with contextlib.suppress(GravadorError):
            g.fechar(STATUS, fechamento_normal=False)  # fecha o descritor; o fechamento recusa a identidade
    return codigos


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-096: ler_identidade_captura:245-246 responde "
                                        "identidade_de_captura_invalida para ausente, adulterado e inode divergente")
def test_identidade_ausente_adulterada_e_inode_divergente_tem_codigos_distintos(tmp_path):
    codigos = _cenarios_de_identidade(tmp_path)
    assert len(set(codigos.values())) == 3, codigos
    assert "ausente" in codigos["ausente"] and "inode" in codigos["inode"]


def test_identidade_ausente_adulterada_ou_com_inode_divergente_e_sempre_recusada(tmp_path):
    codigos = _cenarios_de_identidade(tmp_path)
    assert all(c.startswith("identidade_de_captura_") for c in codigos.values()), codigos


# ==== QHOS-AUD-107 ====
# GuardaCaptura: segunda partida na mesma aquisicao responde captura_em_quarentena; estado sobrevive ao __exit__.

@so_linux
def test_dupla_partida_e_recusada_e_nao_troca_a_geracao(tmp_path):
    raiz = tmp_path / "estado"
    with GuardaCaptura(raiz) as guarda:
        geracao = guarda.iniciar()
        with pytest.raises(OperacaoError):
            guarda.iniciar()
        assert json.loads((raiz / "estado.json").read_text())["geracao"] == geracao
        guarda.finalizar(logout_confirmado=True, fechamento_normal=True)
    assert guarda.fd is None


@so_linux
@pytest.mark.xfail(strict=True, reason="QHOS-AUD-107: GuardaCaptura.iniciar:130-131 responde captura_em_quarentena a "
                                        "uma segunda partida na mesma aquisicao e __exit__:94-97 nao limpa estado")
def test_dupla_partida_tem_codigo_proprio_e_estado_nao_sobrevive_ao_with(tmp_path):
    with GuardaCaptura(tmp_path / "estado") as guarda:
        guarda.iniciar()
        with pytest.raises(OperacaoError, match="ja_iniciada"):
            guarda.iniciar()
        guarda.finalizar(logout_confirmado=True, fechamento_normal=True)
    assert guarda.estado is None and guarda.fd is None


# ==== QHOS-AUD-109 ====
# operacao._atomico e catalogo._escrever_atomico: duas escritas atomicas. Caracterizacao do comportamento observavel.

def _escritores():
    return [("operacao._atomico", lambda destino: operacao._atomico(destino, {"a": 1})),
            ("catalogo._escrever_atomico", lambda destino: catalogo._escrever_atomico(destino, '{"a": 1}\n'))]


@pytest.mark.parametrize("nome,escrever", _escritores(), ids=lambda x: x if isinstance(x, str) else "")
def test_escritas_atomicas_substituem_o_link_sem_segui_lo_e_criam_0600(tmp_path, nome, escrever):
    alvo = tmp_path / "alvo.json"
    alvo.write_text("intacto")
    destino = tmp_path / "destino.json"
    destino.symlink_to(alvo)
    escrever(destino)
    assert alvo.read_text() == "intacto"                      # o alvo do link nao foi tocado
    assert not destino.is_symlink() and json.loads(destino.read_text()) == {"a": 1}
    assert stat.S_IMODE(destino.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []  # sem temporarios


@pytest.mark.parametrize("nome,escrever", _escritores(), ids=lambda x: x if isinstance(x, str) else "")
def test_escritas_atomicas_em_pasta_0777_tem_o_mesmo_comportamento(tmp_path, nome, escrever):
    pasta = tmp_path / "aberta"
    pasta.mkdir(mode=0o777)
    escrever(pasta / "x.json")
    assert json.loads((pasta / "x.json").read_text()) == {"a": 1}
    assert stat.S_IMODE((pasta / "x.json").stat().st_mode) == 0o600


# ==== QHOS-AUD-201 ====
# GuardaCaptura._ler:106-110 confere o tamanho so pelo fstat e depois json.load le o arquivo inteiro.

@so_linux
def test_estado_maior_que_16_kib_e_recusado(tmp_path):
    raiz = tmp_path / "estado"
    raiz.mkdir(mode=0o700)
    (raiz / "estado.json").write_text(json.dumps({"schema": "qhos2_operacao_v1", "estado": "LIVRE"}) + " " * 16384)
    with pytest.raises(OperacaoError, match="estado_operacional_invalido"):
        with GuardaCaptura(raiz):
            pass


@so_linux
def test_estado_que_cresce_depois_do_fstat_e_lido_inteiro_hoje(tmp_path, monkeypatch):
    """Caracterizacao da corrida (QHOS-AUD-201): com o tamanho visto pelo fstat dentro do limite, json.load le o que
    estiver no arquivo. Nenhum contrato fixa o comportamento nessa corrida (CS §2.1: "deixar como esta"); a leitura
    limitada fica como sugestao em EVIDENCIAS/QHOS-AUD-201/patch_sugerido.diff."""
    raiz = tmp_path / "estado"
    raiz.mkdir(mode=0o700)
    (raiz / "estado.json").write_text(json.dumps({"schema": "qhos2_operacao_v1", "estado": "LIVRE"}) + " " * 100_000)
    real = os.fstat

    def fstat_antes_de_crescer(fd):  # o que o fstat viu antes de o arquivo crescer
        info = real(fd)
        return types.SimpleNamespace(st_mode=info.st_mode, st_uid=info.st_uid, st_size=min(info.st_size, 64))
    monkeypatch.setattr(operacao.os, "fstat", fstat_antes_de_crescer)
    try:
        with GuardaCaptura(raiz) as guarda:
            assert guarda.estado["estado"] == "LIVRE"  # hoje: o JSON valido e aceito apesar do tamanho real
    except OperacaoError as erro:
        assert str(erro) == "estado_operacional_invalido"  # com a leitura limitada: recusa, nunca outro codigo


# ==== QHOS-AUD-206 ====
# prazo_calendario anotado `float | None`, mas devolve False em quatro ramos (operacao.py:170,172,180,183).

def _calendario():
    return {"schema": "qhos2_calendario_v1", "fonte": "teste", "valido_de": "2026-10-26T00:00:00Z",
            "valido_ate": "2026-10-27T00:00:00Z",
            "janelas": [{"inicio": "2026-10-26T10:00:00Z", "fim": "2026-10-26T11:00:00Z"}]}


AGORA = datetime(2026, 10, 26, 10, tzinfo=timezone.utc)


def test_prazo_calendario_devolve_o_fim_da_janela_como_float_ou_none_fora_dela():
    prazo = prazo_calendario(_calendario(), AGORA, 3600)
    assert isinstance(prazo, float) and prazo == datetime(2026, 10, 26, 11, tzinfo=timezone.utc).timestamp()
    assert prazo_calendario(_calendario(), AGORA, 3601) is None          # nao cabe na janela
    assert prazo_calendario(_calendario(), AGORA + timedelta(hours=2), 60) is None  # fora de qualquer janela
    cal = _calendario()
    cal["valido_ate"] = "2026-10-26T10:30:00Z"
    assert prazo_calendario(cal, AGORA, 60) == datetime(2026, 10, 26, 10, 30, tzinfo=timezone.utc).timestamp()


def _casos_invalidos():
    sem_esquema = _calendario()
    sem_esquema["schema"] = "outro"
    invertida = _calendario()
    invertida["janelas"][0]["fim"] = "2026-10-26T09:00:00Z"
    return [("sem_esquema", sem_esquema, AGORA, 60), ("duracao_nan", _calendario(), AGORA, math.nan),
            ("fora_da_validade", _calendario(), AGORA + timedelta(days=2), 60), ("janela_invertida", invertida, AGORA, 60)]


@pytest.mark.parametrize("nome,calendario,inicio,duracao", _casos_invalidos(), ids=[c[0] for c in _casos_invalidos()])
def test_prazo_calendario_invalido_devolve_so_none_ou_false(nome, calendario, inicio, duracao):
    """Caracterizacao (QHOS-AUD-206): a anotacao diz `float | None`, mas quatro ramos devolvem False
    (operacao.py:170, 172, 180, 183). Trocar por None ou anotar `Literal[False]` e escolha de contrato (CA x CS);
    os chamadores so testam a verdade. Sugestao em EVIDENCIAS/QHOS-AUD-206/patch_sugerido.diff."""
    resultado = prazo_calendario(calendario, inicio, duracao)
    assert resultado is None or resultado is False, resultado


@pytest.mark.parametrize("nome,calendario,inicio,duracao", _casos_invalidos(), ids=[c[0] for c in _casos_invalidos()])
def test_prazo_calendario_invalido_nunca_autoriza(nome, calendario, inicio, duracao):
    assert not prazo_calendario(calendario, inicio, duracao)
    assert operacao.janela_permitida(calendario, inicio, duracao) is False
