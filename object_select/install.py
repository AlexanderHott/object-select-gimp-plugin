"""Install the hover plugin into the current user's GIMP plugin directory."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

WINDOWS = sys.platform == "win32"
PILLOW = "pillow>=11,<13"
PILLOW_MIN_MAJOR, PILLOW_BELOW_MAJOR = 11, 13
# Importing Image also loads the compiled module, so a build for another Python counts as missing.
PROBE = """
import json, platform, sys, sysconfig
try:
    from PIL import Image
    pillow = True
except ImportError:
    pillow = False
print(json.dumps({
    "version": "%d.%d" % sys.version_info[:2],
    "machine": platform.machine(),
    "platform": sys.platform,
    "ext_suffix": sysconfig.get_config_var("EXT_SUFFIX") or "",
    "platform_tag": sysconfig.get_platform(),
    "pillow": pillow,
}))
"""
# Re-import exactly what the plug-in uses, the way the plug-in arranges DLL lookup,
# so a missing dependency surfaces here instead of at first use inside GIMP.
VERIFY = """
import os, sys
vendor = sys.argv[1]
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(os.path.join(vendor, "PIL"))
sys.path.insert(0, vendor)
from PIL import Image, ImageChops, ImageDraw, ImageFilter
Image.new("RGB", (4, 4)).filter(ImageFilter.GaussianBlur(1))
print("ok")
"""

# GIMP's Windows Python is an MSYS2 build, so its compiled modules load only MSYS2
# (not PyPI/MSVC) binaries. These map sysconfig.get_platform() to the MSYS2
# repository folder and package-name prefix that match it.
MSYS2_MIRRORS = (
    "https://repo.msys2.org/mingw/",
    "https://ftp.osuosl.org/pub/msys2/mingw/",
    "https://mirror.umd.edu/msys2/mingw/",
)
MSYS2_ENVIRONMENTS = {
    "mingw_x86_64_ucrt_llvm": ("clang64", "mingw-w64-clang-x86_64-"),
    "mingw_aarch64_ucrt_llvm": ("clangarm64", "mingw-w64-clang-aarch64-"),
    "mingw_x86_64_ucrt": ("ucrt64", "mingw-w64-ucrt-x86_64-"),
    "mingw_x86_64": ("mingw64", "mingw-w64-x86_64-"),
}


def wheel_platform(target):
    """Name the uv platform whose Pillow wheels load in GIMP's Python."""
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


