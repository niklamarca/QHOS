"""Lancador do systemd: le a credencial do systemd e chama o main sem prompt; nunca imprime a senha."""
import pytest

from qhos2 import agenda

from coleta_multi import coletor, servico

SENHA = "s3nh@ com espaco\\e barra "


@pytest.fixture(autouse=True)
def _nunca_a_guarda_real_do_es(monkeypatch):
    """Nenhum teste le ou libera a guarda verdadeira do ES (~/.local/state/qhos2/operacao)."""
    monkeypatch.setattr(servico, "_guarda_do_es_volta_sozinha", lambda *a, **k: "livre")


def _credencial(tmp_path, monkeypatch, texto):
    (tmp_path / "rithmic-paper").write_text(texto, encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))


@pytest.mark.parametrize("alvo,modulo", [("agenda", agenda), ("coletor", coletor)])
def test_passa_usuario_e_senha_ao_main_sem_prompt(tmp_path, monkeypatch, capsys, alvo, modulo):
    _credencial(tmp_path, monkeypatch, f"trader@exemplo.com\n{SENHA}\n")
    visto = {}

    def falso(argv, *, entrada, senha):
        visto.update(argv=argv, usuario=entrada("Usuario: "), senha=senha("Senha: "))
        return 0
    monkeypatch.setattr(modulo, "main", falso)
    assert servico.main([alvo, "rodar", "--online"]) == 0
    assert visto == {"argv": ["rodar", "--online"], "usuario": "trader@exemplo.com", "senha": SENHA}
    assert SENHA not in capsys.readouterr().out


@pytest.mark.parametrize("texto,codigo", [(None, "credencial_ausente"), ("so-usuario\n", "credencial_incompleta"),
                                          ("\nsenha\n", "credencial_incompleta")])
def test_sem_credencial_valida_recusa_sem_chamar_o_main(tmp_path, monkeypatch, capsys, texto, codigo):
    if texto is None:
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    else:
        _credencial(tmp_path, monkeypatch, texto)
    monkeypatch.setattr(agenda, "main", lambda *a, **k: pytest.fail("nao pode chamar o agendador"))
    assert servico.main(["agenda", "rodar"]) == 2
    assert codigo in capsys.readouterr().out


def test_alvo_desconhecido(monkeypatch):
    assert servico.main(["outro"]) == 2 and servico.main([]) == 2
