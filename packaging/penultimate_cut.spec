# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Penultimate Cut -- a one-folder Windows build.

Build with (from the repo root, on Windows, with vendor/ already populated
by scripts/fetch_vendor.ps1):

    pyinstaller packaging/penultimate_cut.spec --noconfirm

Produces dist/PenultimateCut/PenultimateCut.exe plus everything beside it.

Two things here exist specifically because the obvious PyInstaller defaults
would silently break this app:

- `contents_directory="."` on the EXE -- PyInstaller 6+ otherwise nests all
  supporting files (the Python runtime, collected packages, and anything
  passed via `binaries=`/`datas=`) under a `_internal/` subfolder next to
  the exe. media/executables.vendor_dir() expects vendor/ to be a direct
  sibling of PenultimateCut.exe (`sys.executable`'s own directory), so this
  restores the classic flat onedir layout instead of special-casing
  PyInstaller's internal folder name in application code.
- `collect_data_files("opentimelineio")` -- OTIO resolves its adapters (the
  "otio_json" one io/otio_io.py uses for every save/load/autosave) through
  a plugin-manifest JSON file discovered at runtime via package data, not
  through any `import`. PyInstaller's static analysis has no way to see
  that file on its own; without this, save/open/export would all fail in
  the packaged build the moment they touched OTIO, with no equivalent
  failure in dev (where the manifest is just sitting on disk next to the
  package).
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

# Not __file__ -- PyInstaller exec()s this file directly rather than
# importing it as a module, so __file__ is never defined here. SPECPATH
# (the directory containing this .spec file) is PyInstaller's own
# injected equivalent for exactly this purpose.
repo_root = Path(SPECPATH).resolve().parent  # noqa: F821
vendor_dir = repo_root / "vendor"
resources_dir = repo_root / "penultimate_cut" / "resources"

vendor_binaries = [
    (str(vendor_dir / name), "vendor") for name in ("ffmpeg.exe", "ffprobe.exe", "libmpv-2.dll")
]

a = Analysis(
    [str(repo_root / "penultimate_cut" / "app.py")],
    pathex=[str(repo_root)],
    binaries=vendor_binaries,
    # resources/: the window icon app.py loads at runtime.
    datas=collect_data_files("opentimelineio") + [(str(resources_dir), "penultimate_cut/resources")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PenultimateCut",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(resources_dir / "icon.ico"),
    disable_windowed_traceback=False,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PenultimateCut",
)
