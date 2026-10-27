"""Every ffmpeg/ffprobe launch must pass `creationflags=NO_WINDOW`.

Forgetting it can't be seen when running from a terminal (the child shares
the terminal's console), only in the installed app, where each run flashes
its own console window. So check the source instead."""

import ast
from pathlib import Path

import penultimate_cut

_LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output"}


def test_every_subprocess_launch_hides_its_console_window() -> None:
    package_dir = Path(penultimate_cut.__file__).parent
    launches = []
    for source in package_dir.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr in _LAUNCHERS
            ):
                flags = [kw.value for kw in node.keywords if kw.arg == "creationflags"]
                hidden = any(isinstance(v, ast.Name) and v.id == "NO_WINDOW" for v in flags)
                launches.append((f"{source.name}:{node.lineno}", hidden))
    assert launches, "found no subprocess launches at all -- has the check gone stale?"
    assert [where for where, hidden in launches if not hidden] == []
