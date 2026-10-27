# Penultimate Cut

A simple video editor for Windows. Not the final cut. Just the one you turn in.

See `CLAUDE.md` for the full project brief (goals, architecture, feature phases).

## Download

Get the Windows installer (`PenultimateCutSetup-x.y.z.exe`) from the
[Releases page](https://github.com/xaintly/penultimate-cut/releases/latest).
Windows 10/11, 64-bit. Everything it needs (including ffmpeg) is bundled;
Python isn't required.

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org).

- Committers and reviewers: [xaintly](https://github.com/xaintly)
- Approvers: [xaintly](https://github.com/xaintly)

Release installers are built from this repository's source by GitHub
Actions (`.github/workflows/build-windows.yml`); only those builds are signed.

## Privacy policy

This program will not transfer any information to other networked systems
unless specifically requested by the user or the person installing or
operating it. It does not collect usage data, check for updates, or contact
any server; your videos and projects stay on your computer.

## Development setup

Requires Python 3.12+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the app:

```bash
python -m penultimate_cut.app
```

Run tests:

```bash
pytest
```

Lint and type-check:

```bash
ruff check .
mypy penultimate_cut/model penultimate_cut/export
```

## License

GPL-3.0-or-later (see `LICENSE`). The bundled ffmpeg build includes
`libx264` for H.264 encoding, which is itself GPL-licensed -- distributing
it requires the whole program to be GPL too.
