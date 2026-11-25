"""CLI de `qhos2.laboratorio` (QHOS-AUD-239): ajuda, entrada minima, entrada invalida, arquivo inacessivel e erro
controlado. Tudo em `tmp_path`; nenhum dado real, nenhuma rede."""
import json

import pytest

from qhos2.laboratorio.__main__ import main

RECUSA = "laboratorio recusado: entrada ou cenario invalido"


def _arquivo(tmp_path, nome, conteudo):
    caminho = tmp_path / nome
    caminho.write_text(conteudo if isinstance(conteudo, str) else json.dumps(conteudo), encoding="utf-8")
    return str(caminho)


def test_ajuda_e_acao_desconhecida(capsys):
    with pytest.raises(SystemExit) as saida:
        main(["--help"])
    ajuda = capsys.readouterr().out
    assert saida.value.code == 0 and "simular" in ajuda and "alinhar" in ajuda
    with pytest.raises(SystemExit) as saida:
        main(["executar", "x.json"])
    assert saida.value.code == 2
    with pytest.raises(SystemExit):
        main(["simular"])


def test_simular_entrada_minima_imprime_json_sem_permissao_de_executar(tmp_path, capsys):
    assert main(["simular", _arquivo(tmp_path, "e.json", {"events": []})]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["mode"] == "LOCAL_SIMULATION" and r["can_execute"] is False
    assert main(["simular", _arquivo(tmp_path, "c.json", {"events": [], "config": {"side": "sell"}})]) == 0
    assert json.loads(capsys.readouterr().out)["config"]["side"] == "sell"


def test_alinhar_entrada_minima_nao_qualifica_ambiente_desconhecido(tmp_path, capsys):
    entrada = {"observations": {"ES": [{"received_ns": 100, "event_time_ns": 100, "valid": True,
                                        "environment": "unknown"}]}, "at_ns": 100}
    assert main(["alinhar", _arquivo(tmp_path, "o.json", entrada)]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["eligible"] is False and r["can_execute"] is False


@pytest.mark.parametrize("conteudo", [
    "[]", "{}", '{"events": 5}', "nao e json", '{"events": [], "config": {"side": "x"}}',
    '{"events": [], "config": {"fee_per_contract": NaN}}',
], ids=["lista", "sem_events", "events_nao_lista", "nao_json", "side_invalido", "nan_na_config"])
def test_entrada_invalida_devolve_2_com_mensagem_fixa(tmp_path, capsys, conteudo):
    assert main(["simular", _arquivo(tmp_path, "x.json", conteudo)]) == 2
    assert capsys.readouterr().out.strip() == RECUSA


def test_evento_invalido_dentro_da_lista_e_erro_controlado_do_simulador_nao_da_cli(tmp_path, capsys):
    """Caracterizacao: um evento malformado bloqueia a simulacao (summary.state = blocked) mas a CLI devolve 0, porque
    o simulador nao levanta; so excecoes viram 2."""
    assert main(["simular", _arquivo(tmp_path, "n.json", {"events": [None]})]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["summary"]["state"] == "blocked" and r["can_execute"] is False


def test_alinhar_sem_campos_obrigatorios_devolve_2(tmp_path, capsys):
    assert main(["alinhar", _arquivo(tmp_path, "a.json", {"observations": {}})]) == 2
    assert capsys.readouterr().out.strip() == RECUSA
    assert main(["alinhar", _arquivo(tmp_path, "b.json", {"observations": {"ES": [None]}, "at_ns": 100})]) == 2


def test_arquivo_inacessivel_diretorio_ou_grande_demais_devolve_2(tmp_path, capsys):
    assert main(["simular", str(tmp_path / "nao_existe.json")]) == 2
    assert capsys.readouterr().out.strip() == RECUSA
    assert main(["simular", str(tmp_path)]) == 2
    grande = tmp_path / "grande.json"
    with grande.open("wb") as f:
        f.truncate(64 * 1024 * 1024 + 1)            # arquivo esparso: o limite e checado antes de qualquer leitura
    assert main(["simular", str(grande)]) == 2
    assert capsys.readouterr().out.strip().endswith(RECUSA)
