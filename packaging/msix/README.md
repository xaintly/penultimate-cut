# Microsoft Store package

`scripts/build_msix.ps1` packs the PyInstaller build into
`packaging/dist/PenultimateCut-<version>.msix` (CI builds it too, as the
"PenultimateCut-Store-<version>" artifact). Upload that to Partner Center;
the Store signs it. Each submission needs a higher version than the last, so
bump `pyproject.toml` first.

To try the package before uploading: turn on Developer Mode (Settings →
System → For developers), build, then

    Add-AppxPackage -Register packaging\build\msix\AppxManifest.xml

It installs from that folder as it is, unsigned. Remove it with
`Get-AppxPackage Xaintly.PenultimateCut | Remove-AppxPackage`.

## What Partner Center asks for

Draft answers, to paste in.

**Category:** Photo & video. **Price:** Free.

**Restricted capability (runFullTrust) justification:**
Penultimate Cut is a desktop video editor (a Win32 app packaged with MSIX).
It needs full trust to run its bundled ffmpeg tools on the video files the
user chooses, and to save projects and exported videos where the user picks.

**Privacy policy URL:**
https://github.com/xaintly/penultimate-cut/blob/main/PRIVACY.md

**Website / support:** https://github.com/xaintly/penultimate-cut

**Short description:**
A simple video editor: cut, rearrange, add titles, and export a small MP4.

**Description:**

Penultimate Cut is a video editor for people trying to get something done: a
class assignment, a narrated slideshow, a training clip, a group project
stitched together from several people's recordings, family footage. Not the
final cut. Just the one you turn in.

Preview on top, timeline below, a few big buttons, and everything else one
right-click away.

- Import videos, photos, and audio, including old AVI, WMV, MPG and MOV
  files and phone videos.
- Split at the playhead, delete, and drag clips into order; trim the ends.
- Fade in from black, fade out to black, change a clip's volume or mute it.
- Add simple title cards.
- Undo anything.
- Export an MP4 that plays everywhere, with a choice of size and quality and
  an estimate of the file size before you start.
- Your work is saved as you go and comes back after a crash.

No account, no watermark, no ads, no data collected. Free and open source
(GPL-3.0); the source is at https://github.com/xaintly/penultimate-cut.

**Features (one per line):**
Cut, rearrange and trim clips on a simple timeline
Fade in and out, volume and mute for each clip
Title cards
Works with old AVI, WMV and MPG files and phone videos
Exports MP4 that plays in every browser and learning platform
Undo for every edit; automatic crash recovery
No account, no watermark, no ads; open source

**Search terms:** video editor, slideshow, trim video, cut video, join videos,
school project, MP4 (no other products' names: the Store treats them as
trademark misuse)

**Age rating questionnaire:** no to everything (no violence, user
interaction, purchases, location, or personal data); the app only edits the
user's own files.

**Screenshots:** at least one, 1366×768 or larger, of the app with a
project open.
