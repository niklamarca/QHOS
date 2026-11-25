"""Robustez do observador do Brain: transporte, git e recursos sem transformar falha em "limpo" (QHOS-AUD-264, 275, 277, 288)."""
import http.client
import logging

import pytest

from brain_service.config import ServiceConfig


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL",
                 "BRAIN_QUANT_LOGS", "BRAIN_QUANT_DATA_LAKE"):
        monkeypatch.delenv(name, raising=False)


class _BrokenBody:
    """Resposta cujo corpo termina antes do Content-Length (processo morrendo no meio)."""
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self, _n):
        raise http.client.IncompleteRead(b"")


def _raise(exc):
    def _inner(*_a, **_k):
        raise exc
    return _inner


# --- QHOS-AUD-264 -----------------------------------------------------------

@pytest.mark.parametrize("failure", [
    lambda *a, **k: _BrokenBody(),
    _raise(http.client.BadStatusLine("HTTP/1.1 ???")),
    _raise(http.client.LineTooLong("header line")),
], ids=["IncompleteRead", "BadStatusLine", "LineTooLong"])
def test_get_json_turns_http_protocol_errors_into_observation_error(monkeypatch, failure):
    from brain_service import local_http, observer
    monkeypatch.setattr(local_http, "open_local", failure)
    row = observer.get_json("http://127.0.0.1:8766", "/api/health", timeout_s=1)
    assert row["path"] == "/api/health"
    assert row["_observation_error"] in {"IncompleteRead", "BadStatusLine", "LineTooLong"}


def test_collect_survives_truncated_collector_response(monkeypatch, tmp_path):
    from brain_service import local_http, observer
    monkeypatch.setattr(local_http, "open_local", lambda *a, **k: _BrokenBody())
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False)
    quant = observer.QuantObserver(cfg, repo_root=tmp_path)
    snap = quant.collect()
    assert quant.last is snap and quant.last_collected_at == snap["observed_at"]
    assert not snap["application_ready"]
    assert any("IncompleteRead" in error for error in snap["telemetry"]["errors"])


# --- QHOS-AUD-275 -----------------------------------------------------------

def _no_git(monkeypatch, tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))


def _no_collector(monkeypatch):
    import urllib.error
    from brain_service import local_http
    monkeypatch.setattr(local_http, "open_local", _raise(urllib.error.URLError("refused")))


def test_git_unavailable_is_reported_not_clean(monkeypatch, tmp_path):
    from brain_service import observer
    _no_collector(monkeypatch)
    _no_git(monkeypatch, tmp_path)
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False)
    snap = observer.QuantObserver(cfg, repo_root=tmp_path).collect()
    git = snap["git"]
    assert git["available"] is False
    assert "FileNotFoundError" in git["error"]
    assert git["dirty_n"] is None and git["recent_commits"] == [] and git["dirty_sample"] == []
    # Saude nao muda por causa do git: telemetry.errors continua a ser so transporte/arquivos (020/265 fora do lote).
    assert all(error.startswith(("/api/", "missing:")) for error in snap["telemetry"]["errors"]), snap["telemetry"]["errors"]


def test_git_outside_repository_is_reported_not_clean(tmp_path):
    import shutil
    if not shutil.which("git"):
        pytest.skip("git ausente no host")
    from brain_service import observer
    result = observer._git(tmp_path, ["status", "--porcelain"])
    assert result["available"] is False and result["error"].startswith("git_exit_")
    assert result["stdout"] == ""


def test_perception_git_unavailable_is_reported_not_clean(monkeypatch, tmp_path):
    from brain import perception
    _no_git(monkeypatch, tmp_path)
    git = perception.observe_git()
    assert git["available"] is False and "FileNotFoundError" in git["error"]
    assert git["dirty_n"] is None and git["recent_commits"] == []


# --- QHOS-AUD-277 -----------------------------------------------------------

def _fake_checkout(tmp_path):
    repo = tmp_path / "checkout"
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (repo / ".git" / "refs" / "heads" / "main").write_text("a" * 40 + "\n", encoding="utf-8")
    src = repo / "qhos2" / "src"
    src.mkdir(parents=True)
    return repo, src


def test_git_revision_finds_dot_git_above_src_layout(tmp_path, monkeypatch):
    from brain import attest
    _repo, src = _fake_checkout(tmp_path)
    monkeypatch.setattr(attest, "_REPO_ROOT", src)  # layout <checkout>/qhos2/src: o .git fica dois niveis acima
    out = attest.git_revision()
    assert out["available"] is True and out["revision"] == "a" * 12 and out["ref"] == "refs/heads/main", \
        f"detector de git morto no layout src/: {out}"
    missing = attest.git_revision(tmp_path / "nowhere")
    assert missing["available"] is False and missing["error"] == "no_git_dir"


