# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['screenshot_tool.py'],
    pathex=[],
    binaries=[],
    datas=[('pinshot.ico', '.')],
    hiddenimports=['winrt.windows.media.ocr', 'winrt.windows.graphics.imaging', 'winrt.windows.storage.streams', 'winrt.windows.foundation', 'pystray', 'pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter.test', 'test', 'unittest', 'pydoc', 'doctest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PinShot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['pinshot.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='PinShot',
)
