import json
import subprocess
from types import SimpleNamespace

from object_select import install


def fake_run(calls, pillow):
    def run(command, **_):
        calls.append(command)
        report = {"version": "3.14", "machine": "x86_64", "pillow": pillow}
        return SimpleNamespace(stdout=f"sandbox notice\n{json.dumps(report)}\n")

    return run


def test_vendors_pillow_built_for_gimps_python(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls, pillow=False))
    monkeypatch.setenv("UV", "/bin/uv")
    assert install.install_pillow(tmp_path, ["gimp-python"], "model-python") == "3.14"
    assert calls[0][0] == "gimp-python"
    command = calls[1]
    assert command[:3] == ["/bin/uv", "pip", "install"]
    assert command[command.index("--python-version") + 1] == "3.14"
    assert command[command.index("--python-platform") + 1] == "x86_64-manylinux_2_28"
    assert command[command.index("--target") + 1] == str(tmp_path / "vendor")


def test_gimps_own_pillow_replaces_an_old_vendored_copy(monkeypatch, tmp_path):
    (tmp_path / "vendor" / "PIL").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls, pillow=True))
    assert install.install_pillow(tmp_path, ["gimp-python"], "model-python") is None
    assert len(calls) == 1, "uv must not run"
    assert not (tmp_path / "vendor").exists()
