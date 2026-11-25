"""FileBrowser do Brain numa arvore sintetica (QHOS-AUD-263, 296, 301, 302).

Nenhum nome aqui e real: os "segredos" sao arquivos vazios ou com texto ficticio dentro de tmp_path.
"""
import os
from pathlib import Path

import pytest

from brain_service.file_browser import FileBrowser


def arvore(tmp_path):
    raiz = tmp_path / "trader"
    (raiz / "docs").mkdir(parents=True)
    (raiz / "docs" / "plano.txt").write_bytes(b"plano ficticio\n")   # binario: no Windows o modo texto grava \r\n
    (raiz / "docs" / "b.txt").write_text("b\n", encoding="utf-8")
    (raiz / "docs" / "c.txt").write_text("c\n", encoding="utf-8")
    (raiz / "docs" / "a.bin").write_bytes(b"\x00\x01binario")
    (raiz / ".ssh").mkdir()
    (raiz / ".ssh" / "id_rsa").write_text("CHAVE-FICTICIA\n", encoding="utf-8")
    (raiz / "cert.pem").write_text("PEM-FICTICIO\n", encoding="utf-8")
    (raiz / "service.env").write_text("SEGREDO=ficticio\n", encoding="utf-8")
    (raiz / "coletor.token").write_text("token-ficticio\n", encoding="utf-8")
    (raiz / ".git-credentials").write_text("https://u:p@exemplo.invalid\n", encoding="utf-8")
    (raiz / "chaves").mkdir()
    (raiz / "chaves" / "id_ed25519").write_text("CHAVE-FICTICIA\n", encoding="utf-8")
    (raiz / "ConsoleHost_history.txt").write_text("historico ficticio\n", encoding="utf-8")
    (tmp_path / "fora").mkdir()
    (tmp_path / "fora" / "segredo.txt").write_text("fora da raiz\n", encoding="utf-8")
    return raiz


# --- QHOS-AUD-263: caracterizacao do perimetro ---------------------------------

def test_file_browser_nao_sai_da_raiz_nem_le_credenciais(tmp_path):
    raiz = arvore(tmp_path)
    fb = FileBrowser(raiz)
    listado = fb.list("trader", "docs")
    assert listado["read_only"] is True and listado["root"] == "trader"
    assert {row["name"] for row in listado["entries"]} == {"plano.txt", "b.txt", "c.txt", "a.bin"}
    with pytest.raises(PermissionError, match="outside_read_root"):
        fb.resolve("trader", "../fora/segredo.txt")
    with pytest.raises(PermissionError, match="credential_location"):
        fb.read("trader", ".ssh/id_rsa")
    with pytest.raises(PermissionError, match="credential_file"):
        fb.read("trader", "cert.pem")
    with pytest.raises(ValueError, match="unknown_root"):
        fb.list("inexistente")


def test_file_browser_pagina_e_le_por_blocos(tmp_path):
    fb = FileBrowser(arvore(tmp_path))
    pagina = fb.list("trader", "docs", limit=2)
    assert [row["name"] for row in pagina["entries"]] == ["a.bin", "b.txt"]
    assert pagina["next_after"] == "b.txt"
    seguinte = fb.list("trader", "docs", after=pagina["next_after"], limit=2)
    assert [row["name"] for row in seguinte["entries"]] == ["c.txt", "plano.txt"]
    assert seguinte["next_after"] is None
    leitura = fb.read("trader", "docs/plano.txt", limit=5)
    assert leitura["text"] == "plano" and leitura["next_offset"] == 5 and leitura["read_only"] is True
    resto = fb.read("trader", "docs/plano.txt", offset=5)
    assert resto["text"] == " ficticio\n" and resto["next_offset"] is None


def test_file_browser_binario_so_metadado(tmp_path):
    fb = FileBrowser(arvore(tmp_path))
    with pytest.raises(ValueError, match="binary_file_metadata_only"):
        fb.read("trader", "docs/a.bin")
    with pytest.raises(ValueError, match="not_regular_file"):
        fb.read("trader", "docs")


def test_file_browser_pergunta_com_caminho_le_so_o_nomeado(tmp_path):
    raiz = arvore(tmp_path)
    fb = FileBrowser(raiz)
    resposta = fb.question(f"mostre {raiz / 'docs' / 'plano.txt'}")
    assert resposta["text"].startswith("plano ficticio") and resposta["path"] == os.path.join("docs", "plano.txt")
    listagem = fb.question("liste as pastas do trader")
    assert listagem["root"] == "trader" and listagem["path"] == ""
    assert fb.question("como esta o tempo hoje?") is None
    assert fb.snapshot()["read_only"] is True and fb.snapshot()["automatic_computer_scan"] is False


# --- QHOS-AUD-296: raiz configurada inexistente -------------------------------

def test_raiz_inexistente_fica_registrada_no_snapshot(tmp_path):
    fb = FileBrowser(arvore(tmp_path), {"runtime_logs": str(tmp_path / "nao-existe"),
                                        "runtime_data": str(tmp_path / "fora" / "segredo.txt")})
    assert "runtime_logs" not in fb.roots and "runtime_data" not in fb.roots
    assert fb.snapshot()["unavailable_roots"] == {"runtime_logs": "root_missing", "runtime_data": "root_not_directory"}
    with pytest.raises(ValueError, match="unknown_root"):
        fb.list("runtime_logs")


