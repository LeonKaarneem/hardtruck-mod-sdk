#!/usr/bin/env python3
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12.7"
PYTHON_INSTALLER_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe"
PYTHON_INSTALLER_SHA256 = "1206721601a62c925d4e4a0dcfc371e88f2ddbe8c0c07962ebb2be9b5bde4570"
REQUIREMENTS = PROJECT_ROOT / "tools/requirements-windows-patcher.txt"
SPEC = PROJECT_ROOT / "tools/htmod-windows.spec"


class BuildError(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_pe64(path):
    data = Path(path).read_bytes()
    if len(data) < 0x100 or data[:2] != b"MZ":
        raise BuildError(f"frozen patcher is not a Windows PE executable: {path}")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_offset + 6 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise BuildError(f"frozen patcher has no PE header: {path}")
    machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
    if machine != 0x8664:
        raise BuildError(f"frozen patcher is not 64-bit AMD64: machine {machine:#06x}")


def wine_environment(prefix):
    environment = os.environ.copy()
    environment.update(
        WINEPREFIX=str(prefix),
        WINEARCH="win64",
        WINEDLLOVERRIDES="mscoree,mshtml=",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
    )
    return environment


def wine_command(arguments, prefix):
    command = []
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        command.extend(("xvfb-run", "-a"))
    command.extend(("wine", *map(str, arguments)))
    return command


def run_wine(arguments, prefix):
    return subprocess.run(
        wine_command(arguments, prefix),
        env=wine_environment(prefix),
        check=True,
    )


def windows_path(path, prefix):
    result = subprocess.run(
        ("wine", "winepath", "-w", str(path)),
        env=wine_environment(prefix),
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def download_python(installer):
    installer.parent.mkdir(parents=True, exist_ok=True)
    if not installer.is_file():
        temporary = installer.with_suffix(".download")
        urllib.request.urlretrieve(PYTHON_INSTALLER_URL, temporary)
        temporary.replace(installer)
    actual = sha256(installer)
    if actual != PYTHON_INSTALLER_SHA256:
        raise BuildError(f"Windows Python installer SHA-256 mismatch: {actual}")


def build(args):
    work = args.work_dir.resolve()
    prefix = work / "wineprefix"
    python = prefix / "drive_c/htmod-python/python.exe"
    installer = work / f"downloads/python-{PYTHON_VERSION}-amd64.exe"
    work.mkdir(parents=True, exist_ok=True)
    if prefix.exists():
        subprocess.run(("wineserver", "-k"), env=wine_environment(prefix), check=False)
        shutil.rmtree(prefix)
    for stale in (work / "dist", work / "pyinstaller"):
        if stale.exists():
            shutil.rmtree(stale)
    if not python.is_file():
        download_python(installer)
        run_wine(
            (
                installer,
                "/quiet",
                "InstallAllUsers=0",
                r"TargetDir=C:\htmod-python",
                "Include_pip=1",
                "Include_test=0",
                "PrependPath=0",
                "Shortcuts=0",
                "Include_doc=0",
                "Include_launcher=0",
            ),
            prefix,
        )
    if not python.is_file():
        raise BuildError(f"64-bit Windows Python was not installed at {python}")
    run_wine((python, "-m", "pip", "install", "--no-cache-dir", "--require-hashes", "-r", REQUIREMENTS), prefix)
    source_root = prefix / "drive_c/htmod-source"
    if source_root.exists():
        shutil.rmtree(source_root)
    source_root.mkdir(parents=True)
    for name in ("sdk", "modpack"):
        shutil.copytree(
            PROJECT_ROOT / name,
            source_root / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    (source_root / "tools").mkdir()
    for name in ("htmod_windows_entry.py", "htmod-windows.spec"):
        shutil.copyfile(PROJECT_ROOT / "tools" / name, source_root / "tools" / name)
    environment = wine_environment(prefix)
    environment["HTMOD_SOURCE_ROOT"] = r"C:\htmod-source"
    dist = work / "dist"
    build_work = work / "pyinstaller"
    command = wine_command(
        (
            python,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            windows_path(dist, prefix),
            "--workpath",
            windows_path(build_work, prefix),
            windows_path(source_root / "tools/htmod-windows.spec", prefix),
        ),
        prefix,
    )
    subprocess.run(command, env=environment, check=True)
    frozen = dist / "htmod.exe"
    verify_pe64(frozen)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    shutil.copyfile(frozen, temporary)
    temporary.replace(args.output)
    verify_pe64(args.output)
    print(f"Built {args.output}")
    print(f"Size: {args.output.stat().st_size} bytes")
    print(f"SHA-256: {sha256(args.output)}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build the 64-bit one-file Windows htmod patcher")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "htmod-windows-patcher-build",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        build(args)
    except (BuildError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"error: {error}") from error


if __name__ == "__main__":
    main()
