"""Install the hover plugin into the current user's GIMP plugin directory."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
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
    "platform": sys.platform,
    "pillow": pillow,
}))
"""


def wheel_platform(target):
    """Name the uv platform whose Pillow wheels load in GIMP's Python."""
    if target["platform"] == "win32":
        arch = {"AMD64": "x86_64", "ARM64": "aarch64"}.get(target["machine"].upper())
        return f"{arch or target['machine']}-pc-windows-msvc"
    return f"{target['machine']}-manylinux_2_28"


def find_windows_gimp_python():
    """Find the Python that the GIMP 3 Windows installer puts in its default folders."""
    folders = [os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles")]
    if os.environ.get("LOCALAPPDATA"):
        folders.append(Path(os.environ["LOCALAPPDATA"]) / "Programs")
    for folder in filter(None, folders):
        found = sorted(Path(folder).glob("GIMP 3*/bin/python.exe"), reverse=True)
        if found:
            return found[0]
    return None


def windows_gimp_directory():
    """Match Gimp.directory() on Windows: GIMP3_DIRECTORY, else the newest %APPDATA%\\GIMP\\3.x."""
    if os.environ.get("GIMP3_DIRECTORY"):
        return Path(os.environ["GIMP3_DIRECTORY"])

    def version(path):
        try:
            return tuple(int(part) for part in path.name.split("."))
        except ValueError:
            return ()

    profiles = [path for path in Path(os.environ["APPDATA"]).glob("GIMP/3.*") if version(path)]
    if not profiles:
        sys.exit("Start GIMP once so it creates its settings folder, or pass --plugin-dir.")
    return max(profiles, key=version)


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
            wheel_platform(target),
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
    parser.add_argument(
        "--gimp-python",
        type=Path,
        help="The Python that GIMP runs plug-ins with, such as GIMP's bin\\python.exe on Windows",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    python = root / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    if not python.is_file():
        parser.error("Run uv sync first to create the model environment.")
    flatpak = False
    if args.gimp_python:
        command = [str(args.gimp_python)]
    elif WINDOWS:
        gimp_python = find_windows_gimp_python()
        if gimp_python is None:
            parser.error(
                "Couldn't find GIMP 3. Pass --gimp-python with python.exe from GIMP's bin folder."
            )
        command = [str(gimp_python)]
    else:
        flatpak = (
            not args.native
            and shutil.which("flatpak")
            and subprocess.run(
                ["flatpak", "info", "org.gimp.GIMP"], capture_output=True, check=False
            ).returncode
            == 0
        )
        command = (
            ["flatpak", "run", "--command=python3", "org.gimp.GIMP"] if flatpak else ["python3"]
        )
    if args.plugin_dir:
        destination = args.plugin_dir.expanduser() / "object-select"
    elif WINDOWS:
        destination = windows_gimp_directory() / "plug-ins" / "object-select"
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
