"""
pyasar CLI
==========

Usage examples
--------------
List archive contents::

    pyasar list app.asar

List with sizes::

    pyasar list -l app.asar

Extract entire archive::

    pyasar extract app.asar ./output-dir

Extract a single file::

    pyasar extract-file app.asar src/index.js ./index.js

Replace a file inside the archive::

    pyasar replace app.asar src/index.js ./new-index.js

Replace a file and write to a new archive (non-destructive)::

    pyasar replace app.asar src/index.js ./new-index.js --output patched.asar

Pack a directory into an asar archive::

    pyasar pack ./my-app app.asar
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any

from .archive import AsarArchive


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #


def _die(message: str, code: int = 1) -> None:
    """Print *message* to stderr and exit with *code*."""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


def _print_long(files_dict: dict[str, Any], prefix: str) -> None:
    """Recursively print path + size for every file in *files_dict*."""
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


def cmd_list(args: argparse.Namespace) -> None:
    """List all files contained in the archive."""
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

    from .asar_py import pack_asar  # local import to keep startup fast

    pack_asar(source, dest)
    print(f"Packed '{source}' → '{dest}'")


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
        "-l",
        "--long",
        action="store_true",
        help="Show file sizes.",
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
