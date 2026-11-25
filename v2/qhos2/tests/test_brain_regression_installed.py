"""Exercise the real -S worker with application and pytest sharing site-packages.

All packages are copied from the test interpreter into a temporary venv. No
download, existing service, real state, credential or external connection.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False).create(root)
    packages = root / ("Lib/site-packages" if os.name == "nt" else
                       f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    source = Path(__file__).resolve().parents[1] / "src"
    for name in ("brain_service", "brain", "voice_service", "qhos2"):
        shutil.copytree(source / name, packages / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pytest", "_pytest", "py", "pluggy", "packaging", "pygments", "iniconfig"):
        spec = importlib.util.find_spec(name)
        if name == "pygments" and spec is None:  # Optional in pytest 8, required by newer versions.
            continue
        assert spec and spec.origin
        if spec.submodule_search_locations:
            shutil.copytree(Path(spec.origin).parent, packages / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copyfile(spec.origin, packages / Path(spec.origin).name)
    if os.name == "nt":
        spec = importlib.util.find_spec("colorama")
        if spec:
            shutil.copytree(Path(spec.origin).parent, packages / "colorama")
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python"), packages


def clean_env(tmp_path):
    # Values from a real login/API/provider environment are never forwarded.
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC"}}
    env.update(HOME=str(tmp_path / "home"), QHOS2_DATA_ROOT=str(tmp_path / "data"),
               BRAIN_REGRESSION_PROFILE="qhos2-operational-v1", PYTHONDONTWRITEBYTECODE="1")
    return env


@pytest.mark.lento  # venv temporario + worker real (QHOS-AUD-310)
@pytest.mark.parametrize("poison_startup", [False, True])
def test_installed_runner_finds_pytest_without_site_startup_or_user_paths(installed, tmp_path, poison_startup):
    python, packages = installed
    driver = tmp_path / "driver.py"
    driver.write_text('''from pathlib import Path
import json,os,shutil,sys
import qhos2
from brain_service.regression import RegressionRunner
root=Path(sys.argv[1]); packages=Path(qhos2.__file__).parent.parent
if sys.argv[2]=="poison":
    # The parent has already finished normal Python startup. Its -S child must
    # not execute these files, nor inherit the caller-supplied PYTHONPATH.
    payload="from pathlib import Path; Path("+repr(str(root/"startup-executed"))+").write_text('bad')"
    (packages/"sitecustomize.py").write_text(payload)
    (packages/"startup_probe.pth").write_text("import pathlib; pathlib.Path("+repr(str(root/"pth-executed"))+").write_text('bad')\\n")
    poison=root/"inherited-pythonpath";poison.mkdir()
    (poison/"pytest.py").write_text(payload+"\\nraise RuntimeError('poison-import')")
    os.environ["PYTHONPATH"]=str(poison)
    import site
    def forbidden_user_site():raise AssertionError("user-site must not be consulted")
    site.getusersitepackages=forbidden_user_site
frozen=root/"frozen";shutil.copytree(Path(qhos2.__file__).parent,frozen,ignore=shutil.ignore_patterns("__pycache__"))
r=RegressionRunner(frozen,root/"state",deadline_s=45)
assert r.start_run()["accepted"]
r._thread.join(50)
if r._thread.is_alive():r.stop()
s=r.snapshot()
summary={k:s.get(k) for k in ("phase","verified","passed","failed","collection_errors","reason")}
if s.get("artifact") and Path(s["artifact"]).exists():
    summary["blocked_side_effects"]=json.loads(Path(s["artifact"]).read_text())["blocked_side_effects"]
(root/"result.json").write_text(json.dumps(summary))
''', encoding="utf-8")
    result = subprocess.run([str(python), "-I", str(driver), str(tmp_path),
                             "poison" if poison_startup else "plain"], env=clean_env(tmp_path),
                            capture_output=True, text=True, timeout=70)
    assert result.returncode == 0, result.stderr[-1000:]
    state = json.loads((tmp_path / "result.json").read_text())
    assert state["phase"] == "PASSED", state
    assert state["verified"] is True and state["passed"] == 8
    assert state["blocked_side_effects"] == []
    assert not (tmp_path / "startup-executed").exists()
    assert not (tmp_path / "pth-executed").exists()


@pytest.mark.lento  # venv temporario + worker real (QHOS-AUD-310)
def test_installed_worker_keeps_live_packages_credentials_network_and_writes_blocked(installed, tmp_path):
    python, packages = installed
    mirror = tmp_path / "state/regression_runs/run-boundary"
    (mirror / "tests").mkdir(parents=True)
    (mirror / "__init__.py").write_text('MIRROR_ONLY=True\n')
    (mirror / "pytest.ini").write_text('[pytest]\naddopts =\n')
    (packages / "qhos2/live_probe.py").write_text('raise AssertionError("live-source-executed")\n')
    (packages / "auth.json").write_text('{"synthetic":"not-a-real-token"}')
    test = '''from pathlib import Path
import importlib,socket,subprocess
import pytest,qhos2
LIVE=Path(__LIVE__)
def test_boundaries():
    assert qhos2.MIRROR_ONLY and Path(qhos2.__file__).parent==Path.cwd()
    with pytest.raises(RuntimeError): (LIVE/"qhos2/__init__.py").read_bytes()
    with pytest.raises(RuntimeError): importlib.import_module("brain")
    qhos2.__path__.append(str(LIVE/"qhos2"))
    with pytest.raises(RuntimeError): importlib.import_module("qhos2.live_probe")
    with pytest.raises(RuntimeError): (LIVE/"auth.json").read_bytes()
    with pytest.raises(RuntimeError): Path(__OUTSIDE__).write_text("forbidden")
    with socket.socket() as s:
        with pytest.raises(RuntimeError): s.connect(("127.0.0.1",9))
    with pytest.raises(RuntimeError): subprocess.Popen(["never-run-this-command"])
'''.replace("__LIVE__", repr(str(packages))).replace("__OUTSIDE__", repr(str(tmp_path / "outside-write")))
    (mirror / "tests/test_boundary.py").write_text(test, encoding="utf-8")
    (mirror / "request.json").write_text(json.dumps({"suite": ["tests/test_boundary.py"], "deadline_s": 30,
                                                     "run_id": "fixture", "source_hash": "synthetic"}))
    env = clean_env(tmp_path)
    env.update(PYTHONPATH=str(packages), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    # This custom boundary test is isolated from the production pinned suite.
    # The real runner's fixed eight tests are exercised above without changes.
    code = ("import sys;from brain_service import regression as r;"
            "r.REVIEWED_TESTS={'tests/test_boundary.py':'synthetic'};"
            "raise SystemExit(r._worker(sys.argv[1]))")
    result = subprocess.run([str(python), "-B", "-S", "-c", code, str(mirror)], env=env,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr[-1000:]
    evidence = json.loads((mirror / "regression-results.json").read_text())
    assert all(r["outcome"] == "passed" for r in evidence["records"])
    events = {row["event"] for row in evidence["blocked_side_effects"]}
    assert {"live_application_read", "credential_read", "socket.connect", "subprocess.Popen"} <= events
    assert {"external_read", "external_write"} & events
    assert not (tmp_path / "outside-write").exists()
