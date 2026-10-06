# Contributing to FrameAtlas

Thanks for helping. Bug reports, fixes and new features are all welcome.

## Prerequisites

- **Python 3.10 or newer** (`requires-python` in `pyproject.toml`).
  CI tests Python 3.10, 3.12 and 3.14 on Linux, macOS and Windows.
- **Git**.
- No separate `ffmpeg` executable is needed. PyAV wheels include the FFmpeg
  libraries. On platforms without wheels, PyAV may need a compiler and FFmpeg
  development libraries.
- No AI API key, hosted service or model download is needed.

## Setup

Fork the repository on GitHub, then clone your fork and install it in a
virtual environment with the development extras:

```sh
git clone https://github.com/<your-name>/frameatlas.git
cd frameatlas
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Run the tests

```sh
python -m pytest -q
```

The tests generate their own small videos, so you do not need any sample
footage.

The full 18,000-frame check is opt-in. It extracts a 300-second, 60 FPS
synthetic video and receives every frame over MCP. It takes several minutes:

```sh
python -m pytest -q --run-scale tests/test_scale.py
```

CI runs both. See [docs/validation.md](docs/validation.md) for what they cover.

## Build

```sh
python -m build
```

This writes the source distribution and wheel to `dist/`.

## Try it by hand

```sh
frameatlas extract video.mp4 --output datasets/my-video
frameatlas verify datasets/my-video
frameatlas serve datasets/my-video --port 8765
```

Then open http://127.0.0.1:8765/. To test the MCP server, point an
MCP-capable client at:

```sh
frameatlas mcp /absolute/path/to/datasets/my-video
```

`datasets/` and video files are ignored by Git (`.gitignore`).

## Propose a change

1. For a large change, open an issue first so we can agree on the approach.
2. Create a branch from `main` in your fork:
   `git checkout -b fix/short-description`
3. Make your change. Keep each pull request small and focused on one thing.
4. Add or update tests in `tests/` for the behaviour you change.
5. Run `python -m pytest -q` and make sure all tests pass. If you touch
   extraction, the MCP server or coverage, also run the scale test.
6. Push the branch and open a pull request against `main`. CI must pass.
7. In the description, say **what** you changed and **why**, and how you
   tested it (OS and Python version).

## Notes

- Do not commit videos, generated datasets or credentials.
- Files use LF line endings (`.gitattributes`).
- Security problems: do not open a public issue. See [SECURITY.md](SECURITY.md).

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).
