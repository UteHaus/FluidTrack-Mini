# -*- mode: python ; coding: utf-8 -*-
import glob
import os
import sys

# uv's standalone Python links _tkinter against libtcl9*.so via $ORIGIN-relative
# paths, which PyInstaller does not pick up on its own -> bundle them explicitly.
_py_lib = os.path.join(sys.base_prefix, "lib")
tk_binaries = [
    (lib, ".")
    for pattern in ("libtcl*.so*", "libtk*.so*")
    for lib in glob.glob(os.path.join(_py_lib, pattern))
]


a = Analysis(
    ['src/tray_app.py'],
    pathex=[],
    binaries=tk_binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FluidTrack-Mini',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FluidTrack-Mini',
)
