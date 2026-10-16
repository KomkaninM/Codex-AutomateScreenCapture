# Build on Windows x64: pyinstaller --clean --noconfirm BMSControlPanel.spec
# One onedir bundle: windowed UI plus hidden console worker for piped logs.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
a = Analysis(
    [str(root / "app_entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / ".env.example"), "."),
        (str(root / "targets.json"), "."),
        (str(root / "scripts/macros/example.json"), "scripts/macros"),
    ]
    + collect_data_files("tzdata"),
    hiddenimports=[
        "launcher",
        "server",
        "macro_tool",
        "pyautogui",
        "pyperclip",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "mss.windows",
        "cv2",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
# PyInstaller's PySide6 hooks collect the Qt platform/image plugins and DLLs.
pyz = PYZ(a.pure)
gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BMSControlPanel",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
worker = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BMSWorker",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    gui, worker, a.binaries, a.datas, strip=False, upx=False, name="BMSControlPanel"
)
