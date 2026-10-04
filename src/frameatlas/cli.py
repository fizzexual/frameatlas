from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import av

from . import __version__
from .coverage import Coverage
from .dataset import Dataset
from .extract import extract


def main(argv=None):
    parser = argparse.ArgumentParser(prog="frameatlas", description="Every decoded video frame, accessible to people and AI agents.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("extract", help="Decode every frame; keep native RGB PNGs and exact timestamps")
    command.add_argument("video", type=Path)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--stream", type=int, default=0, help="Video stream ordinal, excluding audio streams")
    command.add_argument("--thumbnail-width", type=int, default=320)
    command.add_argument("--png-compression", type=int, default=1, choices=range(10))
    command.add_argument("--reserve-mib", type=int, default=256)
    for name in ("info", "verify", "frames", "frame", "sheet", "coverage", "serve", "mcp"):
        command = commands.add_parser(name)
        command.add_argument("dataset", type=Path)
        if name in {"frames", "sheet"}:
            command.add_argument("--start", type=int, default=0)
            command.add_argument("--count", type=int, default=32)
        if name == "frame":
            position = command.add_mutually_exclusive_group(required=True)
            position.add_argument("--index", type=int)
            position.add_argument("--time", type=float)
        if name == "sheet":
            command.add_argument("--columns", type=int, default=4)
            command.add_argument("--cell-width", type=int, default=320)
            command.add_argument("--output", required=True, type=Path)
        if name == "coverage":
            command.add_argument("--session", default="agent")
        if name == "serve":
            command.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        if args.command == "extract":
            result = extract(args.video, args.output, video_stream=args.stream,
                             thumbnail_width=args.thumbnail_width, png_compression=args.png_compression,
                             reserve_bytes=args.reserve_mib * 1024 * 1024,
                             progress=lambda n: print(f"Decoded {n:,} frames", file=sys.stderr, flush=True))
        else:
            dataset = Dataset(args.dataset)
            if args.command == "info":
                result = dataset.manifest
            elif args.command == "verify":
                result = dataset.verify()
            elif args.command == "frames":
                result = dataset.frames(args.start, args.count)
            elif args.command == "frame":
                result = dataset.frame(args.index) if args.index is not None else dataset.at_time(args.time)
            elif args.command == "sheet":
                target = args.output.expanduser().resolve()
                if target.exists() or target.is_relative_to(dataset.root):
                    raise ValueError("Choose a new contact sheet output outside the dataset.")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(dataset.sheet(args.start, args.count, args.columns, args.cell_width))
                result = {"output": str(target), "individual_delivery_counted": False}
            elif args.command == "coverage":
                result = Coverage(dataset).report(args.session)
            elif args.command == "serve":
                from .server import serve
                serve(dataset, args.port)
                return 0
            else:
                from .mcp import stdio
                stdio(dataset)
                return 0
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        print("Stopped. This extraction has not completed.", file=sys.stderr)
        return 130
    except (ValueError, OSError, KeyError, av.error.FFmpegError) as error:
        print(f"frameatlas: {error}", file=sys.stderr)
        return 1
