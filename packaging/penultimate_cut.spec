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

import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

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


def gather_licenses() -> Path:
    """Copy every license text and notice the app must ship with into one
    folder (renamed so each says whose it is); it lands as licenses/ beside
    the exe. Fails the build if any is missing rather than shipping without
    it. See packaging/THIRD_PARTY_NOTICES.txt for what each covers."""
    folder = Path(workpath) / "licenses"  # noqa: F821 -- injected by PyInstaller
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    sources = {
        "GPL-3.0.txt": repo_root / "LICENSE",
        "THIRD_PARTY_NOTICES.txt": repo_root / "packaging" / "THIRD_PARTY_NOTICES.txt",
        "Python-LICENSE.txt": Path(sys.base_prefix) / "LICENSE.txt",
        # Written by scripts/fetch_vendor.ps1 from the downloaded builds.
        "FFmpeg-LICENSE.txt": vendor_dir / "licenses" / "FFmpeg-LICENSE.txt",
        "BUILD_SOURCES.txt": vendor_dir / "licenses" / "BUILD_SOURCES.txt",
    }
    for package, file_name, target in [
        ("opentimelineio", "LICENSE.txt", "OpenTimelineIO-LICENSE.txt"),
        ("opentimelineio", "NOTICE.txt", "OpenTimelineIO-NOTICE.txt"),
        ("python-mpv", "LICENSE.GPL", "python-mpv-LICENSE.GPL.txt"),
        ("python-mpv", "LICENSE.LGPL", "python-mpv-LICENSE.LGPL.txt"),
    ]:
        dist = distribution(package)
        found = [f for f in dist.files or [] if f.name == file_name]
        if not found:
            raise SystemExit(f"{package}'s {file_name} not found in its installed metadata")
        sources[target] = Path(dist.locate_file(found[0]))
    for target, source in sources.items():
        if not source.is_file():
            raise SystemExit(f"license file missing: {source} (for {target})")
        shutil.copyfile(source, folder / target)
    return folder


a = Analysis(
    [str(repo_root / "penultimate_cut" / "app.py")],
    pathex=[str(repo_root)],
    binaries=vendor_binaries,
    # resources/: the window icon app.py loads at runtime. The package's
    # own metadata: Help > About reads the version from it.
    datas=collect_data_files("opentimelineio")
    + copy_metadata("penultimate-cut")
    + [(str(resources_dir), "penultimate_cut/resources"), (str(gather_licenses()), "licenses")],
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
