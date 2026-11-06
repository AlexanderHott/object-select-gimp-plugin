"""Install the hover plugin into the current user's GIMP plugin directory."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PILLOW = "pillow>=11,<13"
# Importing Image also loads the compiled module, so a build for another Python counts as missing.
PROBE = """
import json, platform, sys
try:
    from PIL import Image
    pillow = True
except ImportError:
    pillow = False
print(json.dumps({
    "version": "%d.%d" % sys.version_info[:2],
    "machine": platform.machine(),
    "pillow": pillow,
}))
"""


def install_pillow(destination, gimp_python, model_python):
    """Vendor Pillow for GIMP's Python when GIMP doesn't provide a working copy."""
    vendor = destination / "vendor"
    shutil.rmtree(vendor, ignore_errors=True)
    probe = subprocess.run(gimp_python + ["-c", PROBE], text=True, capture_output=True, check=True)
    target = json.loads(probe.stdout.splitlines()[-1])
    if target["pillow"]:
        return None
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        sys.exit("GIMP's Python needs Pillow, and installing it requires uv.")
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--quiet",
            "--python",
            str(model_python),
            "--python-version",
            target["version"],
            "--python-platform",
            f"{target['machine']}-manylinux_2_28",
            "--only-binary",
            ":all:",
            "--target",
            str(vendor),
            PILLOW,
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return target["version"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-dir", type=Path, help="Override the GIMP plug-ins directory")
    parser.add_argument("--native", action="store_true", help="Use a non-Flatpak GIMP installation")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    python = root / ".venv" / "bin" / "python"
    if not python.is_file():
        parser.error("Run uv sync first to create the model environment.")
    flatpak = (
        not args.native
        and shutil.which("flatpak")
        and subprocess.run(
            ["flatpak", "info", "org.gimp.GIMP"], capture_output=True, check=False
        ).returncode
        == 0
    )
    command = ["flatpak", "run", "--command=python3", "org.gimp.GIMP"] if flatpak else ["python3"]
    if args.plugin_dir:
        destination = args.plugin_dir.expanduser() / "object-select"
    else:
        query = subprocess.run(
            command
            + [
                "-c",
                (
                    "import gi; gi.require_version('Gimp','3.0'); "
                    "from gi.repository import Gimp; print(Gimp.directory())"
                ),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        destination = Path(query.stdout.strip()) / "plug-ins" / "object-select"
    destination.mkdir(parents=True, exist_ok=True)
    for source in (root / "gimp-plugin" / "object-select").glob("*.py"):
        target = destination / source.name
        shutil.copy2(source, target)
        target.chmod(0o755 if source.name == "object-select.py" else 0o644)
    settings = {
        "python": str(python),
        "worker": str(root / "object_select" / "worker.py"),
        "command_prefix": ["flatpak-spawn", "--host", "--watch-bus"] if flatpak else [],
    }
    (destination / "launcher.json").write_text(json.dumps(settings, indent=2) + "\n")
    pillow_version = install_pillow(destination, command, python)
    print(f"Installed Object Select in {destination}")
    if pillow_version:
        print(f"Added Pillow for GIMP's Python {pillow_version}.")
    print("Restart GIMP, open an image, select one pixel layer, then Select > Object Select.")
    print("Keep this repository and its .venv in place; the plugin launches its model worker here.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.stderr or str(error))
