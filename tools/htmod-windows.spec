import os
from pathlib import Path

root = Path(os.environ["HTMOD_SOURCE_ROOT"])
modpack = root / "modpack"
datas = [
    (str(modpack / "release.json"), "modpack"),
    (str(modpack / "migrations.json"), "modpack"),
    (str(modpack / "mods"), "modpack/mods"),
]

a = Analysis(
    [str(root / "tools/htmod_windows_entry.py")],
    pathex=[str(root / "sdk")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "research"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# onefile: binaries and data are passed directly to EXE, with no COLLECT.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="htmod",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    codesign_identity=None,
    entitlements_file=None,
)
