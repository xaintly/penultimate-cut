# CLAUDE.md — Penultimate Cut

**Penultimate Cut** — a simple video editor.
*Not the final cut. Just the one you turn in.*

- Display name: `Penultimate Cut` · repo: `penultimate-cut` · Python package:
  `penultimate_cut` · executable: `PenultimateCut.exe` · project-file metadata
  namespace: `penultimate_cut`
- Store/winget/README subtitle: "Penultimate Cut — a simple video editor for Windows"
- The name is a gentle joke on Final Cut Pro. Never use Apple, Microsoft, Adobe, or
  Blackmagic product names, logos, icons, or artwork in the app or repo (comparisons in
  the README in plain text are fine).
- The project aims to be **finished**, not perpetually growing: once the phases below
  are complete, it is feature-complete by design. Bug fixes and format support are
  always welcome; new features must pass the feature filter.

## What this is

A Windows desktop video editor for **people trying to get something done**: a class
assignment, a narrated slideshow, a training clip, a group project stitched together
from several contributors, family footage. The video is a vehicle for something else;
the user wants it correct, clear, finished, and small enough to upload.

The spiritual predecessor is Windows Movie Maker: preview on top, timeline below, a few
big buttons, and everything else one right-click away.

### Who it is NOT for

- **Social content creators** (stickers, trending effects, stock libraries, animated
  captions, vertical-first templates) — well served by CapCut/Clipchamp.
- **Craft-focused editors** (color grading, audio post, multicam) — well served by
  DaVinci Resolve/Premiere/Kdenlive.

### The feature filter (apply to every proposed feature)

> **Does this make it easier for someone to finish a video without learning anything?**

Editing in this app means the video equivalent of cropping and rearranging: cut, remove,
reorder, join, adjust volume/speed, add simple text, export. A feature that *transforms*
the source material (effects, filters, compositing) needs a strong reason. When a feature
fails the filter, record it under Non-goals rather than building it.

**Claude: do not add features, settings, or UI beyond the current phase without asking.**
If something seems missing, propose it in a note; don't implement it.

---

## Technology decisions

| Area | Decision | Why |
|---|---|---|
| Language | **Python 3.12+** | Fastest iteration; owner has a dev background but hasn't coded professionally in a decade. Heavy lifting is done by mpv and ffmpeg, so Python's speed isn't the bottleneck. |
| UI toolkit | **PySide6 (Qt 6)** | Mature native-feeling Windows widgets, custom painting for the timeline, built-in `QUndoStack`. |
| Preview playback | **libmpv** via `python-mpv`, embedded in a `QWidget` (`wid` option) | Exact seeking (`hr-seek`), frame stepping, speed changes with pitch correction (`scaletempo2`), reads essentially every format including old AVI/WMV. |
| Timeline preview | **mpv EDL** (`edl://` / `# mpv EDL v0`) regenerated after each edit | Lets mpv play the cut list as one seamless stream without rendering. Effects that EDL can't express (transitions, captions, blur) are previewed in Phase 2+ via a short background render of the affected region. Title cards are drawn as pictures and play as an image list (`mf://`); see `preview/edl.py`. |
| Probing / thumbnails / waveforms / export | **ffmpeg + ffprobe** (bundled executables, called via `subprocess`) | Universal decoder; filtergraphs cover every planned feature. |
| Project file | **OpenTimelineIO (`.otio`)** via the `opentimelineio` Python package | Open standard (Academy Software Foundation). DaVinci Resolve and other tools can import OTIO, so a user can "graduate" a project to a bigger app. App-only features are stored in OTIO `metadata` under the `penultimate_cut` namespace; other apps ignore it but still get the cut. |
| Packaging | **PyInstaller (one-folder)** + **Inno Setup** installer | Produces a normal Windows installer and `.exe`. Bundle `ffmpeg.exe`, `ffprobe.exe`, `libmpv-2.dll`. |
| Target OS | **Windows 10/11, x64 only** | Don't spend effort on Linux/macOS. Qt is cross-platform, so avoid *gratuitous* Windows-only code, but never trade simplicity for portability. |

### Rejected alternatives (don't revisit without a reason)