def test_git_revision_reads_worktree_gitdir_file(tmp_path):
    from brain.attest import git_revision
    main_repo, _ = _fake_checkout(tmp_path)
    (main_repo / ".git" / "refs" / "heads" / "feature").write_text("b" * 40 + "\n", encoding="utf-8")
    private = main_repo / ".git" / "worktrees" / "wt"
    private.mkdir(parents=True)
    (private / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (private / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "wt"
    (worktree / "qhos2" / "src").mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {private}\n", encoding="utf-8")
    out = git_revision(worktree / "qhos2" / "src")
    assert out["available"] is True and out["revision"] == "b" * 12 and out["ref"] == "refs/heads/feature"


def test_attestation_exposes_git_unavailability(tmp_path, monkeypatch):
    from brain_service import attestation
    monkeypatch.setattr(attestation, "git_revision",
                        lambda *a, **k: {"revision": "", "ref": "", "available": False, "error": "no_git_dir"})
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False, expected_revision="deadbeefcafe")
    snap = attestation.RuntimeAttestor(cfg).snapshot()
    assert "git_head_moved" not in snap["drift"]
    assert "running_revision_not_expected" in snap["drift"]
    # Comportamento primeiro: nada no snapshot dizia que o git nao foi medido.
    unavailable_markers = [snap["running"].get("git_available"), snap["worktree"].get("git_available"), snap.get("warnings")]
    assert unavailable_markers == [False, False, ["git_revision_unavailable"]], \
        f"atestacao esconde git indisponivel (so '' em git_revision): {unavailable_markers}"
    assert snap["expected"]["compared_against"] == ["source_revision"]
    # Indisponibilidade NAO entra em drift (decisao pendente do autor, ver ficha/DECISOES): loaded_code_state nao redefinido aqui.
    assert snap["loaded_code_state"] == "STALE" and snap["drift"] == ["running_revision_not_expected"]


def test_attestation_matches_expected_git_sha_when_git_available(tmp_path, monkeypatch):
    from brain_service import attestation
    monkeypatch.setattr(attestation, "git_revision",
                        lambda *a, **k: {"revision": "a" * 12, "ref": "refs/heads/main", "available": True, "error": None})
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False, expected_revision="a" * 40)
    snap = attestation.RuntimeAttestor(cfg).snapshot()
    assert "running_revision_not_expected" not in snap["drift"]
    assert snap["running"]["git_available"] is True and snap["warnings"] == []
    assert set(snap["expected"]["compared_against"]) == {"source_revision", "git_revision"}


# --- QHOS-AUD-288 -----------------------------------------------------------

def test_own_resources_reports_psutil_missing(monkeypatch, tmp_path):
    import sys
    from brain_service import observer
    monkeypatch.setitem(sys.modules, "psutil", None)
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False)
    out = observer.QuantObserver(cfg, repo_root=tmp_path)._own_resources()
    assert out == {"subject": "brain_service", "error": "psutil_unavailable"}


def test_perception_resources_report_psutil_missing(monkeypatch):
    import sys
    from brain import perception
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert perception.observe_resources() == {"error": "psutil_unavailable"}


def test_attestation_resources_report_psutil_missing(monkeypatch, tmp_path):
    import sys
    from brain_service import attestation
    monkeypatch.setitem(sys.modules, "psutil", None)
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False)
    assert attestation.RuntimeAttestor(cfg).snapshot()["running"]["resources"] == {"error": "psutil_unavailable"}


def test_read_json_distinguishes_absent_from_corrupt(tmp_path):
    from brain_service import observer
    absent = tmp_path / "absent.json"
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    nan = tmp_path / "nan.json"
    nan.write_text('{"x": NaN}', encoding="utf-8")
    assert observer.read_json_file(absent) == ({}, None)
    blob, error = observer.read_json_file(corrupt)
    assert blob == {} and error.startswith("JSONDecodeError")
    blob, error = observer.read_json_file(nan)
    assert blob == {} and error.startswith("ValueError")
    assert observer._read_json(corrupt) == {}


def test_collect_reports_corrupt_lake_files_without_changing_health(monkeypatch, tmp_path):
    from brain_service import observer
    _no_collector(monkeypatch)
    lake = tmp_path / "lake"
    lake.mkdir()
    (lake / "autonomy.json").write_text("{corrupt", encoding="utf-8")
    monkeypatch.setenv("BRAIN_QUANT_DATA_LAKE", str(lake))
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False)
    snap = observer.QuantObserver(cfg, repo_root=tmp_path).collect()
    assert list(snap["telemetry"]["file_errors"]) == ["autonomy"]
    assert snap["telemetry"]["file_errors"]["autonomy"].startswith("JSONDecodeError")
    assert snap["trading_autonomy"]["source"] == "absent"
    assert not any("autonomy" in error for error in snap["telemetry"]["errors"])


def test_read_json_absorbs_recursion_error_as_file_error(monkeypatch, tmp_path):
    """Um JSON aninhado alem do limite do interpretador e um arquivo ruim, nao um motivo para abortar collect()."""
    from brain_service import observer
    path = tmp_path / "deep.json"
    path.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    blob, error = observer.read_json_file(path)
    assert blob == {} and error == "RecursionError"


def test_perception_read_json_does_not_hide_memory_error(monkeypatch, tmp_path):
    from brain import perception
    path = tmp_path / "x.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(perception.Path, "read_text", _raise(MemoryError("sem memoria")))
    with pytest.raises(MemoryError):
        perception._read_json(path)
