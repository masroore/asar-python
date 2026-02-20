import os
import errno
import io
import copy
import struct
import shutil
import fileinput
import json


def round_up(i, m):
    return (i + m - 1) & ~(m - 1)


class Asar:
    def __init__(self, path, fp, header, base_offset):
        self.path = path
        self.fp = fp
        self.header = header
        self.base_offset = base_offset

    # ------------------------------------------------------------------ #
    #  Class methods                                                       #
    # ------------------------------------------------------------------ #

    @classmethod
    def open(cls, path):
        fp = open(path, "rb")
        data_size, header_size, header_object_size, header_string_size = struct.unpack(
            "<4I", fp.read(16)
        )
        header_json = fp.read(header_string_size).decode("utf-8")
        return cls(
            path=path,
            fp=fp,
            header=json.loads(header_json),
            base_offset=round_up(16 + header_string_size, 4),
        )

    @classmethod
    def compress(cls, path):
        offset = 0
        paths = []

        def _path_to_dict(path):
            nonlocal offset, paths
            result = {"files": {}}
            for f in os.scandir(path):
                if os.path.isdir(f.path):
                    result["files"][f.name] = _path_to_dict(f.path)
                elif f.is_symlink():
                    result["files"][f.name] = {"link": os.path.realpath(f.name)}
                else:
                    paths.append(f.path)
                    size = f.stat().st_size
                    result["files"][f.name] = {"size": size, "offset": str(offset)}
                    offset += size
            return result

        def _paths_to_bytes(paths):
            _bytes = io.BytesIO()
            with fileinput.FileInput(files=paths, mode="rb") as f:
                for i in f:
                    _bytes.write(i)
            return _bytes.getvalue()

        header = _path_to_dict(path)
        header_json = json.dumps(header, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        header_string_size = len(header_json)
        data_size = 4
        aligned_size = round_up(header_string_size, data_size)
        header_size = aligned_size + 8
        header_object_size = aligned_size + data_size
        diff = aligned_size - header_string_size
        header_json = header_json + b"\0" * diff if diff else header_json
        fp = io.BytesIO()
        fp.write(
            struct.pack(
                "<4I", data_size, header_size, header_object_size, header_string_size
            )
        )
        fp.write(header_json)
        fp.write(_paths_to_bytes(paths))

        return cls(
            path=path,
            fp=fp,
            header=header,
            base_offset=round_up(16 + header_string_size, 4),
        )

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def list_files(self):
        """Return a sorted list of all file paths in the archive.

        Returns:
            list[str]: Archive-relative POSIX paths (e.g. ``src/index.js``).
        """
        result = []
        self._walk_entries("", self.header["files"], result)
        return sorted(result)

    def extract_file(self, archive_path, destination):
        """Extract a single file from the archive.

        Args:
            archive_path (str):
                Archive-relative path of the file (e.g. ``src/index.js``).
            destination (str):
                Disk path where the file will be written.
        """
        info = self._find_file(archive_path)
        if info is None:
            raise FileNotFoundError(f"'{archive_path}' not found in archive")
        os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
        self._read_and_write(info, destination)

    def replace_file(self, archive_path, source_path, output=None):
        """Replace a single file inside the archive.

        The archive is rewritten so that only *archive_path* is updated;
        all other file bytes are preserved exactly.

        Args:
            archive_path (str):
                Archive-relative path of the file to replace.
            source_path (str):
                Path on disk of the replacement file.
            output (str | None):
                Output archive path.  Defaults to overwriting the original.
        """
        if not os.path.isfile(source_path):
            raise FileNotFoundError(f"Source file not found: {source_path}")
        if self._find_file(archive_path) is None:
            raise FileNotFoundError(f"'{archive_path}' not found in archive")

        output = output or self.path

        with open(source_path, "rb") as f:
            new_data = f.read()

        new_header = copy.deepcopy(self.header)
        self._recompute_offsets(new_header["files"], archive_path, len(new_data), [0])

        header_json = json.dumps(
            new_header, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        header_string_size = len(header_json)
        data_size = 4
        aligned_size = round_up(header_string_size, data_size)
        header_size = aligned_size + 8
        header_object_size = aligned_size + data_size
        diff = aligned_size - header_string_size
        header_json_padded = header_json + b"\0" * diff if diff else header_json

        buf = io.BytesIO()
        buf.write(
            struct.pack(
                "<4I", data_size, header_size, header_object_size, header_string_size
            )
        )
        buf.write(header_json_padded)
        self._write_entries(buf, self.header["files"], archive_path, new_data)

        tmp = output + ".tmp"
        with open(tmp, "wb") as f:
            buf.seek(0)
            f.write(buf.read())
        os.replace(tmp, output)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _walk_entries(prefix, files_dict, result):
        for name, info in files_dict.items():
            path = f"{prefix}/{name}" if prefix else name
            if "files" in info:
                Asar._walk_entries(path, info["files"], result)
            else:
                result.append(path)

    def _find_file(self, archive_path):
        parts = archive_path.replace("\\", "/").split("/")
        node = self.header
        for part in parts:
            if "files" not in node:
                return None
            node = node["files"].get(part)
            if node is None:
                return None
        if "files" in node:
            return None
        return node

    def _read_and_write(self, info, dest_path):
        self.fp.seek(self.base_offset + int(info["offset"]))
        data = self.fp.read(int(info["size"]))
        with open(dest_path, "wb") as f:
            f.write(data)

    def _recompute_offsets(
        self, files_dict, replaced_path, new_size, counter, prefix=""
    ):
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

    def _write_entries(self, buf, files_dict, replaced_path, new_data, prefix=""):
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

    # ------------------------------------------------------------------ #
    #  Extraction                                                          #
    # ------------------------------------------------------------------ #

    def _copy_unpacked_file(self, source, destination):
        unpacked_dir = self.path + ".unpacked"
        if not os.path.isdir(unpacked_dir):
            print("Couldn't copy file {}, no extracted directory".format(source))
            return

        src = os.path.join(unpacked_dir, source)
        if not os.path.exists(src):
            print("Couldn't copy file {}, doesn't exist".format(src))
            return

        dest = os.path.join(destination, source)
        shutil.copyfile(src, dest)

    def _extract_file(self, source, info, destination):
        if "offset" not in info:
            self._copy_unpacked_file(source, destination)
            return

        self.fp.seek(self.base_offset + int(info["offset"]))
        r = self.fp.read(int(info["size"]))

        dest = os.path.join(destination, source)
        with open(dest, "wb") as f:
            f.write(r)

    def _extract_link(self, source, link, destination):
        dest_filename = os.path.normpath(os.path.join(destination, source))
        link_src_path = os.path.dirname(os.path.join(destination, link))
        link_to = os.path.join(link_src_path, os.path.basename(link))

        try:
            os.symlink(link_to, dest_filename)
        except OSError as e:
            if e.errno == errno.EEXIST:
                os.unlink(dest_filename)
                os.symlink(link_to, dest_filename)
            else:
                raise e

    def _extract_directory(self, source, files, destination):
        dest = os.path.normpath(os.path.join(destination, source))

        if not os.path.exists(dest):
            os.makedirs(dest)

        for name, info in files.items():
            item_path = os.path.join(source, name)

            if "files" in info:
                self._extract_directory(item_path, info["files"], destination)
            elif "link" in info:
                self._extract_link(item_path, info["link"], destination)
            else:
                self._extract_file(item_path, info, destination)

    def extract(self, path):
        if os.path.exists(path):
            raise FileExistsError()
        self._extract_directory(".", self.header["files"], path)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.fp.close()


# ------------------------------------------------------------------ #
#  Module-level convenience functions                                  #
# ------------------------------------------------------------------ #


def pack_asar(source, dest):
    with Asar.compress(source) as a:
        with open(dest, "wb") as fp:
            a.fp.seek(0)
            fp.write(a.fp.read())


def extract_asar(source, dest):
    with Asar.open(source) as a:
        a.extract(dest)