- **Browser app** — cannot natively decode AVI/WMV; ffmpeg.wasm is slow and memory-capped,
  which fails the "several hours of source video" requirement.
- **Rust (Tauri/egui)** — viable, but embedding mpv and building a custom timeline is
  slower to iterate on. Revisit only if Python performance becomes a real problem
  (measure first).
- **MLT framework** — powerful, but its data model and build complexity are aimed at
  Shotcut/Kdenlive-scale editors.
- **Reading Movie Maker / Clipchamp projects** — large reverse-engineering effort for
  features we'd mostly discard. Non-goal.

### Licensing (decided)

- **The app is GPL-3.0**, published as an open-source GitHub project for personal use.
  Bundle a GPL ffmpeg build that includes `libx264` (best quality-per-byte for small files).
- Keep the encoder choice behind one function (`export/encoders.py`) with `libx264` as
  the default and Windows' built-in Media Foundation encoder (`h264_mf`) as a fallback
  option, so the encoder can be switched without touching anything else.
- **Shipping the licenses:** the installed app has a `licenses\` folder (GPL-3.0 text,
  `THIRD_PARTY_NOTICES.txt` naming each bundled component, its license and source, the
  ffmpeg build's own notice, and `BUILD_SOURCES.txt` with the exact ffmpeg/libmpv builds).
  `scripts/fetch_vendor.ps1` writes the vendor parts; `gather_licenses()` in the
  PyInstaller spec collects everything and fails the build if anything is missing.
  **Help → About** shows the version, license, source link, and links to that folder.
  Qt is used under its GPL-3.0 option, like the rest of the app.

---

## Architecture

```
penultimate_cut/
  app.py                 # entry point, QApplication setup
  model/                 # pure Python, NO Qt imports, NO subprocess
    project.py           # Project, Track, Clip, MediaRef dataclasses
    timeline_ops.py      # split, trim, move, delete, ripple — pure functions
    time.py              # rational timestamps (see "Time" below)
  commands/              # QUndoCommand subclasses wrapping timeline_ops
  io/
    otio_io.py           # Project <-> .otio (+ penultimate_cut metadata)
    autosave.py
  media/
    probe.py             # ffprobe wrapper -> MediaInfo
    thumbnails.py        # filmstrip frames, cached
    waveform.py          # audio peaks, cached
    cache.py             # per-media cache dir keyed by path+size+mtime
  preview/
    player.py            # libmpv wrapper (embed, seek, speed, frame-step)
    edl.py               # Project -> mpv EDL text (pure, testable)
  export/
    filtergraph.py       # Project -> ffmpeg args (pure, testable)
    encoders.py          # encoder selection + presets
    runner.py            # runs ffmpeg, parses progress, cancel support
  ui/
    main_window.py
    media_bin.py
    timeline/            # custom-painted timeline widget
    preview_widget.py
    clip_properties.py   # per-clip panel (fit mode, volume, mute, fades, title text/colors; later speed, rotate)
    titles.py            # draws title cards (Qt) as pictures for preview and export
    export_dialog.py
tests/
  conftest.py            # generates synthetic test media with ffmpeg lavfi
packaging/
  penultimate_cut.spec   # PyInstaller
  installer.iss          # Inno Setup
