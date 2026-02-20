import struct
import json
import shutil
import os
import os.path
import io
import logging

LOGGER = logging.getLogger(__name__)


def _round_up(i, m):
    return (i + m - 1) & ~(m - 1)


class AsarArchive:
    """Represents a single *.asar file."""

    def __init__(self, filename, asarfile, files, baseoffset):
        """Initializes a new instance of the :see AsarArchive class.

        Args:
            filename (str):
                The path to the *.asar file to read/write from/to.

            asarfile (File):
                A open *.asar file object.

            files (dict):
                Dictionary of files contained in the archive.
                (The header that was read from the file).

            baseoffset (int):
                Base offset, indicates where in the file the header ends.
        """

        self.filename = filename
        self.asarfile = asarfile
        self.files = files
        self.baseoffset = baseoffset

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def list_files(self):
        """Return a sorted list of all file paths contained in the archive.

        Returns:
            list[str]: Archive-relative POSIX paths (e.g. ``src/index.js``).
        """
        result = []
        self._walk_files("", self.files["files"], result)
        return sorted(result)

    def extract(self, destination):
        """Extracts the contents of the archive to the specified directory.

        Args:
            destination (str):
                Path to an empty directory to extract the files to.
        """

        if os.path.exists(destination):
            raise OSError(20, "Destination exists", destination)

        self.__extract_directory(".", self.files["files"], destination)

    def extract_file(self, archive_path, destination):
        """Extract a single file from the archive to *destination*.

        Args:
            archive_path (str):
                Archive-relative path of the file to extract
                (e.g. ``src/index.js``).
            destination (str):
                Path on disk where the extracted file will be written.
                Parent directories are created automatically.
        """
        info = self._find_file(archive_path)
        if info is None:
            raise FileNotFoundError(f"'{archive_path}' not found in archive")
        os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
        self.__extract_file_to(info, destination)
        LOGGER.debug("Extracted %s → %s", archive_path, destination)

    def replace_file(self, archive_path, source_path, output=None):
        """Replace a single file inside the archive.

        The archive is rewritten from scratch so that only the targeted file
        is updated; all other files remain byte-for-byte identical.

        Args:
            archive_path (str):
                Archive-relative path of the file to replace
                (e.g. ``src/index.js``).
            source_path (str):
                Path on disk of the replacement file.
            output (str | None):
                Where to write the new archive.  Defaults to overwriting
                the original archive (``self.filename``).
        """
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"Source file not found: {source_path}")

        info = self._find_file(archive_path)
        if info is None:
            raise FileNotFoundError(f"'{archive_path}' not found in archive")

        output = output or self.filename

        with open(source_path, "rb") as f:
            new_data = f.read()

        # Build an updated header with recalculated offsets for every file
        import copy

        new_header = copy.deepcopy(self.files)
        self._update_offsets(new_header["files"], archive_path, len(new_data))

        header_json = json.dumps(
            new_header, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        header_string_size = len(header_json)
        data_size = 4
        aligned_size = _round_up(header_string_size, data_size)
        header_size = aligned_size + 8
        header_object_size = aligned_size + data_size
        diff = aligned_size - header_string_size
        header_json_padded = header_json + b"\0" * diff if diff else header_json
        new_base_offset = 16 + aligned_size  # 4 ints × 4 bytes + aligned header

        buf = io.BytesIO()
        buf.write(
            struct.pack(
                "<4I", data_size, header_size, header_object_size, header_string_size
            )
        )
        buf.write(header_json_padded)

        # Write every file's data in the order they appear in the new header
        self._write_file_data(
            buf,
            self.files["files"],
            new_header["files"],
            archive_path,
            new_data,
            new_base_offset,
        )

        tmp = output + ".tmp"
        with open(tmp, "wb") as f:
            buf.seek(0)
            f.write(buf.read())
        os.replace(tmp, output)
        LOGGER.debug("Replaced %s in %s", archive_path, output)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _walk_files(prefix, files_dict, result):
        for name, info in files_dict.items():
            path = f"{prefix}/{name}" if prefix else name
            if "files" in info:
                AsarArchive._walk_files(path, info["files"], result)
            else:
                result.append(path)

    def _find_file(self, archive_path):
        """Return the fileinfo dict for *archive_path* or None."""
        parts = archive_path.replace("\\", "/").split("/")
        node = self.files
        for part in parts:
            if "files" not in node:
                return None
            node = node["files"].get(part)
            if node is None:
                return None
        # node should now be the file info dict (not a directory)
        if "files" in node:
            return None  # it's a directory
        return node

    def _update_offsets(self, files_dict, replaced_path, new_size):
        """Rebuild all offsets in *files_dict* given that *replaced_path*
        changes size to *new_size*.  Returns the accumulated offset after
        visiting all files."""
        # Collect all file entries in iteration order, compute old sizes,
        # then recompute sequential offsets.
        self.__recompute_offsets(files_dict, replaced_path, new_size, [0])

    def __recompute_offsets(
        self, files_dict, replaced_path, new_size, counter, current_prefix=""
    ):
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
        self, buf, old_files, new_files, replaced_path, new_data, base_offset, prefix=""
    ):
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
                    data = self.asarfile.read(int(old_info["size"]))
                    buf.write(data)

    def __extract_directory(self, path, files, destination):
        """Extracts a single directory to the specified directory on disk.

        Args:
            path (str):
                Relative (to the root of the archive) path of the directory
                to extract.

            files (dict):
                A dictionary of files from a *.asar file header.

            destination (str):
                The path to extract the files to.
        """

        # assures the destination directory exists
        destination_path = os.path.join(destination, path)
        if not os.path.exists(destination_path):
            os.makedirs(destination_path)

        for name, contents in files.items():
            item_path = os.path.join(path, name)

            # objects that have a 'files' member are directories,
            # recurse into them
            if "files" in contents:
                self.__extract_directory(item_path, contents["files"], destination)

                continue

            self.__extract_file(item_path, contents, destination)

    def __extract_file(self, path, fileinfo, destination):
        """Extracts the specified file to the specified destination.

        Args:
            path (str):
                Relative (to the root of the archive) path of the
                file to extract.

            fileinfo (dict):
                Dictionary containing the offset and size of the file
                (Extracted from the header).

            destination (str):
                Directory to extract the archive to.
        """

        if "offset" not in fileinfo:
            self.__copy_extracted(path, destination)
            return

        destination_path = os.path.join(destination, path)
        self.__extract_file_to(fileinfo, destination_path)
        LOGGER.debug("Extracted %s to %s", path, destination_path)

    def __extract_file_to(self, fileinfo, dest_path):
        """Write the raw bytes of *fileinfo* to *dest_path*."""
        self.asarfile.seek(self.__absolute_offset(fileinfo["offset"]))
        contents = self.asarfile.read(int(fileinfo["size"]))
        with open(dest_path, "wb") as fp:
            fp.write(contents)

    def __copy_extracted(self, path, destination):
        """Copies a file that was already extracted to the destination directory.

        Args:
            path (str):
                Relative (to the root of the archive) of the file to copy.

            destination (str):
                Directory to extract the archive to.
        """

        unpacked_dir = self.filename + ".unpacked"
        if not os.path.isdir(unpacked_dir):
            LOGGER.warning("Failed to copy extracted file %s, no extracted dir", path)
            return

        source_path = os.path.join(unpacked_dir, path)
        if not os.path.exists(source_path):
            LOGGER.warning("Failed to copy extracted file %s, does not exist", path)
            return

        destination_path = os.path.join(destination, path)
        shutil.copyfile(source_path, destination_path)

    def __absolute_offset(self, offset):
        """Converts the specified relative offset into an absolute offset.

        Offsets specified in the header are relative to the end of the header.

        Args:
            offset (int):
                The relative offset to convert to an absolute offset.

        Returns (int):
            The specified relative offset as an absolute offset.
        """

        return int(offset) + self.baseoffset

    def __enter__(self):
        """When the `with` statements opens."""

        return self

    def __exit__(self, type, value, traceback):
        """When the `with` statement ends."""

        if not self.asarfile:
            return

        self.asarfile.close()
        self.asarfile = None

    @classmethod
    def open(cls, filename):
        """Opens a *.asar file and constructs a new :see AsarArchive instance.

        Args:
            filename (str):
                Path to the *.asar file to open for reading.

        Returns (AsarArchive):
            An insance of of the :AsarArchive class or None if reading failed.
        """

        asarfile = open(filename, "rb")

        # uses google's pickle format, which prefixes each field
        # with its total length, the first field is a 32-bit unsigned
        # integer, thus 4 bytes, we know that, so we skip it
        asarfile.seek(4)

        header_size = struct.unpack("I", asarfile.read(4))
        if len(header_size) <= 0:
            raise IndexError()

        # substract 8 bytes from the header size, again because google's
        # pickle format uses some padding here
        header_size = header_size[0] - 8

        # read the actual header, which is a json string, again skip 8
        # bytes because of pickle padding
        asarfile.seek(asarfile.tell() + 8)
        header = asarfile.read(header_size).decode("utf-8").rstrip("\x00")

        files = json.loads(header)
        return cls(filename, asarfile, files, asarfile.tell())
