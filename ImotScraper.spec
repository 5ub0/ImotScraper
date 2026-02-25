# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        # stdlib
        'logging',
        'threading',
        'webbrowser',
        're',
        'io',
        'queue',
        'sqlite3',
        'smtplib',
        'email.mime.text',
        'email.mime.multipart',
        # PyQt6
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.sip',
        # third-party
        'requests',
        'bs4',
        'urllib3',
        'PIL',
        'PIL.Image',
        'PIL.ImageQt',
        # project packages
        'controller',
        'controller.app_controller',
        'database',
        'database.db_manager',
        'email_service_module',
        'email_service_module.email_service',
        'gui',
        'gui.imot_gui_qt',
        'gui.theme_qt',
        'scheduler',
        'scheduler.scheduler_service',
        'scraper',
        'scraper.imotBgScraper',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tests'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ImotScraper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    distpath='dist',
)