def msys2_fetch(path):
    """Read one repository path from the first MSYS2 mirror that serves it."""
    errors = []
    for base in MSYS2_MIRRORS:
        request = urllib.request.Request(
            base + path, headers={"User-Agent": "object-select-installer"}
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except OSError as error:
            errors.append(f"  {base + path}: {error}")
    raise RuntimeError("Couldn't reach the MSYS2 package mirrors:\n" + "\n".join(errors))


def msys2_versions(env_dir, package):
    """List a package's releases as (sort_key, pkgver, filename), newest first."""
    index = msys2_fetch(env_dir + "/").decode("utf-8", "replace")
    pattern = re.escape(package) + r"-(\d[^\"<]*?)-(\d+)-any\.pkg\.tar\.zst"
    releases = {}
    for pkgver, pkgrel in re.findall(pattern, index):
        filename = f"{package}-{pkgver}-{pkgrel}-any.pkg.tar.zst"
        numbers = [int(part) for part in re.findall(r"\d+", pkgver)]
        releases[filename] = ((numbers, int(pkgrel)), pkgver, filename)
    return sorted(releases.values(), reverse=True)


def msys2_extract(env_dir, filename, workdir):
    """Download one MSYS2 package into workdir and unpack it, returning its root."""
    archive = workdir / filename
    archive.write_bytes(msys2_fetch(env_dir + "/" + filename))
    extract = workdir / filename[: -len(".pkg.tar.zst")]
    extract.mkdir()
    try:
        subprocess.run(
            ["tar", "-C", str(extract), "-xf", str(archive)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        sys.exit("Unpacking GIMP's Pillow needs the 'tar' command, which ships with Windows 10+.")
    return extract


def install_pillow_msys2(vendor, target):
    """Vendor an MSYS2 Pillow whose binaries match GIMP's MSYS2 Python."""
    platform_tag = target["platform_tag"]
    environment = MSYS2_ENVIRONMENTS.get(platform_tag)
    if environment is None:
        sys.exit(
            f"GIMP's Python ({platform_tag}) isn't a recognised MSYS2 build, so the matching "
            "Pillow can't be fetched. Install Pillow into GIMP's Python yourself, or report this."
        )
    env_dir, prefix = environment
    ext_suffix = target["ext_suffix"]
    work = Path(tempfile.mkdtemp(prefix="object-select-pillow-"))
    try:
        pillow_version = None
        pil_source = None
        tried = []
        for _, pkgver, filename in msys2_versions(env_dir, prefix + "python-pillow"):
            major = int(re.findall(r"\d+", pkgver)[0])
            if not PILLOW_MIN_MAJOR <= major < PILLOW_BELOW_MAJOR:
                continue
            tried.append(pkgver)
            extract = msys2_extract(env_dir, filename, work)
            # The compiled module name carries the ABI tag; a match means this build loads.
            found = list(extract.glob(f"**/site-packages/PIL/_imaging{ext_suffix}"))
            if found:
                pil_source, pillow_version = found[0].parent, pkgver
                break
            shutil.rmtree(extract, ignore_errors=True)
            if len(tried) >= 6:
                break
        if pil_source is None:
            sys.exit(
                f"No Pillow {PILLOW} in the MSYS2 {env_dir} repository matches GIMP's Python "
                f"(needs _imaging{ext_suffix}). Tried: {', '.join(tried) or 'none'}."
            )
        # GIMP bundles Pillow's other libraries (zlib, libjpeg, libtiff, ...); libimagequant
        # is the one it omits, so vendor it beside the compiled modules that load it.
        quant = msys2_versions(env_dir, prefix + "libimagequant")
        if not quant:
            sys.exit(f"Couldn't find libimagequant in the MSYS2 {env_dir} repository.")
        quant_extract = msys2_extract(env_dir, quant[0][2], work)
        shutil.copytree(pil_source, vendor / "PIL")
        quant_dlls = sorted(quant_extract.glob("**/bin/*imagequant*.dll"))
        if not quant_dlls:
            sys.exit("The MSYS2 libimagequant package contained no DLL.")
        for dll in quant_dlls:
            shutil.copy2(dll, vendor / "PIL" / dll.name)
        return pillow_version
    finally:
        shutil.rmtree(work, ignore_errors=True)


def verify_pillow(gimp_python, vendor):
    """Confirm GIMP's Python loads the vendored Pillow before we claim success."""
    check = subprocess.run(
        gimp_python + ["-c", VERIFY, str(vendor)],
        text=True,
        capture_output=True,
        check=False,
    )
    if check.returncode != 0:
        detail = (check.stdout + check.stderr).strip()
        sys.exit("GIMP's Python still can't load the vendored Pillow:\n" + detail)


def install_pillow(destination, gimp_python, model_python):
    """Vendor Pillow for GIMP's Python when GIMP doesn't provide a working copy."""
    vendor = destination / "vendor"
    shutil.rmtree(vendor, ignore_errors=True)
    probe = subprocess.run(gimp_python + ["-c", PROBE], text=True, capture_output=True, check=True)
    target = json.loads(probe.stdout.splitlines()[-1])
    if target["pillow"]:
        return None
    if target["platform"] == "win32":
        version = install_pillow_msys2(vendor, target)
        verify_pillow(gimp_python, vendor)
        return version
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
    dist = next(iter(vendor.glob("pillow-*.dist-info")), None)
    return dist.name.split("-")[1] if dist else target["version"]


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
        print(f"Vendored Pillow {pillow_version} for GIMP's Python.")
    print("Restart GIMP, open an image, select one pixel layer, then Select > Object Select.")
    print("Keep this repository and its .venv in place; the plugin launches its model worker here.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.stderr or str(error))