vendor/                  # ffmpeg.exe, ffprobe.exe, libmpv-2.dll (gitignored; fetched by script)
```

### Core rules

1. **Non-destructive editing.** Source files are never modified. A project is a list of
   references with in/out points plus settings. Rendering happens only at export.
2. **The model is pure.** `model/`, `preview/edl.py`, and `export/filtergraph.py` take
   data in and return data out. No Qt, no subprocess, no file I/O. This is where most
   tests live.
3. **Every edit is an undoable command.** All model mutations go through a
   `QUndoCommand` pushed onto the project's `QUndoStack`. No direct model mutation from
   UI code — ever. This is the rule that makes undo/redo work; breaking it once breaks it
   everywhere.
4. **UI never blocks.** Probing, thumbnails, waveforms, proxies, and export run in
   worker threads/processes with progress and cancel.
5. **Time.** Store times as rational numbers (`fractions.Fraction` seconds or OTIO
   `RationalTime`), never floats, so splits land on exact frames and repeated edits
   don't drift. Snap edit points to the source clip's frame boundaries.

### Project file details

- Saved as `.otio`. Media references store both the **absolute path** and a **path
  relative to the project file**; on load, try relative first, then absolute, then
  prompt the user to relink missing media (search the folder they pick for matching
  filenames).
- App-specific data (fades, normalize, titles, blur regions, export settings) lives in
  `metadata["penultimate_cut"]` with a `schema_version` integer. Write a migration whenever
  the schema changes.
- **Autosave** every 60 seconds (and on export start) to `<project>.otio.autosave`.
  On launch, if an autosave is newer than its project, offer to recover it.
- **Default folder:** `Videos\PenultimateCut` (the user's Videos folder as Windows
  reports it, since it can be moved), created on first use. New projects are saved
  there; exports default to `<project name>.mp4` next to the project, never
  overwriting an existing file. A setting to change this folder is a Phase 2 candidate.
- **App settings** (not per-project, e.g. preview volume) live in `QSettings`
  (`ui/settings.py`), never in the `.otio`. The preview volume slider only affects
  playback while editing, never the export.

---

## UI principles

- Main window: **preview** (top), **media bin** (left or top-left), **timeline**
  (bottom), and a slim bar of labeled buttons on top of the timeline: Import, Split,
  Delete, Add Title, Export.
- Icons (these buttons, the preview's icon-only play controls) come from Windows' own
  icon font through Qt's theme icons (`QIcon.ThemeIcon`, Qt 6.7+), so there are no
  image files to ship. The app icon is `penultimate_cut/resources/icon.png`; run
  `scripts/make_icon.py` after changing it to rebuild `icon.ico` (all sizes Windows
  uses).
- **No modes.** No razor tool, no selection-vs-cut tool switching. Splitting always
  happens **at the playhead** (button or `S`).
- Everything clip-specific lives in the **clip properties panel** and the **right-click
  menu**. Unused features stay out of sight.
- Plain language: "Make a smaller file", not "Bitrate". "Fade in from black", not
  "Opacity keyframe".
- Every destructive-looking action is undoable, so no "Are you sure?" dialogs except
  when closing with unsaved changes.

### Keyboard shortcuts

| Key | Action |
|---|---|
| Space | Play / pause; while playing faster or slower, back to normal speed |
| L | Faster: 1.5× → 2× → 4× → 10× (starts playing if paused) |
| S | Split at playhead |
| Delete | Remove selected segment and leave empty space (like Movie Maker/Clipchamp) |
| Shift+Delete | Remove selected segment and close the gap (ripple) |
| ← / → | Step one frame back / forward |
| Shift+← / → | Jump 1 second |
| + / − | Zoom timeline in / out (around the playhead) |
| Mouse wheel | Zoom timeline around the pointer; Shift+wheel scrolls |
| Ctrl+I | Import media |
| Ctrl+T | Add a title card at the playhead |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Ctrl+S | Save |
| Home / End | Jump to start / end |

Keys live on menu actions so the menus show them (**File**: Import Media…; **Edit**: Undo,
Redo, Split, Delete Clip, Delete and Close Gap, Add Title; **View**: Zoom In/Out on Timeline; **Play**: Play /
Pause, Faster, Playback Speed ▸ 0.1×–10×, frame/second steps, Go to Start/End; **Help**:
About). Only Delete and Shift+Delete are plain shortcuts. No reverse
play (J) or separate pause (K): clicking the timeline or stepping back covers it.
The current speed shows next to the time when it isn't 1×.

Empty space on the timeline (a `Gap`) plays as black + silence. Right-click it for
**Delete empty space**; right-click anywhere on the timeline for **Delete all empty
space**. Selecting empty space and pressing Delete also closes it.

On the timeline, **right-click and drag** scrolls (the content follows the mouse);
a right-click without dragging opens the menu. Left-drag stays reserved for moving
and trimming clips. In the media bin, Delete or right-click → **Remove from project**
removes media: silently if no clip uses it, otherwise after a confirmation (its clips
may be off-screen), as one undoable edit. Right-click a clip → **Delete clip**, or
**Edit → Delete Clip** for the clip under the playhead (greyed out when there's none);
both leave empty space, like Delete. **Delete and close gap** (right-click, Edit menu)
is the same with later clips moving up, like Shift+Delete. Hovering over a clip shows a
tooltip: its file name, which part of the original it uses, and its length (for empty
space: what it is and how to remove it). The timeline area is only as tall as its one
track needs; the preview gets the rest.

---

## Export

Export dialog has **three choices only**:

- **Resolution:** Automatic (shown with its size, e.g. "Automatic (1920×1080)") ·
  1080p · 720p · 480p · 360p. Automatic (`model/project.py: auto_resolution`) takes
  the shape covering the most timeline time and the sharpest clip of that shape,
  capped at 1080p; never decided by just the first clip. The named sizes apply to the
  short side (720p vertical = 720×1280) and never upscale.
- **Quality:** Small file · Balanced · High quality
- **File:** name and location

Show an **estimated file size** that updates as choices change.

Fixed output: **MP4, H.264 video (yuv420p, `+faststart`), AAC audio (stereo, 48 kHz).**
This plays in every browser, LMS, and media player that matters.

- Small file: aggressive compression, lower audio bitrate (good for narrated slides
  and mostly-static video).
- Project frame rate defaults to the first video clip's rate; other clips are conformed.
- Media sizes are **display** sizes (phone rotation tags and non-square pixels
  applied, in `media/probe.py`); export squares pixels and stamps every segment
  SAR 1:1, which ffmpeg's concat requires.
- Mixed aspect ratios (vertical phone video, old 4:3 footage): per-clip **Fit mode**
  - **Black bars** (letterbox/pillarbox) — default
  - **Blurred background** — the clip scaled to cover the frame, cropped, heavily
    blurred, and dimmed slightly, with the fitted clip centered on top (the familiar
    YouTube treatment for vertical video). Implement as ffmpeg `split` → cover-scale +
    `crop` → downscale → `gblur`/`boxblur` → upscale, then `overlay` the fitted copy.
    Blur at reduced resolution for speed. Apply the same filter in mpv for preview.
  - **Crop to fill** — scale to cover and crop the overflow
- When importing a clip whose aspect ratio differs from the project, default its Fit mode
  to Black bars but make Fit mode prominent in the clip properties panel.
- Long exports show progress, time remaining, and a Cancel button. Cancelling deletes
  the partial file.

---

## Feature phases

### Phase 1 — Core (the everyday editor)

- Import video, audio, and still images (drag-and-drop or button); old formats
  (AVI, WMV, MPG, MOV) must work. Audio-only files import, but using them on the
  timeline waits for Phase 2's audio track (the single Phase 1 track is video; for
  now an audio-only clip can be dropped there, and export refuses it).
- Media bin with thumbnails and durations.
- Timeline: one video track + its linked audio; scroll and zoom; filmstrip thumbnails;
  audio waveform. Both scale to multi-hour files: filmstrip tiles keep the video's
  shape (never stretched) and only on-screen tiles are made; the waveform is computed
  once per media file by ffmpeg (`astats`) and shared by every clip cut from it.
- Preview sliders (position, volume): a click anywhere on the line jumps there.
- Playhead: click/drag to scrub, play/pause, speed up/slow down playback, frame step.
- **Split at playhead.**
- Remove segments (ripple and non-ripple).
- Move segments by dragging; **trim handles** on clip edges.
- Still images get a default duration (5 s), adjustable by trimming.
- **Undo/redo** for every edit.
- Save/open projects (`.otio`), autosave + recovery, relink missing media.
- Export (see Export section).
- Pulled in from Phase 2 for 1.0: per-clip **volume** (0–200%) and **mute**, and **fade in
  from black / fade out to black** (picture and sound, a fixed 1 s, shortened on short
  clips; splitting keeps fades at the clip's outer ends). Set in the clip properties
  panel; shown on the timeline (fade wedges, waveform height, "· muted"). Preview applies
  them with one whole-timeline mpv filter chain switched on per clip span
  (`preview/edl.py: preview_filters`); export applies them per clip segment.
- An empty timeline shows "Drag videos here, or click Import".
- Also pulled in for 1.0: **title cards** (Add Title, Ctrl+T). A full-screen card of
  centered text on a color (default dark blue `#012456`, white text, a soft shadow, a
  slight top-to-bottom shade), 5 s, fading in and out. Inserted before whatever is under
  the playhead (at the end once past everything), then selected with its text ready to
  type. Text and the two colors are edited in the clip properties panel; typing is applied
  a pause at a time and undoes as one step. Otherwise a title is a still image: trim,
  split, move, delete, and fades all work unchanged. Model: `MediaRef.title` (a frozen
  `Title`); saved as an OTIO `GeneratorReference`. Drawn by Qt (`ui/titles.py`) at the
  output size, the same picture for preview and export; text shrinks to fit.

