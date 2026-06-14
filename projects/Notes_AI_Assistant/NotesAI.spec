# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('.env', '.')],
    hiddenimports=['anthropic', 'dotenv', 'webview', 'pypdf', 'docx'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=None,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NotesAI',
    debug=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='NotesAI',
)

app = BUNDLE(
    coll,
    name='Notes AI Assistant.app',
    icon='icon.icns',
    bundle_identifier='com.notesai.assistant',
    info_plist={
        'NSAppleEventsUsageDescription': 'Notes AI Assistant needs access to Notes to read your notes.',
        'CFBundleShortVersionString': '1.0.0',
    },
)
