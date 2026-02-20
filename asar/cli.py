"""
pyasar CLI
==========

Usage examples
--------------
List archive contents::

    pyasar list app.asar

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

import argparse
import sys
import os
import struct
import json

from .archive import AsarArchive


# ------------------------------------------------------------------ #
#  Sub-command handlers                                                #
# ------------------------------------------------------------------ #


def cmd_list(args):
    """List all files contained in the archive."""
    with AsarArchive.open(args.archive) as a:
        files = a.list_files()

    if not files:
        print("(archive is empty)")
        return

    if args.long:
        # Show size information alongside the path
        with AsarArchive.open(args.archive) as a:
            print(f"{'SIZE':>10}  PATH")
            print("-" * 50)
            _print_long(a.files["files"], "")
    else:
        for f in files:
            print(f)


def _print_long(files_dict, prefix):
    """Recursively print path + size for every file in *files_dict*."""
    for name, info in sorted(files_dict.items()):
        path = f"{prefix}/{name}" if prefix else name
        if "files" in info:
            _print_long(info["files"], path)
        else:
            size = info.get("size", "?")
            status = ""
            if "offset" not in info:
                status = "  [unpacked]"
            print(f"{str(size):>10}  {path}{status}")


def cmd_extract(args):
    """Extract all files from the archive to a directory."""
    dest = args.destination
    if os.path.exists(dest):
        print(
            f"Error: destination '{dest}' already exists. "
            "Remove it first or choose a different path.",
            file=sys.stderr,
        )
        sys.exit(1)

    with AsarArchive.open(args.archive) as a:
        a.extract(dest)

    print(f"Extracted '{args.archive}' → '{dest}'")


def cmd_extract_file(args):
    """Extract a single file from the archive."""
    with AsarArchive.open(args.archive) as a:
        a.extract_file(args.file, args.destination)

    print(f"Extracted '{args.file}' → '{args.destination}'")


def cmd_replace(args):
    """Replace a single file inside the archive."""
    output = args.output  # may be None → overwrites in-place
    with AsarArchive.open(args.archive) as a:
        a.replace_file(args.file, args.source, output=output)

    target = output or args.archive
    print(f"Replaced '{args.file}' in '{target}'")


def cmd_pack(args):
    """Pack a directory into a new .asar archive."""

    source = args.source
    dest = args.archive

    if not os.path.isdir(source):
        print(f"Error: source '{source}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    if os.path.exists(dest) and not args.force:
        print(
            f"Error: '{dest}' already exists. Use --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Delegate to the Asar.compress helper in asar_py
    from .asar_py import pack_asar

    pack_asar(source, dest)
    print(f"Packed '{source}' → '{dest}'")


# ------------------------------------------------------------------ #
#  Argument parser                                                     #
# ------------------------------------------------------------------ #


def build_parser():
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
        "source", metavar="SOURCE", help="Path on disk of the replacement file."
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


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except FileExistsError as e:
        print(f"Error: destination already exists – {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError, struct.error, json.JSONDecodeError) as e:
        print(f"Error: failed to parse archive – {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
