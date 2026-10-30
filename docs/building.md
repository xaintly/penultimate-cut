# Building and releasing

How a source checkout becomes `PenultimateCutSetup-x.yy.exe` on the Releases
page, plus the helper scripts that support it.

## The short version: making a release

1. Make sure everything passes:

   ```powershell
   pytest
   ruff check .
   mypy penultimate_cut/model penultimate_cut/export
   ```

2. Bump `version` in `pyproject.toml` (versions so far: 0.76, 0.77, 0.90).
   This is the only place the version lives. Help → About, the installer
   file name and the release title all read it from here.
3. Commit it as `Version x.yy`, tag it, and push both:

   ```powershell
   git commit -am "Version 0.91"
   git tag v0.91
   git push origin main v0.91
   ```

4. The tag starts the **Build Windows installer** workflow
   (`.github/workflows/build-windows.yml`), which takes several minutes. When it
   succeeds, a GitHub Release named "Penultimate Cut x.yy" appears with the
   installer attached and release notes generated from the commits since the
   last tag. You can edit the notes on GitHub afterwards.
5. Install it on a clean Windows machine or VM and try it before announcing
   it.

**If the tag build fails:** fix the problem and release the next version
number. Re-using a tag means deleting it locally and on GitHub
(`git tag -d v0.91`, then `git push origin :refs/tags/v0.91`) and deleting
any half-made release. That works, but a fresh number is simpler, and no one
will mind a skipped version.

## What the build does

The workflow runs on GitHub's Windows machines. You can repeat each step
locally (next section).

| Step | Tool | Produces |
|---|---|---|
| Install the app and PyInstaller | pip | |
| Fetch ffmpeg, ffprobe, libmpv and their license notes | `scripts/fetch_vendor.ps1` | `vendor/` |
| Bundle Python, the app and `vendor/` into a folder | PyInstaller, `packaging/penultimate_cut.spec` | `dist/PenultimateCut/` |
| Wrap that folder in an installer | Inno Setup, `packaging/installer.iss` | `packaging/dist/PenultimateCutSetup-x.yy.exe` |
| Attach it to a GitHub Release (tag builds only) | `softprops/action-gh-release` | the Releases page |

A tag isn't the only way to start the workflow. You can also run it by hand
from the repo's **Actions** tab → **Build Windows installer** → **Run
workflow**. A manual run builds the installer as a downloadable artifact
(GitHub login required, deleted after 90 days) without publishing a release.
That's a quick way to test a packaging change.

## Building locally

Use a local build when you need to check the packaged app quickly, without
waiting for GitHub.

### One-time setup

- Python 3.12 or newer
- [7-Zip](https://7-zip.org) (`fetch_vendor.ps1` uses it to unpack mpv)
- [Inno Setup 6](https://jrsoftware.org/isinfo.php), only if you want the
  installer and not just the app folder

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]" pyinstaller
powershell -ExecutionPolicy Bypass -File scripts/fetch_vendor.ps1
```

`vendor/` is not in git. Run `fetch_vendor.ps1` again whenever you want
newer ffmpeg/mpv builds. It always downloads the current ones, so two builds
made a month apart can bundle different versions. `vendor/licenses/BUILD_SOURCES.txt`
(shipped in the app's `licenses` folder) records exactly which builds were
used.

### Build

From the repo root:

```powershell
pyinstaller packaging/penultimate_cut.spec --noconfirm
```

This produces `dist/PenultimateCut/PenultimateCut.exe`. You can run it
straight from that folder; it's exactly what gets installed. Then, if you
want the installer too:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging/installer.iss /DAppVersion=0.91
```

This produces `packaging/dist/PenultimateCutSetup-0.91.exe`. If you leave out
`/DAppVersion`, the installer is labelled `0.0.0-dev`.

## Helper scripts and files

### `scripts/fetch_vendor.ps1`

Downloads the bundled programs into `vendor/`:
- ffmpeg and ffprobe, from BtbN's GPL build
- libmpv, from shinchiro's build

It also copies ffmpeg's license and writes `vendor/licenses/BUILD_SOURCES.txt`.
It looks up the current download links each time it runs, because both
projects rotate their files. If it fails because a link or archive layout has
changed, the error message says which part. Check the project's releases
page and adjust the matching pattern in the script.

### `scripts/make_icon.py`

Rebuilds `penultimate_cut/resources/icon.ico` from `icon.png`, at every size
Windows uses (16–256 px). Run it whenever you change the artwork:

```powershell
python scripts/make_icon.py
```

The script trims the transparent margin automatically. Even so, artwork that
fills its square and has bold shapes reads much better at 16 px. Check the
result in the title bar and taskbar of a local build. The installer and the
exe pick up the new `.ico` on the next build.

### License files

Every installed copy carries a `licenses\` folder, linked from Help → About.
The PyInstaller spec's `gather_licenses()` collects it from:
- `LICENSE`
- `packaging/THIRD_PARTY_NOTICES.txt`
- Python's own license
- the license files of the installed OpenTimelineIO and python-mpv packages
- `vendor/licenses/`

The build **stops** if any of these is missing, so a release can't
accidentally ship without them.

If you add a new bundled dependency:
1. Add a section for it to `THIRD_PARTY_NOTICES.txt` (license and source
   link).
2. If its license requires shipping its license text, add that file to
   `gather_licenses()`.

### Code signing

The README describes signing through SignPath, but it isn't part of the
workflow yet. Releases are currently unsigned, which is why Windows shows
"Unknown publisher". Once SignPath approves the project, signing needs two
extra steps in the workflow:
- sign `PenultimateCut.exe` after PyInstaller and before Inno Setup packs it;
- sign the installer before "Publish GitHub Release".
