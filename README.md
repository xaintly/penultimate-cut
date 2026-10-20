# Penultimate Cut

A simple video editor for Windows. Not the final cut. Just the one you turn in.

See `CLAUDE.md` for the full project brief (goals, architecture, feature phases).

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
