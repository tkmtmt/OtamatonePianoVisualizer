# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path
import os
import glob
import site

hiddenimports = collect_submodules("aubio") + ["sounddevice"]

binaries = []

# 仮想環境 / conda環境の候補ディレクトリ
candidates = [
    os.path.join(os.environ.get("CONDA_PREFIX", ""), "Library", "bin"),
    os.path.join(os.environ.get("CONDA_PREFIX", ""), "DLLs"),
    os.path.join(os.environ.get("VIRTUAL_ENV", ""), "Scripts"),
]

dll_patterns = [
    "mkl_*.dll",
    "libiomp5md.dll",
    "svml_dispmd.dll",
    "libifcoremd.dll",
    "libmmd.dll",
]

for base in candidates:
    if base and os.path.isdir(base):
        for pattern in dll_patterns:
            for f in glob.glob(os.path.join(base, pattern)):
                binaries.append((f, "."))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=[("config.json", ".")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OtamaTuner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OtamaTuner",
)