"""Human-readable formatting shared across UI widgets."""

from __future__ import annotations

from fractions import Fraction


def format_duration(value: Fraction | None) -> str:
    """`MM:SS`, or `H:MM:SS` once there's at least an hour -- the media bin can
    hold a feature-length source clip, where plain `MM:SS` stops being legible."""
    if value is None:
        return "00:00"
    total_seconds = int(value)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
