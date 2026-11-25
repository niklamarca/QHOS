"""CLI de `qhos2.hipoteses` (QHOS-AUD-239): ajuda, barras minimas, entrada invalida, arquivo inacessivel, `--captura`
sobre captura sintetica qualificada e recusada. Tudo em `tmp_path`; nenhum dado real."""
import json
from datetime import datetime, timezone

import pytest

from qhos2 import hipoteses as hp
from qhos2 import sintetico as si
from qhos2.gravador import Gravador
from test_hipoteses_heranca import bars

RECUSA = "hipoteses recusadas: entrada invalida"


def _arquivo(tmp_path, nome, conteudo):
    caminho = tmp_path / nome
    caminho.write_text(conteudo if isinstance(conteudo, str) else json.dumps(conteudo), encoding="utf-8")
    return str(caminho)


def _captura(tmp_path):
    """Mesma captura sintetica de `test_hipoteses_heranca`: retrato de 1 ordem por lado e 44 negocios de 30 em 30 s."""
    t = int(datetime(2026, 11, 10, 15, tzinfo=timezone.utc).timestamp()) * 10 ** 9
    g = Gravador(tmp_path / "capturas", identidade={"system_name": "QHOS2 Sintetico",
                                                  "requested_symbol": "ESZ6", "exchange": "CME"})
    for i, frame in enumerate([si.quadro_retrato("C", 25., [(4, 1, "c")]),
                               si.quadro_retrato("V", 25.25, [(1, 1, "v")]), si.quadro_retrato_fim()]):
        g(frame, 116, t + i)
    for i in range(44):
        at = t + i * 30_000_000_000 + 1_000_000_000
        g(si.quadro_negocio(25. + i * .25, 2, "C", "p", "a", at), 150, at)
    g.fechar({"estado": "FECHADA", "retrato_completo": True, "erro": None,
              "referencia": {"min_qprice_change": .25}}, fechamento_normal=True)
    return g.pasta


def test_ajuda_e_argumento_obrigatorio(capsys):
    with pytest.raises(SystemExit) as saida:
        hp.main(["--help"])
    assert saida.value.code == 0 and "--captura" in capsys.readouterr().out
    with pytest.raises(SystemExit) as saida:
        hp.main([])
    assert saida.value.code == 2


def test_barras_completas_devolvem_0_e_os_quatro_modelos_prontos_sem_permissao(tmp_path, capsys):
    assert hp.main([_arquivo(tmp_path, "b.json", bars(41))]) == 0
    r = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in r] == ["vwap", "mean_reversion", "breakout", "trend_following"]
    assert all(m["state"] == "ready" and m["trade_eligible"] is False and m["profitability_verified"] is False
               for m in r)


def test_barras_insuficientes_esperam_e_entrada_que_nao_e_lista_bloqueia(tmp_path, capsys):
    assert hp.main([_arquivo(tmp_path, "b.json", bars(20))]) == 0
    r = {m["id"]: m for m in json.loads(capsys.readouterr().out)}
    assert r["vwap"]["state"] == "ready" and r["trend_following"]["state"] == "waiting"
    assert hp.main([_arquivo(tmp_path, "o.json", {})]) == 2
    r = json.loads(capsys.readouterr().out)
    assert all(m["state"] == "blocked" and m["reason"] == "INVALID_BAR_SEQUENCE" for m in r)
    assert hp.main([_arquivo(tmp_path, "t.json", [{"time": "x"}])]) == 2
    assert all(m["reason"] == "INVALID_BAR_TIMESTAMP" for m in json.loads(capsys.readouterr().out))


def test_entrada_invalida_inacessivel_ou_grande_demais_devolve_2_com_mensagem_fixa(tmp_path, capsys):
    assert hp.main([_arquivo(tmp_path, "x.json", "nao e json")]) == 2
    assert capsys.readouterr().out.strip() == RECUSA
    assert hp.main([str(tmp_path / "nao_existe.json")]) == 2
    assert capsys.readouterr().out.strip() == RECUSA
    assert hp.main([str(tmp_path)]) == 2
    grande = tmp_path / "grande.json"
    with grande.open("wb") as f:
        f.truncate(64 * 1024 * 1024 + 1)
    assert hp.main([str(grande)]) == 2
    assert capsys.readouterr().out.strip().endswith(RECUSA)


def test_captura_sintetica_qualificada_devolve_0_e_recusada_devolve_2(tmp_path, capsys):
    pasta = _captura(tmp_path)
    assert hp.main([str(pasta), "--captura"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["esquema"] == "qhos2.barras_pesquisa.v1" and len(r["bars"]) == 44
    assert all(m["state"] == "ready" for m in r["models"])
    assert r["execution_enabled"] is False and r["profitability_verified"] is False
    assert hp.main([str(tmp_path / "nao_existe"), "--captura"]) == 2
    assert capsys.readouterr().out.strip() == RECUSA
    manifesto = pasta / "manifest.json"
    corpo = json.loads(manifesto.read_text())
    corpo["status"]["requested_symbol"] = "NQZ6"
    manifesto.write_text(json.dumps(corpo))
    assert hp.main([str(pasta), "--captura"]) == 2
    assert capsys.readouterr().out.strip() == RECUSA
