# asar-python

A pure-Python library and command-line tool for reading, writing, and patching
[Electron `.asar` archives](https://github.com/electron/asar).

- **List** archive contents in plain, long, JSON, XML, or YAML format
- **Extract** an entire archive or a single file to disk
- **Replace** one file inside an archive without touching any other bytes
- **Pack** a directory tree into a new `.asar` archive
- Fully typed, `pathlib`-native, requires **Python ≥ 3.13**

---

## Table of Contents

- [Installation](#installation)
- [Command-line usage](#command-line-usage)
  - [list](#list)
  - [extract](#extract)
  - [extract-file](#extract-file)
  - [replace](#replace)
  - [pack](#pack)
- [Python API](#python-api)
  - [AsarArchive](#asararchive)
  - [Convenience functions](#convenience-functions)
- [Project structure](#project-structure)
- [Requirements](#requirements)
- [License](#license)

---

## Installation

```bash
# From source with pip
pip install .

# Or with uv
uv pip install .
```

After installation the `pyasar` command is available on your `PATH`.

---

## Command-line usage

```
pyasar <command> [options]
```

### list

List the files stored in an archive.

```bash
# Plain listing (one path per line)
pyasar list app.asar

# With file sizes
pyasar list --long app.asar
pyasar list -l app.asar

# Structured output formats
pyasar list --format json app.asar
pyasar list --format xml  app.asar
pyasar list --format yaml app.asar
```

**Options**

| Flag | Description |
|------|-------------|
| `-f FORMAT`, `--format FORMAT` | Output format: `plain` *(default)*, `long`, `json`, `xml`, `yaml` |
| `-l`, `--long` | Shorthand for `--format long` — shows file sizes |

**Example outputs**

```
# plain
src/index.js
src/main.js
package.json
```

```
# long
      SIZE  PATH
--------------------------------------------------
      1024  package.json
      8192  src/index.js
      4096  src/main.js
```

```jsonc
// json
[
  { "path": "package.json", "size": 1024, "unpacked": false },
  { "path": "src/index.js", "size": 8192, "unpacked": false }
]
```

```xml
<!-- xml -->
<archive>
  <file path="package.json" size="1024" />
  <file path="src/index.js" size="8192" />
</archive>
```

```yaml
# yaml
- path: package.json
  size: 1024
  unpacked: false
- path: src/index.js
  size: 8192
  unpacked: false
```

---

### extract

Extract the entire archive into a directory.  The destination must not already
exist.

```bash
pyasar extract app.asar ./output-dir
pyasar x app.asar ./output-dir          # short alias
```

---

### extract-file

Extract a single file from an archive to a specific path on disk.  Parent
directories are created automatically.

```bash
pyasar extract-file app.asar src/index.js ./index.js
pyasar xf app.asar src/index.js ./index.js   # short alias
```

---

### replace

Replace one file inside an archive.  All other file bytes are left
byte-for-byte identical.  By default the original archive is overwritten
in-place; use `--output` for a non-destructive patch.

```bash
# In-place replacement
pyasar replace app.asar src/index.js ./patched-index.js

# Write patched archive to a new file (original untouched)
pyasar replace app.asar src/index.js ./patched-index.js --output patched.asar

# Short aliases
pyasar r app.asar src/index.js ./patched-index.js -o patched.asar
```

**Options**

| Flag | Description |
|------|-------------|
| `-o OUTPUT`, `--output OUTPUT` | Write the patched archive to `OUTPUT` instead of overwriting the original |

---

### pack

Pack a directory tree into a new `.asar` archive.

```bash
pyasar pack ./my-app app.asar
pyasar p ./my-app app.asar        # short alias

# Overwrite an existing archive
pyasar pack ./my-app app.asar --force
pyasar pack ./my-app app.asar -f
```

**Options**

| Flag | Description |
|------|-------------|
| `-f`, `--force` | Overwrite `ARCHIVE` if it already exists |

---

## Python API

### AsarArchive

The main class in `asar.archive`.  All path arguments accept both `str` and
`pathlib.Path`.

```python
from asar import AsarArchive

# Open an existing archive
with AsarArchive.open("app.asar") as archive:

    # List all file paths (sorted)
    paths: list[str] = archive.list_files()

    # Extract everything
    archive.extract("./output-dir")          # destination must not exist

    # Extract a single file
    archive.extract_file("src/index.js", "./index.js")

    # Replace a file in-place
    archive.replace_file("src/index.js", "./patched-index.js")

    # Replace a file and write to a new archive
    archive.replace_file(
        "src/index.js",
        "./patched-index.js",
        output="patched.asar",
    )
```

**Pack a directory into an archive**

```python
with AsarArchive.compress("./my-app") as archive:
    # archive.asarfile is an in-memory BytesIO buffer
    archive.asarfile.seek(0)
    Path("app.asar").write_bytes(archive.asarfile.read())
```

#### Constructor

```python
AsarArchive(filename, asarfile, files, baseoffset)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `filename` | `Path` | Path to the `.asar` file |
| `asarfile` | `IO[bytes]` | Open binary file object (or `BytesIO` for in-memory) |
| `files` | `dict` | Parsed header dictionary |
| `baseoffset` | `int` | Absolute byte position where file data begins |

#### Class methods

| Method | Description |
|--------|-------------|
| `AsarArchive.open(filename)` | Open an existing `.asar` file for reading |
| `AsarArchive.compress(path)` | Pack a directory into an in-memory archive |

#### Instance methods

| Method | Description |
|--------|-------------|
| `list_files()` | Return a sorted list of all archive-relative file paths |
| `extract(destination)` | Extract the entire archive to `destination` (must not exist) |
| `extract_file(archive_path, destination)` | Extract a single file to disk |
| `replace_file(archive_path, source_path, output=None)` | Replace one file; rewrites archive with updated offsets |

---

### Convenience functions

```python
from asar import pack_asar, extract_asar

# Pack a directory into an archive
pack_asar("./my-app", "app.asar")

# Extract an archive into a directory
extract_asar("app.asar", "./output-dir")
```

---

## Project structure

```
pyasar/
├── asar/
│   ├── __init__.py      # Public exports: AsarArchive, pack_asar, extract_asar
│   ├── archive.py       # AsarArchive class + pack_asar / extract_asar
│   └── cli.py           # argparse CLI wired to asar.cli:main
├── main.py              # Standalone entry-point (python main.py …)
├── pyproject.toml
└── uv.lock
```

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | ≥ 3.13 |
| [PyYAML](https://pyyaml.org/) | ≥ 6.0.3 |

All other dependencies (`json`, `struct`, `xml.etree.ElementTree`, `pathlib`,
`shutil`, `io`, `copy`, `logging`) are part of the Python standard library.

---

## License

This project is released under the MIT License — see the accompanying [LICENSE](LICENSE)
file for the full text.
