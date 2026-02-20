from __future__ import annotations

import copy
import io
import json
import logging
import shutil
import struct
from pathlib import Path
from typing import IO, Any

LOGGER = logging.getLogger(__name__)


def _round_up(i: int, m: int) -> int:
    return (i + m - 1) & ~(m - 1)


class AsarArchive:
    """Represents a single *.asar file."""

    def __init__(
        self,
        filename: Path,
        asarfile: IO[bytes],
        files: dict[str, Any],
        baseoffset: int,
    ) -> None:
        """Initialise a new AsarArchive instance.

        Args:
            filename:   Path to the *.asar file.
            asarfile:   Open binary file object for the archive.
            files:      Parsed header dictionary.
            baseoffset: Absolute position in the file where file data begins.
        """
        self.filename = Path(filename)
        self.asarfile = asarfile
        self.files = files
        self.baseoffset = baseoffset

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def list_files(self) -> list[str]:
        """Return a sorted list of all file paths contained in the archive.

        Returns:
            Archive-relative POSIX paths (e.g. ``src/index.js``).
        """
        result: list[str] = []
        self._walk_files("", self.files["files"], result)
        return sorted(result)

    def extract(self, destination: Path | str) -> None:
        """Extract the contents of the archive to *destination*.

        Args:
            destination: Path to a directory that must **not** already exist.

        Raises:
            OSError: If *destination* already exists.
        """
        dest = Path(destination)
        if dest.exists():
            raise OSError(20, "Destination exists", str(dest))
        self.__extract_directory(".", self.files["files"], dest)

    def extract_file(self, archive_path: str, destination: Path | str) -> None:
        """Extract a single file from the archive to *destination*.

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
        self.__extract_file_to(info, dest)
        LOGGER.debug("Extracted %s → %s", archive_path, dest)

    def replace_file(
        self,
        archive_path: str,
        source_path: Path | str,
        output: Path | str | None = None,
    ) -> None:
        """Replace a single file inside the archive.

        The archive is rewritten from scratch so that only *archive_path* is
        updated; all other files remain byte-for-byte identical.

        Args:
            archive_path: Archive-relative path of the file to replace.
            source_path:  Disk path of the replacement file.
            output:       Where to write the new archive.  ``None`` (default)
                          overwrites the original archive in-place.

        Raises:
            FileNotFoundError: If *source_path* or *archive_path* do not exist.
        """
        src = Path(source_path)
        if not src.is_file():
            raise FileNotFoundError(f"Source file not found: {src}")

        info = self._find_file(archive_path)
        if info is None:
            raise FileNotFoundError(f"'{archive_path}' not found in archive")

        out = Path(output) if output is not None else self.filename
        new_data = src.read_bytes()

        # Build an updated header with recalculated offsets for every file.
        new_header = copy.deepcopy(self.files)
        self._update_offsets(new_header["files"], archive_path, len(new_data))

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
        new_base_offset = 16 + aligned_size  # 4 × uint32 + aligned header

        buf = io.BytesIO()
        buf.write(
            struct.pack(
                "<4I", data_size, header_size, header_object_size, header_string_size
            )
        )
        buf.write(header_json_padded)

        self._write_file_data(
            buf,
            self.files["files"],
            new_header["files"],
            archive_path,
            new_data,
            new_base_offset,
        )

        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_bytes(buf.getvalue())
        tmp.replace(out)
        LOGGER.debug("Replaced %s in %s", archive_path, out)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _walk_files(prefix: str, files_dict: dict[str, Any], result: list[str]) -> None:
        for name, info in files_dict.items():
            path = f"{prefix}/{name}" if prefix else name
            if "files" in info:
                AsarArchive._walk_files(path, info["files"], result)
            else:
                result.append(path)

    def _find_file(self, archive_path: str) -> dict[str, Any] | None:
        """Return the file-info dict for *archive_path*, or ``None``."""
        parts = archive_path.replace("\\", "/").split("/")
        node: dict[str, Any] = self.files
        for part in parts:
            if "files" not in node:
                return None
            node = node["files"].get(part)
            if node is None:
                return None
        if "files" in node:
            return None  # it's a directory
        return node

    def _update_offsets(
        self, files_dict: dict[str, Any], replaced_path: str, new_size: int
    ) -> None:
        """Rebuild sequential offsets in *files_dict*, updating the size of
        *replaced_path* to *new_size*."""
        self.__recompute_offsets(files_dict, replaced_path, new_size, [0])

    def __recompute_offsets(
        self,
        files_dict: dict[str, Any],
        replaced_path: str,
        new_size: int,
        counter: list[int],
        current_prefix: str = "",
    ) -> None:
        for name, info in files_dict.items():
            path = f"{current_prefix}/{name}" if current_prefix else name
            if "files" in info:
                self.__recompute_offsets(
                    info["files"], replaced_path, new_size, counter, path
                )
            elif "offset" in info:
                if path == replaced_path:
                    info["size"] = new_size
                info["offset"] = str(counter[0])
                counter[0] += info["size"]

    def _write_file_data(
        self,
        buf: io.BytesIO,
        old_files: dict[str, Any],
        new_files: dict[str, Any],
        replaced_path: str,
        new_data: bytes,
        base_offset: int,
        prefix: str = "",
    ) -> None:
        for name, old_info in old_files.items():
            path = f"{prefix}/{name}" if prefix else name
            new_info = new_files[name]
            if "files" in old_info:
                self._write_file_data(
                    buf,
                    old_info["files"],
                    new_info["files"],
                    replaced_path,
                    new_data,
                    base_offset,
                    path,
                )
            elif "offset" in old_info:
                if path == replaced_path:
                    buf.write(new_data)
                else:
                    self.asarfile.seek(self.__absolute_offset(old_info["offset"]))
                    buf.write(self.asarfile.read(int(old_info["size"])))

    def __extract_directory(
        self, path: str, files: dict[str, Any], destination: Path
    ) -> None:
        dest_path = destination / path
        dest_path.mkdir(parents=True, exist_ok=True)

        for name, contents in files.items():
            item_path = f"{path}/{name}"
            if "files" in contents:
                self.__extract_directory(item_path, contents["files"], destination)
            else:
                self.__extract_file(item_path, contents, destination)

    def __extract_file(
        self, path: str, fileinfo: dict[str, Any], destination: Path
    ) -> None:
        if "offset" not in fileinfo:
            self.__copy_extracted(path, destination)
            return

        dest_path = destination / path
        self.__extract_file_to(fileinfo, dest_path)
        LOGGER.debug("Extracted %s to %s", path, dest_path)

    def __extract_file_to(self, fileinfo: dict[str, Any], dest_path: Path) -> None:
        """Write the raw bytes described by *fileinfo* to *dest_path*."""
        self.asarfile.seek(self.__absolute_offset(fileinfo["offset"]))
        data = self.asarfile.read(int(fileinfo["size"]))
        dest_path.write_bytes(data)

    def __copy_extracted(self, path: str, destination: Path) -> None:
        """Copy a file that lives in the sibling ``.unpacked`` directory."""
        unpacked_dir = Path(str(self.filename) + ".unpacked")
        if not unpacked_dir.is_dir():
            LOGGER.warning("Failed to copy extracted file %s, no extracted dir", path)
            return

        source_path = unpacked_dir / path
        if not source_path.exists():
            LOGGER.warning("Failed to copy extracted file %s, does not exist", path)
            return

        dest_path = destination / path
        shutil.copyfile(source_path, dest_path)

    def __absolute_offset(self, offset: int | str) -> int:
        """Convert a header-relative *offset* to an absolute file position."""
        return int(offset) + self.baseoffset

    def __enter__(self) -> AsarArchive:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.asarfile:
            self.asarfile.close()
            self.asarfile = None

    @classmethod
    def open(cls, filename: Path | str) -> AsarArchive:
        """Open a *.asar file and return a new :class:`AsarArchive` instance.

        Args:
            filename: Path to the *.asar file.

        Returns:
            A ready-to-use :class:`AsarArchive`.
        """
        path = Path(filename)
        asarfile = path.open("rb")

        # The asar format uses a subset of Chromium's Pickle serialisation.
        # Layout: [uint32 data_size=4][uint32 header_size][uint32 header_object_size]
        #         [uint32 header_string_size][<header_string_size bytes of JSON>…]
        asarfile.seek(4)  # skip data_size field
        (header_size,) = struct.unpack("<I", asarfile.read(4))
        header_size -= 8  # subtract the two trailing pickle fields

        asarfile.seek(asarfile.tell() + 8)  # skip header_object_size + string_size
        header = asarfile.read(header_size).decode().rstrip("\x00")

        files = json.loads(header)
        return cls(path, asarfile, files, asarfile.tell())