# --- QHOS-AUD-301: filtro de credenciais pelo caminho resolvido ---------------

@pytest.mark.parametrize("relativo", ["service.env", "coletor.token", ".git-credentials",
                                      "chaves/id_ed25519", "ConsoleHost_history.txt"])
def test_credenciais_por_nome_fora_de_pastas_privadas_sao_recusadas(tmp_path, relativo):
    fb = FileBrowser(arvore(tmp_path))
    with pytest.raises(PermissionError, match="credential_"):
        fb.read("trader", relativo)


def test_link_para_credencial_e_recusado_antes_de_ler(tmp_path):
    raiz = arvore(tmp_path)
    try:
        os.symlink(raiz / ".ssh" / "id_rsa", raiz / "docs" / "nota.txt")
        os.symlink(tmp_path / "fora" / "segredo.txt", raiz / "docs" / "externo.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink indisponivel neste host")
    fb = FileBrowser(raiz)
    with pytest.raises(PermissionError, match="credential_location"):
        fb.read("trader", "docs/nota.txt")
    with pytest.raises(PermissionError, match="outside_read_root"):
        fb.read("trader", "docs/externo.txt")


def test_pasta_privada_lexica_e_recusada_mesmo_se_o_link_sai_dela(tmp_path):
    raiz = arvore(tmp_path)
    try:
        os.symlink(raiz / "docs", raiz / ".aws")
    except (OSError, NotImplementedError):
        pytest.skip("symlink indisponivel neste host")
    fb = FileBrowser(raiz)
    with pytest.raises(PermissionError, match="credential_location"):
        fb.list("trader", ".aws")


@pytest.mark.parametrize("relativo", ["id_rsa.bak", "service.env.old", ".npmrc", ".pypirc", "credentials.json",
                                      ".docker/config.json", ".kube/config"])
def test_variantes_e_copias_de_credencial_sao_recusadas(tmp_path, relativo):
    raiz = arvore(tmp_path)
    alvo = raiz / relativo
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text("ficticio\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="credential_"):
        FileBrowser(raiz).read("trader", relativo)


def test_conteudo_de_chave_privada_e_recusado_mesmo_com_nome_inocente(tmp_path):
    raiz = arvore(tmp_path)
    (raiz / "docs" / "anotacoes.txt").write_text("-----BEGIN RSA PRIVATE KEY-----\nAAAAficticio\n-----END RSA PRIVATE KEY-----\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="credential_content"):
        FileBrowser(raiz).read("trader", "docs/anotacoes.txt")


def test_raiz_ilegivel_e_distinguida_de_ausente(tmp_path):
    if os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0:
        pytest.skip("permissao de diretorio nao e negada ao root nem no Windows")
    pai = tmp_path / "pai"
    (pai / "raiz").mkdir(parents=True)
    os.chmod(pai, 0)
    try:
        fb = FileBrowser(arvore(tmp_path), {"x": str(pai / "raiz")})
    finally:
        os.chmod(pai, 0o700)
    assert fb.snapshot()["unavailable_roots"] == {"x": "root_unreadable"} and "x" not in fb.roots


# --- QHOS-AUD-302: texto entre aspas nao e caminho ----------------------------

def test_texto_entre_aspas_sem_forma_de_caminho_nao_le_nada(tmp_path):
    fb = FileBrowser(arvore(tmp_path))
    assert fb.question('traduza "bom dia"') is None
    assert fb.question("o que significa `config.py` neste contexto?") is None


def test_caminho_entre_aspas_ou_absoluto_le_so_quando_inequivoco(tmp_path):
    raiz = arvore(tmp_path)
    fb = FileBrowser(raiz)
    assert fb.interpret('leia "docs/plano.txt"') == {"root": "trader", "relative": "docs/plano.txt", "explicit": False}
    assert fb.interpret('traduza "bom dia"') is None
    lido = fb.question('leia "docs/plano.txt"')
    assert lido["text"].startswith("plano ficticio")
    fora = fb.question(f'veja "{tmp_path / "fora" / "segredo.txt"}"')
    assert fora == {"available": False, "reason": "path_outside_configured_read_roots"}
    assert fb.question('abra "docs/nao-existe.txt"') is None, "caminho relativo adivinhado e inexistente nao e pedido operacional"
    ausente = fb.question(f'abra "{raiz / "docs" / "nao-existe.txt"}"')
    assert ausente == {"available": False, "reason": "path_not_found"}


@pytest.mark.parametrize("texto", ['traduza "e/ou"', 'o que e "24/7"?', 'diga "C:"', "resuma https://exemplo.com/artigo"])
def test_barra_url_ou_unidade_sem_caminho_real_nao_forca_modo_operacional(tmp_path, texto):
    # Revisao independente do lote l3c: estes textos devolviam path_not_found e assistant.chat forcava o modo operacional.
    fb = FileBrowser(arvore(tmp_path))
    assert fb.question(texto) is None, texto
