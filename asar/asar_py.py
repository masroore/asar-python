from __future__ import annotations

import copy
import io
import json
import logging
import shutil
import struct
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


def _round_up(i: int, m: int) -> int:
    return (i + m - 1) & ~(m - 1)


class Asar:
    """Read, write, and patch Electron *.asar archives."""

    def __init__(
        self,
        path: Path,
        fp: io.IOBase,
        header: dict[str, Any],
        base_offset: int,
    ) -> None:
        self.path = Path(path)
        self.fp = fp
        self.header = header
        self.base_offset = base_offset

    # ------------------------------------------------------------------ #
    #  Class methods                                                       #
    # ------------------------------------------------------------------ #

    @classmethod
    def open(cls, path: Path | str) -> Asar:
        """Open an existing *.asar archive for reading.

        Args:
            path: Filesystem path to the archive.

        Returns:
            A ready-to-use :class:`Asar` instance.
        """
        p = Path(path)
        fp = p.open("rb")
        data_size, header_size, header_object_size, header_string_size = struct.unpack(
            "<4I", fp.read(16)
        )
        header_json = fp.read(header_string_size).decode()
        return cls(
            path=p,
            fp=fp,
            header=json.loads(header_json),
            base_offset=_round_up(16 + header_string_size, 4),
        )

    @classmethod
    def compress(cls, path: Path | str) -> Asar:
        """Pack a directory tree into an in-memory *.asar archive.

        Args:
            path: Root directory to pack.

        Returns:
            An :class:`Asar` instance backed by an in-memory buffer.
        """
        root = Path(path)
        offset = 0
        file_paths: list[Path] = []

        def _dir_to_dict(directory: Path) -> dict[str, Any]:
            nonlocal offset
            result: dict[str, Any] = {"files": {}}
            for entry in sorted(directory.iterdir()):
                if entry.is_symlink():
                    result["files"][entry.name] = {"link": str(entry.resolve())}
                elif entry.is_dir():
                    result["files"][entry.name] = _dir_to_dict(entry)
                else:
                    file_paths.append(entry)
                    size = entry.stat().st_size
                    result["files"][entry.name] = {"size": size, "offset": str(offset)}
                    offset += size
            return result

        header = _dir_to_dict(root)
        header_json = json.dumps(
            header, sort_keys=True, separators=(",", ":")
        ).encode()
        header_string_size = len(header_json)
        data_size = 4
        aligned_size = _round_up(header_string_size, data_size)
        header_size = aligned_size + 8
        header_object_size = aligned_size + data_size
        diff = aligned_size - header_string_size
        header_json_padded = header_json + b"\x00" * diff if diff else header_json

        buf = io.BytesIO()
        buf.write(
            struct.pack(
                "<4I", data_size, header_size, header_object_size, header_string_size
            )
        )
        buf.write(header_json_padded)
        for fp_path in file_paths:
            buf.write(fp_path.read_bytes())

        return cls(
            path=root,
            fp=buf,
            header=header,
            base_offset=_round_up(16 + header_string_size, 4),
        )

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def list_files(self) -> list[str]:
        """Return a sorted list of all file paths in the archive.

        Returns:
            Archive-relative POSIX paths (e.g. ``src/index.js``).
        """
        result: list[str] = []
        self._walk_entries("", self.header["files"], result)
        return sorted(result)

    def extract_file(self, archive_path: str, destination: Path | str) -> None:
        """Extract a single file from the archive.

        Args:
            archive_path: Archive-relative path (e.g. ``src/index.js``).
            destination:  Disk path where the file will be written.
                          Parent directories are created automatically.

        Raises:
            FileNotFoundError: If *archive_path* is not in the archive.
        """
        dest = Path(destination)
        info = self._find_file(archive_path)
        if info is None:
            raise FileNotFoundError(f"'{archive_path}' not found in archive")
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._read_and_write(info, dest)

    def replace_file(
        self,
        archive_path: str,
        source_path: Path | str,
        output: Path | str | None = None,
    ) -> None:
        """Replace a single file inside the archive.

        The archive is rewritten so that only *archive_path* is updated;
        all other file bytes are preserved exactly.

        Args:
            archive_path: Archive-relative path of the file to replace.
            source_path:  Disk path of the replacement file.
            output:       Output archive path.  ``None`` overwrites the original.

        Raises:
            FileNotFoundError: If *source_path* or *archive_path* do not exist.
        """
        src = Path(source_path)
        if not src.is_file():
            raise FileNotFoundError(f"Source file not found: {src}")
        if self._find_file(archive_path) is None:
            raise FileNotFoundError(f"'{archive_path}' not found in archive")

        out = Path(output) if output is not None else self.path
        new_data = src.read_bytes()

        new_header = copy.deepcopy(self.header)
        self._recompute_offsets(new_header["files"], archive_path, len(new_data), [0])

        header_json = json.dumps(
            new_header, sort_keys=True, separators=(",", ":")
        ).encode()
        header_string_size = len(header_json)
        data_size = 4
        aligned_size = _round_up(header_string_size, data_size)
        header_size = aligned_size + 8
        header_object_size = aligned_size + data_size
        diff = aligned_size - header_string_size
        header_json_padded = header_json + b"\x00" * diff if diff else header_json

        buf = io.BytesIO()
        buf.write(
            struct.pack(
                "<4I", data_size, header_size, header_object_size, header_string_size
            )
        )
        buf.write(header_json_padded)
        self._write_entries(buf, self.header["files"], archive_path, new_data)

        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(out)
        LOGGER.debug("Replaced %s in %s", archive_path, out)

    def extract(self, path: Path | str) -> None:
        """Extract the entire archive to *path*.

        Args:
            path: Destination directory (must not already exist).

        Raises:
            FileExistsError: If *path* already exists.
        """
        dest = Path(path)
        if dest.exists():
            raise FileExistsError(f"Destination already exists: {dest}")
        self._extract_directory(".", self.header["files"], dest)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _walk_entries(
        prefix: str, files_dict: dict[str, Any], result: list[str]
    ) -> None:
        for name, info in files_dict.items():
            path = f"{prefix}/{name}" if prefix else name
            if "files" in info:
                Asar._walk_entries(path, info["files"], result)
            else:
                result.append(path)

    def _find_file(self, archive_path: str) -> dict[str, Any] | None:
        """Return the file-info dict for *archive_path*, or ``None``."""
        parts = archive_path.replace("\\", "/").split("/")
        node: dict[str, Any] = self.header
        for part in parts:
            if "files" not in node:
                return None
            node = node["files"].get(part)
            if node is None:
                return None
        if "files" in node:
            return None  # it's a directory
        return node

    def _read_and_write(self, info: dict[str, Any], dest_path: Path) -> None:
        self.fp.seek(self.base_offset + int(info["offset"]))
        data = self.fp.read(int(info["size"]))
        dest_path.write_bytes(data)

    def _recompute_offsets(
        self,
        files_dict: dict[str, Any],
        replaced_path: str,
        new_size: int,
        counter: list[int],
        prefix: str = "",
    ) -> None:
        for name, info in files_dict.items():
            path = f"{prefix}/{name}" if prefix else name
            if "files" in info:
                self._recompute_offsets(
                    info["files"], replaced_path, new_size, counter, path
                )
            elif "offset" in info:
                if path == replaced_path:
                    info["size"] = new_size
                info["offset"] = str(counter[0])
                counter[0] += info["size"]

    def _write_entries(
        self,
        buf: io.BytesIO,
        files_dict: dict[str, Any],
        replaced_path: str,
        new_data: bytes,
        prefix: str = "",
    ) -> None:
        for name, info in files_dict.items():
            path = f"{prefix}/{name}" if prefix else name
            if "files" in info:
                self._write_entries(buf, info["files"], replaced_path, new_data, path)
            elif "offset" in info:
                if path == replaced_path:
                    buf.write(new_data)
                else:
                    self.fp.seek(self.base_offset + int(info["offset"]))
                    buf.write(self.fp.read(int(info["size"])))

    def _copy_unpacked_file(self, source: str, destination: Path) -> None:
        """Copy a file that lives in the sibling ``.unpacked`` directory."""
        unpacked_dir = Path(str(self.path) + ".unpacked")
        if not unpacked_dir.is_dir():
            LOGGER.warning("Failed to copy %s: no .unpacked directory", source)
            return
        src = unpacked_dir / source
        if not src.exists():
            LOGGER.warning("Failed to copy %s: file does not exist", source)
            return
        shutil.copyfile(src, destination / source)

    def _extract_file(
        self, source: str, info: dict[str, Any], destination: Path
    ) -> None:
        if "offset" not in info:
            self._copy_unpacked_file(source, destination)
            return
        dest = destination / source
        self.fp.seek(self.base_offset + int(info["offset"]))
        dest.write_bytes(self.fp.read(int(info["size"])))

    def _extract_link(self, source: str, link: str, destination: Path) -> None:
        dest_filename = (destination / source).resolve()
        link_to = (destination / link).parent / Path(link).name
        try:
            dest_filename.symlink_to(link_to)
        except FileExistsError:
            dest_filename.unlink()
            dest_filename.symlink_to(link_to)

    def _extract_directory(
        self, source: str, files: dict[str, Any], destination: Path
    ) -> None:
        dest = (destination / source).resolve()
        dest.mkdir(parents=True, exist_ok=True)

        for name, info in files.items():
            item_path = f"{source}/{name}"
            if "files" in info:
                self._extract_directory(item_path, info["files"], destination)
            elif "link" in info:
                self._extract_link(item_path, info["link"], destination)
            else:
                self._extract_file(item_path, info, destination)

    def __enter__(self) -> Asar:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.fp.close()


# ------------------------------------------------------------------ #
#  Module-level convenience functions                                  #
# ------------------------------------------------------------------ #


def pack_asar(source: Path | str, dest: Path | str) -> None:
    """Pack *source* directory into a new *.asar archive at *dest*."""
    with Asar.compress(source) as a:
        buf = a.fp
        buf.seek(0)
        Path(dest).write_bytes(buf.read())


def extract_asar(source: Path | str, dest: Path | str) -> None:
    """Extract the *.asar archive at *source* into *dest*."""
    with Asar.open(source) as a:
        a.extract(dest)
