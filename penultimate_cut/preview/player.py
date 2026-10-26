"""libmpv wrapper: embeds mpv in a Qt widget, exposes seek/speed/frame-step.

Keeps every mpv-specific option, property, and command name in this one
module so `ui/preview_widget.py` never has to know mpv's API directly --
it just calls `load_edl`, `seek`, `frame_step`, sets `speed`, etc.

`wid=None` runs mpv headless (`vo=null`, `ao=null`) instead of embedding into
a window. That's what this module's own tests use, since there's no real
window to embed into without a visible UI; `ui/preview_widget.py` passes a
real native window id (`int(widget.winId())`) to get actual video output.

`load_unsafe_playlists` defaults to True because `preview/edl.py`'s gap
filler uses `av://lavfi:...` entries, which mpv otherwise refuses to open.

Verified empirically (not just from docs) that pitch-corrected speed change
needs no extra configuration: `audio-pitch-correction` defaults to on, so
setting `speed` alone gives the scaletempo2 behavior CLAUDE.md asks for.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

import mpv

from penultimate_cut.model.time import format_seconds, fraction_from_seconds

FrameStepDirection = Literal["forward", "backward"]


class Player:
    def __init__(
        self,
        wid: int | None = None,
        *,
        load_unsafe_playlists: bool = True,
        **extra_mpv_options: object,
    ) -> None:
        """`extra_mpv_options` are passed straight through to `mpv.MPV(...)`,
        overriding anything set above -- an escape hatch for tests (e.g.
        forcing `vo="null"` even with a real `wid`, to embed without the
        GL path at all)."""
        mpv_kwargs: dict[str, object] = {
            "load_unsafe_playlists": load_unsafe_playlists,
            # Stay paused on the last frame at the end of the timeline. mpv's
            # default unloads the file there and goes idle, after which play
            # and seek silently do nothing until the next edit reloads it.
            "keep_open": "yes",
        }
        if wid is not None:
            mpv_kwargs["wid"] = wid
            if sys.platform == "win32":
                # Recent mpv/libmpv builds default to vo=gpu-next
                # (libplacebo). It's known to produce a fully black window
                # when embedded via `wid=` on Windows -- its d3d11
                # output-format negotiation (`d3d11-output-format=auto`)
                # can pick a format the swapchain won't actually display,
                # while playback otherwise proceeds normally underneath
                # (duration/position tracking, pause state, all unaffected,
                # since none of that depends on a frame ever being drawn).
                # vo=gpu is the older, far-more-tested renderer for `wid=`
                # embedding specifically. Windows-only: this environment's
                # own real-GL test (test_preview_widget.py, software Mesa)
                # relies on the default and vo=gpu doesn't even initialize
                # against llvmpipe here -- an unrelated Linux/X11-vs-Windows
                # GPU-backend difference, not evidence against this fix.
                mpv_kwargs["vo"] = "gpu"
        else:
            mpv_kwargs["vo"] = "null"
            mpv_kwargs["ao"] = "null"
        mpv_kwargs.update(extra_mpv_options)
        self._mpv = mpv.MPV(**mpv_kwargs)
        self._edl_path: Path | None = None

    def __enter__(self) -> Player:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._mpv.terminate()
        if self._edl_path is not None:
            self._edl_path.unlink(missing_ok=True)

    # --- loading -----------------------------------------------------------

    def load_edl(self, edl_text: str, start: Fraction = Fraction(0)) -> None:
        """Write `edl_text` to this player's scratch `.edl` file and load it,
        positioned at `start` -- so reloading after an edit can keep the
        playhead where it was, instead of jumping back to the beginning (a
        seek issued right after loading is dropped while the load is still
        in flight, so this has to be part of the load itself).

        Reuses the same file across calls (overwriting it) rather than
        making a new one per edit, since the EDL is regenerated after every
        timeline change.

        Written with explicit `newline="\\n"` and UTF-8: on Windows,
        `write_text`'s defaults turn `\\n` into `\\r\\n` -- and mpv only
        recognizes an EDL whose first line is exactly `# mpv EDL v0\\n`, so
        it rejects the whole file as an unrecognized format -- and encode
        with the ANSI codepage, which breaks `edl.py`'s length prefixes
        (counted in UTF-8 bytes) for any non-ASCII path.
        """
        if self._edl_path is None:
            handle = NamedTemporaryFile(suffix=".edl", delete=False)
            self._edl_path = Path(handle.name)
            handle.close()
        self._edl_path.write_text(edl_text, encoding="utf-8", newline="\n")
        self._mpv.loadfile(str(self._edl_path), start=format_seconds(start))

    def unload(self) -> None:
        """Stop and show nothing -- e.g. when the timeline becomes empty."""
        with contextlib.suppress(SystemError):
            self._mpv.command("stop")

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Block until the most recent `load_edl` has finished loading (its
        duration is known), or `timeout` elapses. Returns False (never
        raises) on timeout/failure, so a bad load can be handled instead of
        crashing the caller.

        Deliberately not "wait until playing" (mpv's `core-idle` property):
        that means actively decoding, which never becomes true -- and so
        never stops waiting -- if the player is paused, which it very
        routinely is right when an edit triggers a reload.

        Two waits, not one, confirmed necessary by hitting the race between
        them: `duration` is level-sensitive (it checks the *current* value
        first), so right after calling `load_edl` a second time, it can see
        the *previous* file's still-current, still-non-None duration and
        return immediately -- before the new file has loaded at all. Waiting
        for the edge-triggered `file-loaded` event first guarantees we're
        then waiting on the new file's duration, not the old one's leftover
        value.
        """
        try:
            self._mpv.wait_for_event("file-loaded", timeout=timeout)
            self._mpv.wait_for_property("duration", lambda d: d is not None, timeout=timeout)
            return True
        except Exception:
            return False

    # --- playback control ----------------------------------------------------
    #
    # Each of these can transiently fail with a plain SystemError ("Error
    # running mpv command", python-mpv's own exception for libmpv's generic
    # COMMAND error) if it lands while a `load_edl` reload is still
    # in-flight -- a real, reachable timing window, not a hypothetical one:
    # every edit triggers a reload (see ui/main_window.py), so a click on
    # the timeline that arrives in that same instant hits exactly this.
    # python-mpv's ShutdownError (closed player) is also a SystemError
    # subclass, so this equally covers a stray call arriving after close().
    # Degrading to a no-op here is the right call either way -- there's
    # nothing a caller could usefully do differently with the exception.

    def play(self) -> None:
        """Resume playback -- from the start, if it had reached the end."""
        with contextlib.suppress(SystemError):
            if self._mpv.eof_reached:
                self._mpv.seek(0, reference="absolute", precision="exact")
            self._mpv.pause = False

    def pause(self) -> None:
        with contextlib.suppress(SystemError):
            self._mpv.pause = True

    def toggle_play_pause(self) -> None:
        with contextlib.suppress(SystemError):
            if self._mpv.pause:
                self.play()
            else:
                self.pause()

    @property
    def is_playing(self) -> bool:
        return not bool(self._mpv.pause)

    def seek(self, position: Fraction) -> None:
        """Seek to an absolute position, frame-exact (hr-seek)."""
        with contextlib.suppress(SystemError):
            self._mpv.seek(float(position), reference="absolute", precision="exact")

    def frame_step(self, direction: FrameStepDirection = "forward") -> None:
        with contextlib.suppress(SystemError):
            if direction == "forward":
                self._mpv.frame_step()
            else:
                self._mpv.frame_back_step()

    @property
    def position(self) -> Fraction | None:
        value = self._mpv.time_pos
        return None if value is None else fraction_from_seconds(value)

    @property
    def duration(self) -> Fraction | None:
        value = self._mpv.duration
        return None if value is None else fraction_from_seconds(value)

    @property
    def speed(self) -> Fraction:
        return fraction_from_seconds(self._mpv.speed)

    @speed.setter
    def speed(self, value: Fraction) -> None:
        self._mpv.speed = float(value)

    @property
    def volume(self) -> int:
        """Playback volume, 0-100 -- preview only; export never sees it."""
        return round(self._mpv.volume)

    @volume.setter
    def volume(self, value: int) -> None:
        with contextlib.suppress(SystemError):
            self._mpv.volume = max(0, min(100, value))

    def on_position_changed(self, callback: Callable[[Fraction | None], None]) -> None:
        """Register `callback` to fire whenever mpv's playback position changes
        (several times a second during playback) -- drives the UI playhead."""

        def _handler(_name: str, value: float | None) -> None:
            callback(None if value is None else fraction_from_seconds(value))

        self._mpv.observe_property("time-pos", _handler)

    def on_playing_changed(self, callback: Callable[[bool], None]) -> None:
        """Register `callback(is_playing)` to fire whenever playback starts
        or stops -- including mpv pausing itself at the end of the timeline,
        which no caller asked for."""

        def _handler(_name: str, paused: bool | None) -> None:
            callback(not paused)

        self._mpv.observe_property("pause", _handler)