**Phase 1 is done when** the owner can take a narrated slideshow plus two phone clips,
cut and rearrange them, and export a small MP4 that plays in a browser — without
reading any instructions.

### Phase 2 — Occasional needs

- Detach audio from video; edit audio separately.
- Audio-only files (music, narration) on the timeline, via an additional audio track.
- Additional audio track (music); **audio mix** slider (favor narration vs. music);
  optional automatic **ducking** under speech.
- **Normalize loudness** across the whole project (ffmpeg `loudnorm`, two-pass) —
  for group projects merged from several contributors.
- **Transitions** between clips: cross-dissolve and fade-through-black only.
- **Captions** (text over a clip) and scrolling **credits**; more title options (a few
  clean built-in styles; font, size, position) on top of 1.0's basic title cards.
- **Speed change with pitch correction** per clip, plus **"Fit to length…"**: enter a
  target total duration (e.g. 5:00–8:00) and the app computes and applies one uniform
  speed factor (with a warning above ~1.25× or below ~0.8×).
- **Record narration** from the microphone while the preview plays.
- **Rotate** 90° and per-clip **Fit mode**: black bars, blurred background, or crop to
  fill (see Export section).
- **Group** two or more clips so they move together until ungrouped (needs
  multi-select on the timeline first).

### Phase 3 — Helpful extras

