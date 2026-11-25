"""Cabecalho publico antes da captura, vinculo de arquivo e copia verificavel."""
import json,os
from pathlib import Path
import pytest
from qhos2.gravador import Gravador, GravadorError
from qhos2.operacao import ler_identidade_captura, OperacaoError, copiar_captura
from test_gravador import IDENT, STATUS, gravar


def test_header_precede_quadro_e_nao_grava_keys_secretas(tmp_path):
    g = Gravador(tmp_path, identidade={**IDENT, "password":"never-persist", "user":"never-user"}, captura_id="id-publico")
    try:
        p = g.pasta / "capture_identity.json"
        raw = p.read_bytes(); h = ler_identidade_captura(g.pasta)
        assert g.quadros == 0 and (g.pasta / "market_frames.jsonl").stat().st_size == 0
        assert b"never-" not in raw and b"password" not in raw and b"user" not in raw
        assert h["capture_id"] == "id-publico" and h["requested_symbol"] == IDENT["requested_symbol"]
        assert h["raw_ino"] == (g.pasta / "market_frames.jsonl").stat().st_ino
    finally:
        g.fechar(STATUS, fechamento_normal=True)


def _descritores():
    """Descritores abertos neste processo (Linux: /proc; senao, sondagem por fstat)."""
    try:
        return set(os.listdir("/proc/self/fd"))
    except OSError:
        abertos = set()
        for fd in range(1024):
            try:
                os.fstat(fd)
                abertos.add(fd)
            except OSError:
                pass
        return abertos


def test_falha_header_fecha_descritor_sem_manifesto(tmp_path):
    # falha legitima, sem remendar open() nem a escrita atomica (QHOS-AUD-071): o gravador aceita a identidade, mas o
    # cabecalho publico so admite campos de ate 128 caracteres, e a releitura do que foi persistido o recusa
    antes = _descritores()
    with pytest.raises(GravadorError, match="identidade_de_captura_nao_persistida") as erro:
        Gravador(tmp_path, identidade={**IDENT, "system_name": "x" * 129}, captura_id="failure")
    # `erro` segura o traceback (e com ele o Gravador meio construido) ate aqui: um descritor que o codigo nao fechou
    # nao e fechado pela contagem de referencias antes da conferencia
    assert _descritores() == antes, erro.traceback[-1]
    assert not (tmp_path/"failure"/"manifest.json").exists()


def test_header_adulterado_nao_vira_manifesto_valido(tmp_path):
    g=Gravador(tmp_path, identidade=IDENT)
    p=g.pasta/"capture_identity.json"; h=json.loads(p.read_text());h["exchange"]="OTHER";p.write_text(json.dumps(h))
    with pytest.raises(GravadorError, match="identidade_de_captura_diverge"):
        g.fechar(STATUS, fechamento_normal=True)
    assert not (g.pasta/"manifest.json").exists()


def test_arquivo_trocado_recusa_ativa_e_restore_fechado_aceita_inode_novo(tmp_path):
    g=gravar(tmp_path/"origem")
    g.fechar(STATUS,fechamento_normal=True)
    m=json.loads((g.pasta/"manifest.json").read_text())
    before=(g.pasta/"market_frames.jsonl").stat().st_ino
    destino=tmp_path/"restore-renomeado"
    assert copiar_captura(g.pasta,destino)["copiada"]
    assert (destino/"market_frames.jsonl").stat().st_ino != before
    assert ler_identidade_captura(destino,manifesto=m)["capture_id"]==g.id
    with pytest.raises(OperacaoError):ler_identidade_captura(destino)


def test_backup_recusa_header_corrompido(tmp_path):
    g=gravar(tmp_path/"origem")
    g.fechar(STATUS,fechamento_normal=True)
    h=g.pasta/"capture_identity.json";h.write_bytes(h.read_bytes()+b" ")
    with pytest.raises(OperacaoError,match="identidade_de_captura_invalida"):
        copiar_captura(g.pasta,tmp_path/"backup")
    assert not (tmp_path/"backup").exists()


@pytest.mark.parametrize("alteracao", ["extra", pytest.param("fifo", marks=pytest.mark.posix),
                                       pytest.param("symlink", marks=pytest.mark.posix), "too_large", "inode"])
def test_header_inseguro_recusado_sem_bloqueio(tmp_path, alteracao):
    g=Gravador(tmp_path,identidade=IDENT)
    p=g.pasta/"capture_identity.json"; original=p.read_bytes()
    try:
        if alteracao=="extra":
            h=json.loads(original);h["password"]="not-allowed";p.write_text(json.dumps(h))
        elif alteracao=="fifo":p.unlink();os.mkfifo(p)
        elif alteracao=="symlink":
            target=tmp_path/"outside";target.write_bytes(original);p.unlink();p.symlink_to(target)
        elif alteracao=="too_large":p.write_bytes(b" "*4097)
        else:
            h=json.loads(original);h["raw_ino"]+=1;p.write_text(json.dumps(h))
        with pytest.raises(OperacaoError):ler_identidade_captura(g.pasta)
    finally:
        p.unlink();p.write_bytes(original);g.fechar(STATUS,fechamento_normal=True)


def test_backup_legado_sem_header_permanece_compativel(tmp_path):
    g=gravar(tmp_path/"origem")
    g.fechar(STATUS,fechamento_normal=True)
    p=g.pasta/"manifest.json";m=json.loads(p.read_text());m.pop("capture_identity_sha256");m.pop("capture_id");p.write_text(json.dumps(m));(g.pasta/"capture_identity.json").unlink()
    assert copiar_captura(g.pasta,tmp_path/"backup")["copiada"]
    assert not (tmp_path/"backup"/"capture_identity.json").exists()
