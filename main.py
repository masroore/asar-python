"""
pyasar – standalone entry-point
================================

This module re-exports the CLI from ``asar.cli`` so the package can also be
run directly::

    python main.py list app.asar
    python main.py extract app.asar ./out
    python main.py replace app.asar src/index.js ./new.js

See ``asar/cli.py`` for the full command reference.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Any

# Resolve the package root so this file works when executed directly
# (i.e. ``python main.py …``) without a prior ``pip install``.
import os as _os

_os.chdir(Path(__file__).parent)
sys.path.insert(0, str(Path(__file__).parent))

from asar.archive import AsarArchive, pack_asar  # noqa: E402  (after sys.path tweak)


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #


def _die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


def _print_long(files_dict: dict[str, Any], prefix: str) -> None:
    for name, info in sorted(files_dict.items()):
        path = f"{prefix}/{name}" if prefix else name
        if "files" in info:
            _print_long(info["files"], path)
        else:
            size = info.get("size", "?")
            status = "" if "offset" in info else "  [unpacked]"
            print(f"{str(size):>10}  {path}{status}")


# ------------------------------------------------------------------ #
#  Sub-command handlers                                                #
# ------------------------------------------------------------------ #


def cmd_list(args: Any) -> None:
    archive_path = Path(args.archive)
    with AsarArchive.open(archive_path) as a:
        files = a.list_files()
        files_dict = a.files["files"] if args.long else None

    if not files:
        print("(archive is empty)")
        return

    if args.long:
        print(f"{'SIZE':>10}  PATH")
        print("-" * 50)
        _print_long(files_dict, "")
    else:
        print("\n".join(files))


def cmd_extract(args: Any) -> None:
    archive_path = Path(args.archive)
    dest = Path(args.destination)
    if dest.exists():
        _die(
            f"destination '{dest}' already exists. "
            "Remove it first or choose a different path."
        )
    with AsarArchive.open(archive_path) as a:
        a.extract(dest)
    print(f"Extracted '{archive_path}' → '{dest}'")


def cmd_extract_file(args: Any) -> None:
    archive_path = Path(args.archive)
    dest = Path(args.destination)
    with AsarArchive.open(archive_path) as a:
        a.extract_file(args.file, dest)
    print(f"Extracted '{args.file}' → '{dest}'")


def cmd_replace(args: Any) -> None:
    archive_path = Path(args.archive)
    source_path = Path(args.source)
    output_path = Path(args.output) if args.output else None

    if not source_path.is_file():
        _die(f"source file '{source_path}' does not exist or is not a regular file.")

    with AsarArchive.open(archive_path) as a:
        a.replace_file(args.file, source_path, output=output_path)

    target = output_path or archive_path
    print(f"Replaced '{args.file}' in '{target}'")


def cmd_pack(args: Any) -> None:
    source = Path(args.source)
    dest = Path(args.archive)
    if not source.is_dir():
        _die(f"source '{source}' is not a directory.")
    if dest.exists() and not args.force:
        _die(f"'{dest}' already exists. Use --force to overwrite.")

    pack_asar(source, dest)
    print(f"Packed '{source}' → '{dest}'")


# ------------------------------------------------------------------ #
#  Argument parser                                                     #
# ------------------------------------------------------------------ #


def build_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(
        prog="pyasar",
        description="Utility for working with Electron .asar archives.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(title="commands", dest="command", metavar="<command>")
    sub.required = True

    # list
    p = sub.add_parser("list", aliases=["ls"], help="List archive contents.")
    p.add_argument("archive", metavar="ARCHIVE")
    p.add_argument("-l", "--long", action="store_true", help="Show file sizes.")
    p.set_defaults(func=cmd_list)

    # extract
    p = sub.add_parser("extract", aliases=["x"], help="Extract entire archive.")
    p.add_argument("archive", metavar="ARCHIVE")
    p.add_argument("destination", metavar="DESTINATION")
    p.set_defaults(func=cmd_extract)

    # extract-file
    p = sub.add_parser("extract-file", aliases=["xf"], help="Extract a single file.")
    p.add_argument("archive", metavar="ARCHIVE")
    p.add_argument("file", metavar="FILE")
    p.add_argument("destination", metavar="DESTINATION")
    p.set_defaults(func=cmd_extract_file)

    # replace
    p = sub.add_parser("replace", aliases=["r"], help="Replace a file in the archive.")
    p.add_argument("archive", metavar="ARCHIVE")
    p.add_argument("file", metavar="FILE")
    p.add_argument("source", metavar="SOURCE")
    p.add_argument("-o", "--output", metavar="OUTPUT", default=None)
    p.set_defaults(func=cmd_replace)

    # pack
    p = sub.add_parser("pack", aliases=["p"], help="Pack a directory into an archive.")
    p.add_argument("source", metavar="SOURCE")
    p.add_argument("archive", metavar="ARCHIVE")
    p.add_argument("-f", "--force", action="store_true")
    p.set_defaults(func=cmd_pack)

    return parser


# ------------------------------------------------------------------ #
#  Entry point                                                         #
# ------------------------------------------------------------------ #


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except FileNotFoundError as exc:
        _die(str(exc))
    except FileExistsError as exc:
        _die(f"destination already exists – {exc}")
    except OSError as exc:
        _die(str(exc))
    except (ValueError, KeyError, struct.error, json.JSONDecodeError) as exc:
        _die(f"failed to parse archive – {exc}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