- **Region blur / solid redaction box**, static, active from time X to Y.
  Offer "solid box" as the recommended style for text; mild blur can be reversible.
- **Keyframed region**: set the box position at a few times; interpolate between them.
- **Snapshot**: save current frame as PNG.
- **Freeze frame**: hold the current frame for N seconds.
- **Pan & zoom** on still images (reuses the keyframe system).
- **Auto-captions** via whisper.cpp (offline), editable before burning in or exporting
  as `.srt`.
- **Proxies**: background-generated low-res, short-GOP copies for smooth scrubbing of
  long or high-res sources; used for preview only, never export.

### Maybe later (only if the filter says yes)

- Automatic face detection + tracking for blur (detection is easy; reliable tracking
  with review/correction is the real work).
- Webcam / screen recording (PowerPoint's Record feature and OBS already cover this).

### Non-goals

Stickers, stock media libraries, green screen/chroma key, background removal, AI
auto-compose, color grading, LUTs, decorative filters (beyond maybe black & white),
fancy transitions, multicam, picture-in-picture compositing, reading other apps'
native project files, Linux/macOS builds, vertical-video templates.

---

## Testing

- **Never commit copyrighted sample media.** Tests generate synthetic media with ffmpeg
  `lavfi` sources (`testsrc2`, `sine`, `anullsrc`), including an AVI and a WMV fixture
  and clips with mismatched frame rates/aspect ratios.
- Pure modules (`model/`, `edl.py`, `filtergraph.py`, `otio_io.py`) get thorough unit
  tests, including round-trip save/load and undo/redo sequences.
- `filtergraph.py`: golden tests comparing generated ffmpeg args for known projects.
- Export integration tests: render short projects, verify with ffprobe that duration
  (±1 frame), resolution, codecs, and audio presence are correct.
- Before calling a UI feature done, run the app and actually exercise it.

## Working conventions for Claude

- Run `pytest` before declaring any task complete; keep the suite fast (< 1 min) by
  using short synthetic clips.
- Type hints everywhere; `ruff` for lint/format; `mypy` on `model/` and `export/`.
- Small, focused commits with descriptive messages.
- When a requirement is ambiguous, choose the option that is **simpler for a
  first-time user** and note the choice.
- Update this file when an architectural decision changes; keep the Non-goals list
  current.
