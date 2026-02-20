"""
pyasar – standalone entry-point
================================

This module provides a CLI for working with Electron .asar archives.
It can also be run directly::

    python main.py list app.asar
    python main.py extract app.asar ./out
    python main.py replace app.asar src/index.js ./new.js
    python main.py patch patch.yaml

See ``asar/cli.py`` for the full command reference.

Patch config format (YAML)
--------------------------
The ``patch`` command reads a YAML config file with the following structure::

    source: path/to/input.asar       # archive to read from
    dest:   path/to/output.asar      # archive to write to (may be the same as source)
    files:
      - archive: src/index.js        # path inside the archive to replace
        source:  ./new-index.js      # replacement file on disk
      - archive: package.json
        source:  ./package.json

* ``source`` and ``dest`` may point to the same file; the operation is always
  atomic (write to a temp file, then rename).
* Paths under ``files[*].source`` are resolved relative to the directory that
  contains the YAML config file, so configs are fully portable.
* All replacement files are validated **before** any writing begins, so the
  archive is never left partially modified on error.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from asar import AsarArchive, pack_asar
from asar.listing import FORMATS, ArchiveListing


# ------------------------------------------------------------------ #
#  Sub-command handlers                                                #
# ------------------------------------------------------------------ #


def cmd_list(args: argparse.Namespace) -> None:
    """List all files contained in the archive."""
    archive_path = Path(args.archive)
    fmt: str = "long" if args.long else args.format

    with AsarArchive.open(archive_path) as a:
        listing = ArchiveListing.from_archive(a)

    if listing.is_empty:
        print("(archive is empty)")
        return

    print(listing.render(fmt))


def cmd_extract(args: argparse.Namespace) -> None:
    """Extract all files from the archive to a directory."""
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


def cmd_extract_file(args: argparse.Namespace) -> None:
    """Extract a single file from the archive."""
    archive_path = Path(args.archive)
    dest = Path(args.destination)

    with AsarArchive.open(archive_path) as a:
        a.extract_file(args.file, dest)

    print(f"Extracted '{args.file}' → '{dest}'")


def cmd_replace(args: argparse.Namespace) -> None:
    """Replace a single file inside the archive."""
    archive_path = Path(args.archive)
    source_path = Path(args.source)
    output_path = Path(args.output) if args.output else None

    if not source_path.is_file():
        _die(f"source file '{source_path}' does not exist or is not a regular file.")

    with AsarArchive.open(archive_path) as a:
        a.replace_file(args.file, source_path, output=output_path)

    target = output_path or archive_path
    print(f"Replaced '{args.file}' in '{target}'")


def cmd_pack(args: argparse.Namespace) -> None:
    """Pack a directory into a new .asar archive."""
    source = Path(args.source)
    dest = Path(args.archive)

    if not source.is_dir():
        _die(f"source '{source}' is not a directory.")

    if dest.exists() and not args.force:
        _die(f"'{dest}' already exists. Use --force to overwrite.")

    pack_asar(source, dest)
    print(f"Packed '{source}' → '{dest}'")


def cmd_patch(args: argparse.Namespace) -> None:
    """Apply a batch of file replacements described by a YAML config file."""
    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        _die(f"config file '{config_path}' not found.")

    raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        _die("config file must be a YAML mapping.")

    # ---- validate required top-level keys --------------------------------
    for key in ("source", "dest", "files"):
        if key not in raw:
            _die(f"config is missing required key '{key}'.")

    config_dir = config_path.parent

    def _resolve(p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (config_dir / path).resolve()

    archive_source = _resolve(raw["source"])
    archive_dest = _resolve(raw["dest"])

    if not archive_source.is_file():
        _die(f"source archive '{archive_source}' not found.")

    replacements = raw["files"]
    if not isinstance(replacements, list) or not replacements:
        _die("'files' must be a non-empty list.")

    # ---- resolve and validate every entry before touching anything --------
    validated: list[tuple[str, Path]] = []
    for i, entry in enumerate(replacements, start=1):
        if not isinstance(entry, dict):
            _die(f"files[{i}]: each entry must be a mapping.")
        for key in ("archive", "source"):
            if key not in entry:
                _die(f"files[{i}]: missing required key '{key}'.")
        src = _resolve(entry["source"])
        if not src.is_file():
            _die(f"files[{i}]: source file '{src}' not found.")
        validated.append((entry["archive"], src))

    # ---- apply all replacements atomically --------------------------------
    # Copy source → temp, apply each replacement in turn, then move to dest.
    print(f"Source  : {archive_source}")
    print(f"Dest    : {archive_dest}")
    print(f"Patches : {len(validated)}\n")

    with tempfile.TemporaryDirectory() as tmp_dir:
        working = Path(tmp_dir) / "working.asar"
        shutil.copy2(archive_source, working)

        for archive_path, src in validated:
            with AsarArchive.open(working) as a:
                a.replace_file(archive_path, src, output=working)
            print(f"  patched  {archive_path}  ←  {src.name}")

        archive_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(working), archive_dest)

    print(f"\nDone — wrote patched archive to '{archive_dest}'")


# ------------------------------------------------------------------ #
#  Argument parser                                                     #
# ------------------------------------------------------------------ #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyasar",
        description="Utility for working with Electron .asar archives.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        metavar="<command>",
    )
    subparsers.required = True

    # -- list -----------------------------------------------------------
    p_list = subparsers.add_parser(
        "list",
        aliases=["ls"],
        help="List the contents of an archive.",
        description="List all files stored in the archive.",
    )
    p_list.add_argument("archive", metavar="ARCHIVE", help="Path to the .asar file.")
    p_list.add_argument(
        "-f",
        "--format",
        choices=FORMATS,
        default="plain",
        metavar="FORMAT",
        help=(
            "Output format: plain (default), long, json, xml, yaml. "
            f"Choices: {', '.join(FORMATS)}."
        ),
    )
    p_list.add_argument(
        "-l",
        "--long",
        action="store_true",
        help="Shorthand for --format long (show file sizes).",
    )
    p_list.set_defaults(func=cmd_list)

    # -- extract --------------------------------------------------------
    p_extract = subparsers.add_parser(
        "extract",
        aliases=["x"],
        help="Extract the entire archive.",
        description="Extract all files from ARCHIVE into DESTINATION.",
    )
    p_extract.add_argument("archive", metavar="ARCHIVE", help="Path to the .asar file.")
    p_extract.add_argument(
        "destination",
        metavar="DESTINATION",
        help="Directory to extract files into (must not exist).",
    )
    p_extract.set_defaults(func=cmd_extract)

    # -- extract-file ---------------------------------------------------
    p_xf = subparsers.add_parser(
        "extract-file",
        aliases=["xf"],
        help="Extract a single file from the archive.",
        description="Extract FILE from ARCHIVE and write it to DESTINATION.",
    )
    p_xf.add_argument("archive", metavar="ARCHIVE", help="Path to the .asar file.")
    p_xf.add_argument(
        "file",
        metavar="FILE",
        help="Archive-relative path of the file to extract (e.g. src/index.js).",
    )
    p_xf.add_argument(
        "destination",
        metavar="DESTINATION",
        help="Path on disk to write the extracted file.",
    )
    p_xf.set_defaults(func=cmd_extract_file)

    # -- replace --------------------------------------------------------
    p_rep = subparsers.add_parser(
        "replace",
        aliases=["r"],
        help="Replace a file inside the archive.",
        description=(
            "Replace FILE inside ARCHIVE with the contents of SOURCE.\n"
            "By default the original archive is overwritten in-place.\n"
            "Use --output to write the patched archive to a new file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_rep.add_argument("archive", metavar="ARCHIVE", help="Path to the .asar file.")
    p_rep.add_argument(
        "file",
        metavar="FILE",
        help="Archive-relative path of the file to replace (e.g. src/index.js).",
    )
    p_rep.add_argument(
        "source",
        metavar="SOURCE",
        help="Path on disk of the replacement file.",
    )
    p_rep.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT",
        default=None,
        help="Write the patched archive to OUTPUT instead of overwriting ARCHIVE.",
    )
    p_rep.set_defaults(func=cmd_replace)

    # -- pack -----------------------------------------------------------
    p_pack = subparsers.add_parser(
        "pack",
        aliases=["p"],
        help="Pack a directory into an .asar archive.",
        description="Recursively pack SOURCE directory into ARCHIVE.",
    )
    p_pack.add_argument("source", metavar="SOURCE", help="Directory to pack.")
    p_pack.add_argument("archive", metavar="ARCHIVE", help="Output .asar file path.")
    p_pack.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite ARCHIVE if it already exists.",
    )
    p_pack.set_defaults(func=cmd_pack)

    # -- patch ----------------------------------------------------------
    p_patch = subparsers.add_parser(
        "patch",
        help="Apply a batch of file replacements described by a YAML config file.",
        description="Replace files in an archive according to a YAML config.",
    )
    p_patch.add_argument(
        "config",
        metavar="CONFIG",
        help="Path to the YAML config file.",
    )
    p_patch.set_defaults(func=cmd_patch)

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
