"""
asar.listing
============

Utilities for collecting and rendering the file listing of an `.asar` archive
in multiple output formats.

Usage::

    from asar import AsarArchive, ArchiveListing

    with AsarArchive.open("app.asar") as a:
        listing = ArchiveListing.from_archive(a)

    print(listing.render("plain"))   # one path per line
    print(listing.render("long"))    # with file sizes
    print(listing.render("json"))    # JSON array
    print(listing.render("xml"))     # XML document
    print(listing.render("yaml"))    # YAML sequence
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .archive import AsarArchive

# All supported output formats.
FORMATS: tuple[str, ...] = ("plain", "long", "json", "xml", "yaml")

# A single file entry produced by :meth:`ArchiveListing.entries`.
Entry = dict[str, Any]  # keys: path (str), size (int), unpacked (bool)


class ArchiveListing:
    """Collected file listing for a single `.asar` archive.

    Instances are normally created via the :meth:`from_archive` class method.
    The raw entry list is available as :attr:`entries` and can be rendered to
    any supported format with :meth:`render`.
    """

    def __init__(self, entries: list[Entry]) -> None:
        """Initialise with a pre-built list of file entries.

        Each entry is a dict with keys:

        * ``path``     – archive-relative POSIX path (e.g. ``src/index.js``)
        * ``size``     – file size in bytes
        * ``unpacked`` – ``True`` if the file lives in the ``.unpacked`` sidecar
        """
        self.entries: list[Entry] = entries

    # ------------------------------------------------------------------ #
    #  Construction                                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def from_archive(cls, archive: AsarArchive) -> ArchiveListing:
        """Build a listing from an already-open :class:`~asar.AsarArchive`.

        Args:
            archive: An open :class:`~asar.AsarArchive` instance.

        Returns:
            A new :class:`ArchiveListing`.
        """
        entries: list[Entry] = []
        cls._collect(archive.files["files"], "", entries)
        return cls(entries)

    @classmethod
    def _collect(
        cls,
        files_dict: dict[str, Any],
        prefix: str,
        result: list[Entry],
    ) -> None:
        """Recursively walk *files_dict* and append one :data:`Entry` per file."""
        for name, info in sorted(files_dict.items()):
            path = f"{prefix}/{name}" if prefix else name
            if "files" in info:
                cls._collect(info["files"], path, result)
            else:
                result.append(
                    {
                        "path": path,
                        "size": info.get("size", 0),
                        "unpacked": "offset" not in info,
                    }
                )

    # ------------------------------------------------------------------ #
    #  Rendering                                                           #
    # ------------------------------------------------------------------ #

    def render(self, fmt: str) -> str:
        """Render the listing in the requested *fmt*.

        Args:
            fmt: One of ``"plain"``, ``"long"``, ``"json"``, ``"xml"``,
                 ``"yaml"``.

        Returns:
            The listing as a string in the requested format.

        Raises:
            ValueError: If *fmt* is not a recognised format name.
        """
        try:
            renderer = _RENDERERS[fmt]
        except KeyError:
            raise ValueError(
                f"Unknown format {fmt!r}. Valid formats: {', '.join(FORMATS)}"
            )
        return renderer(self.entries)

    # Convenience properties -------------------------------------------------

    @property
    def is_empty(self) -> bool:
        """``True`` when the archive contains no files."""
        return not self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __repr__(self) -> str:  # pragma: no cover
        return f"ArchiveListing({len(self.entries)} files)"


# ------------------------------------------------------------------ #
#  Private renderers                                                   #
# ------------------------------------------------------------------ #


def _render_plain(entries: list[Entry]) -> str:
    return "\n".join(e["path"] for e in entries)


def _render_long(entries: list[Entry]) -> str:
    header = f"{'SIZE':>10}  PATH"
    sep = "-" * 50
    rows = [
        f"{e['size']:>10}  {e['path']}" + ("  [unpacked]" if e["unpacked"] else "")
        for e in entries
    ]
    return "\n".join([header, sep, *rows])


def _render_json(entries: list[Entry]) -> str:
    return json.dumps(entries, indent=2)


def _render_xml(entries: list[Entry]) -> str:
    root = ET.Element("archive")
    for e in entries:
        child = ET.SubElement(root, "file")
        child.set("path", e["path"])
        child.set("size", str(e["size"]))
        if e["unpacked"]:
            child.set("unpacked", "true")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _render_yaml(entries: list[Entry]) -> str:
    return yaml.dump(entries, sort_keys=False, allow_unicode=True)


_RENDERERS: dict[str, Any] = {
    "plain": _render_plain,
    "long": _render_long,
    "json": _render_json,
    "xml": _render_xml,
    "yaml": _render_yaml,
}
