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
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

from asar import AsarArchive, pack_asar

# Valid output formats for the list sub-command.
_LIST_FORMATS = ("plain", "long", "json", "xml", "yaml")


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #


def _die(message: str, code: int = 1) -> None:
    """Print *message* to stderr and exit with *code*."""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


def _collect_entries(
    files_dict: dict[str, Any], prefix: str, result: list[dict[str, Any]]
) -> None:
    """Recursively collect file metadata into *result* as flat dicts."""
    for name, info in sorted(files_dict.items()):
        path = f"{prefix}/{name}" if prefix else name
        if "files" in info:
            _collect_entries(info["files"], path, result)
        else:
            result.append(
                {
                    "path": path,
                    "size": info.get("size", 0),
                    "unpacked": "offset" not in info,
                }
            )


# -- format renderers ---------------------------------------------------


def _render_plain(entries: list[dict[str, Any]]) -> str:
    return "\n".join(e["path"] for e in entries)


def _render_long(entries: list[dict[str, Any]]) -> str:
    header = f"{'SIZE':>10}  PATH"
    sep = "-" * 50
    rows = [
        f"{e['size']:>10}  {e['path']}" + ("  [unpacked]" if e["unpacked"] else "")
        for e in entries
    ]
    return "\n".join([header, sep, *rows])


def _render_json(entries: list[dict[str, Any]]) -> str:
    return json.dumps(entries, indent=2)


def _render_xml(entries: list[dict[str, Any]]) -> str:
    root = ET.Element("archive")
    for e in entries:
        child = ET.SubElement(root, "file")
        child.set("path", e["path"])
        child.set("size", str(e["size"]))
        if e["unpacked"]:
            child.set("unpacked", "true")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _render_yaml(entries: list[dict[str, Any]]) -> str:
    return yaml.dump(entries, sort_keys=False, allow_unicode=True)


_RENDERERS = {
    "plain": _render_plain,
    "long": _render_long,
    "json": _render_json,
    "xml": _render_xml,
    "yaml": _render_yaml,
}


# ------------------------------------------------------------------ #
#  Sub-command handlers                                                #
# ------------------------------------------------------------------ #


def cmd_list(args: argparse.Namespace) -> None:
    """List all files contained in the archive."""
    archive_path = Path(args.archive)

    # --long is a convenience alias for --format long
    fmt: str = "long" if args.long else args.format

    with AsarArchive.open(archive_path) as a:
        entries: list[dict[str, Any]] = []
        _collect_entries(a.files["files"], "", entries)

    if not entries:
        print("(archive is empty)")
        return

    print(_RENDERERS[fmt](entries))


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
        choices=_LIST_FORMATS,
        default="plain",
        metavar="FORMAT",
        help=(
            "Output format: plain (default), long, json, xml, yaml. "
            f"Choices: {', '.join(_LIST_FORMATS)}."
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
