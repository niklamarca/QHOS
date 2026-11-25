"""QHOS-AUD-050: `pesquisa.main` (CLI) sem teste.

O lote 2b (QHOS-AUD-239, `test_pesquisa_caminho_oficial.py`) cobre ajuda, `texto`, `registrar` recusado, `amostras` e
`avaliar` com plano inexistente. Aqui ficam os caminhos que faltavam: `registrar` valido devolve `plano_id` e e
idempotente; `avaliar` com captura recusada devolve 2 (o registro dessa avaliacao e so caracterizado, ver 219);
`avaliar` valido devolve 0 e registra;
e o `except` generico nunca ecoa caminho nem texto. Registro numa raiz temporaria (QHOS2_DATA_ROOT), relogio do
modulo fixado pelo teste (`pe._agora`), corpus sintetico do `Gravador`; nenhum orcamento real e consumido.
"""
import json

import pytest

from qhos2 import desequilibrio as de
from qhos2 import eventos as ev
from qhos2 import pesquisa as pe
from test_pesquisa import ANTES, DEPOIS, captura, proposta


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    return tmp_path / "dados"


def relogio(monkeypatch, agora):
    """A CLI nao recebe `agora`: o instante vem de `pe._agora()`, fixado aqui para o teste nao depender do dia."""
    monkeypatch.setattr(pe, "_agora", lambda valor=None: agora if valor is None else valor)


def _contar(raiz, tabela, **filtro):
    with pe._banco(raiz) as conn:
        if filtro:
            (coluna, valor), = filtro.items()
            return conn.execute(f"SELECT count(*) FROM {tabela} WHERE {coluna}=?", (valor,)).fetchone()[0]
        return conn.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]


def test_cli_registrar_valido_devolve_plano_id_e_repetir_nao_cria_outro(raiz, tmp_path, monkeypatch, capsys):
    relogio(monkeypatch, ANTES)
    arquivo = tmp_path / "proposta.json"
    arquivo.write_text(json.dumps(proposta()), encoding="utf-8")
    assert pe.main(["registrar", str(arquivo)]) == 0
    saida = json.loads(capsys.readouterr().out)
    assert set(saida) == {"plano_id"} and len(saida["plano_id"]) == 64 and int(saida["plano_id"], 16) >= 0
    # a mesma proposta pela API devolve a mesma chave (idempotente) e o registro tem um plano so
    assert pe.registrar_plano(raiz, proposta(), agora=ANTES) == saida["plano_id"]
    assert pe.main(["registrar", str(arquivo)]) == 0
    assert json.loads(capsys.readouterr().out) == saida
    assert _contar(raiz, "objetos", tipo="plano") == 1


def _avaliar_com_recusada(raiz, tmp_path, monkeypatch):
    plano = pe.registrar_plano(raiz, proposta(), agora=ANTES)
    pasta = captura(tmp_path)
    relogio(monkeypatch, DEPOIS)

    def recusar(pasta_, *a, **kw):
        raise ev.EventoError("quadro_160_desalinhado:0")
    monkeypatch.setattr(de, "estados_da_captura", recusar)
    return plano, pasta, pe.main(["avaliar", plano, str(pasta)])


def test_cli_avaliar_com_captura_recusada_devolve_2(raiz, tmp_path, monkeypatch, capsys):
    """Campo 6 do 050: `avaliar` com `recusadas` devolve 2. Vale hoje (pesquisa.py:467) e vale tambem se a 219 for
    corrigida (recusar sem registrar levanta PesquisaError e a CLI devolve 2 pelo `except`)."""
    _, pasta, codigo = _avaliar_com_recusada(raiz, tmp_path, monkeypatch)
    assert codigo == 2
    saida = capsys.readouterr()
    assert str(tmp_path) not in saida.err


def test_caracterizacao_219_avaliacao_com_recusada_e_impressa_e_registrada(raiz, tmp_path, monkeypatch, capsys):
    """CARACTERIZACAO, nao requisito: hoje a avaliacao com captura recusada imprime o relatorio e fica REGISTRADA
    (pesquisa.py:311-327). E exatamente o que a QHOS-AUD-219 (excluida desta campanha; ACHADOS:4156-4169) contesta;
    se a 219 for corrigida este teste deve mudar junto, nao a correcao."""
    plano, pasta, codigo = _avaliar_com_recusada(raiz, tmp_path, monkeypatch)
    assert codigo == 2
    r = json.loads(capsys.readouterr().out)
    assert r["tipo"] == "AVALIACAO" and r["plano"] == plano
    assert r["resultado"]["recusadas"] == [f"{pasta.name}: quadro_160_desalinhado:0"]
    assert r["resultado"]["pregoes_usados"] == 0 and r["resultado"]["veredito"] == "ADIADO"
    assert _contar(raiz, "execucoes") == 1


def test_cli_avaliar_valido_devolve_0_registra_e_repeticao_e_reproducao(raiz, tmp_path, monkeypatch, capsys):
    """Sem recusada. O veredito sintetico e ADIADO (1 pregao); se ADIADO deve ou nao liberar nova hipotese e da 219
    (excluida) e nao e afirmado aqui: so o codigo de saida, o tipo e a cadeia AVALIACAO -> REPRODUCAO."""
    plano = pe.registrar_plano(raiz, proposta(), agora=ANTES)
    pasta = captura(tmp_path)
    relogio(monkeypatch, DEPOIS)
    assert pe.main(["avaliar", plano, str(pasta)]) == 0
    a = json.loads(capsys.readouterr().out)
    assert a["tipo"] == "AVALIACAO" and a["resultado"]["recusadas"] == [] and a["resultado"]["veredito"] == "ADIADO"
    assert a["resultado"]["origens_dos_dados"] == ["sintetico"] and a["rentabilidade_validada"] is False
    assert pe.main(["avaliar", plano, str(pasta)]) == 0
    b = json.loads(capsys.readouterr().out)
    assert b["tipo"] == "REPRODUCAO" and b["resultado"] == a["resultado"] and b["registro_sha256"] != a["registro_sha256"]
    assert _contar(raiz, "execucoes") == 2


@pytest.mark.parametrize("argv_de", [
    lambda d: ["texto", str(d / "SEGREDO-caminho.txt"), "--origem", "ORIGEM-PRIVADA", "--classe", "ORDEM"],
    lambda d: ["registrar", str(d / "SEGREDO-inexistente.json")],
    lambda d: ["avaliar", "SEGREDO-plano-" + "0" * 50, str(d / "SEGREDO-captura")],
    lambda d: ["amostras", str(d / "SEGREDO-captura")],
])
def test_cli_excecao_generica_nao_ecoa_caminho_texto_nem_origem(raiz, tmp_path, monkeypatch, capsys, argv_de):
    relogio(monkeypatch, DEPOIS)
    (tmp_path / "SEGREDO-caminho.txt").write_text("CONTEUDO-PRIVADO do texto", encoding="utf-8")
    assert pe.main(argv_de(tmp_path)) == 2
    saida = capsys.readouterr()
    assert saida.out.strip() == "pesquisa recusada: registro, entrada ou janela invalida"
    assert "SEGREDO" not in saida.out + saida.err and "PRIVAD" not in saida.out + saida.err
    assert str(tmp_path) not in saida.out + saida.err
