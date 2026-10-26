"""Runs ffmpeg, parses progress, supports cancellation.

`run_export` is a plain blocking call with progress/cancel callbacks -- it
has no Qt dependency and doesn't start its own thread. The UI layer is
responsible for calling it from a worker thread (per CLAUDE.md's "UI never
blocks" rule) and for how cancellation gets triggered (a button setting a
flag that `cancel_check` reads).

Takes `args` (everything ffmpeg needs *except* the executable itself, `-y`,
and the output path) plus `output_path` and `total_duration` separately, so
callers just concatenate `build_filtergraph(...)`'s input/filter/map args
with `encoder_args(...)` and hand the result straight in -- this module
doesn't know about `Project`, `FFmpegPlan`, or quality tiers at all.

Progress is read from `-progress pipe:1` (machine-readable key=value blocks)
rather than scraping ffmpeg's human-readable stderr stats line. stderr is
drained concurrently on a background thread purely to avoid a classic
subprocess deadlock (ffmpeg blocking on a full stderr pipe while this
process waits on stdout) -- its only other job is giving `RunnerError`
something useful to say if ffmpeg fails.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import IO

from penultimate_cut.media.executables import ExecutableNotFoundError, locate


class RunnerError(RuntimeError):
    """ffmpeg exited with an error (and the export wasn't cancelled)."""


class ExportCancelled(Exception):
    """Raised after `cancel_check` returns True; the partial output file is
    already deleted by the time this propagates."""


@dataclass
class ExportProgress:
    fraction: float
    """0.0-1.0, clamped. Can exceed source precision near the very end."""
    out_time: Fraction
    """Seconds of output encoded so far."""
    speed: float
    """Encoding speed multiplier; 1.0 is realtime, 2.0 is twice as fast."""


def run_export(
    args: list[str],
    output_path: Path,
    total_duration: Fraction,
    on_progress: Callable[[ExportProgress], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """Run ffmpeg with `args`, writing to `output_path`.

    Calls `on_progress` as ffmpeg reports progress. If `cancel_check` is
    given and returns True, terminates ffmpeg, deletes `output_path` if it
    exists, and raises `ExportCancelled`. Raises `RunnerError` if ffmpeg
    exits with a nonzero status for any other reason.
    """
    try:
        ffmpeg = locate("ffmpeg")
    except ExecutableNotFoundError as exc:
        raise RunnerError(str(exc)) from exc

    full_args = [ffmpeg, "-y", *args, "-progress", "pipe:1", "-nostats", str(output_path)]
    process = subprocess.Popen(
        full_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    stderr_lines: list[str] = []
    stderr_thread = threading.Thread(target=_drain, args=(process.stderr, stderr_lines))
    stderr_thread.start()

    cancelled = False
    try:
        for block in _progress_blocks(process.stdout):
            if cancel_check is not None and cancel_check():
                cancelled = True
                process.terminate()
                break
            if on_progress is not None:
                progress = _parse_progress(block, total_duration)
                if progress is not None:
                    on_progress(progress)
    except BaseException:
        # Nobody is reading ffmpeg's stdout any more, so it could block
        # forever on a full pipe -- stop it rather than wait on it.
        process.kill()
        process.wait()
        stderr_thread.join()
        output_path.unlink(missing_ok=True)
        raise
    finally:
        process.wait()
        stderr_thread.join()

    if cancelled:
        output_path.unlink(missing_ok=True)
        raise ExportCancelled

    if process.returncode != 0:
        output_path.unlink(missing_ok=True)
        tail = "\n".join(stderr_lines[-20:])
        raise RunnerError(f"ffmpeg exited with status {process.returncode}:\n{tail}")


def _drain(stream: IO[str], into: list[str]) -> None:
    for line in stream:
        into.append(line.rstrip("\n"))


def _progress_blocks(stream: IO[str]) -> Iterator[dict[str, str]]:
    """Yield each `-progress` block as a dict, split on its `progress=...` terminator line."""
    block: dict[str, str] = {}
    for line in stream:
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        block[key] = value
        if key == "progress":
            yield block
            block = {}


def _parse_progress(block: dict[str, str], total_duration: Fraction) -> ExportProgress | None:
    """None for a block with no usable time -- ffmpeg sometimes reports
    `out_time_us=N/A` mid-export (seen with several clips and gaps, while it
    flushes one stream), and such a block simply carries no progress."""
    try:
        out_time = Fraction(int(block["out_time_us"]), 1_000_000)
    except (KeyError, ValueError):
        return None
    fraction = float(out_time / total_duration) if total_duration > 0 else 0.0
    fraction = max(0.0, min(1.0, fraction))
    speed_str = block.get("speed", "0x").rstrip("x").strip()
    try:
        speed = float(speed_str)
    except ValueError:
        speed = 0.0
    return ExportProgress(fraction=fraction, out_time=out_time, speed=speed)
