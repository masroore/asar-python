"""Quick end-to-end test for AsarArchive and Asar."""

import tempfile, os, struct, json


def make_asar(path):
    files = {
        "files": {
            "hello.txt": {"size": 13, "offset": "0"},
            "sub": {"files": {"world.txt": {"size": 12, "offset": "13"}}},
        }
    }
    hj = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    sz = len(hj)
    aligned = (sz + 3) & ~3
    hj_pad = hj + b"\x00" * (aligned - sz)
    with open(path, "wb") as f:
        f.write(struct.pack("<4I", 4, aligned + 8, aligned + 4, sz))
        f.write(hj_pad)
        f.write(b"Hello, world!")  # 13 bytes
        f.write(b"Hello, sub!!")  # 12 bytes


tmpdir = tempfile.mkdtemp()
asar_path = os.path.join(tmpdir, "test.asar")
make_asar(asar_path)

# ── AsarArchive ────────────────────────────────────────────────────
from asar.archive import AsarArchive

with AsarArchive.open(asar_path) as a:
    files = a.list_files()
print("list_files:", files)
assert files == ["hello.txt", "sub/world.txt"], f"Unexpected: {files}"

out = os.path.join(tmpdir, "hello_out.txt")
with AsarArchive.open(asar_path) as a:
    a.extract_file("hello.txt", out)
content = open(out).read()
print("extract_file:", content)
assert content == "Hello, world!", f"Unexpected: {content!r}"

repl = os.path.join(tmpdir, "r.txt")
with open(repl, "w") as f:
    f.write("REPLACED!")
patched = os.path.join(tmpdir, "patched.asar")
with AsarArchive.open(asar_path) as a:
    a.replace_file("hello.txt", repl, output=patched)

with AsarArchive.open(patched) as a:
    v1 = os.path.join(tmpdir, "v1.txt")
    a.extract_file("hello.txt", v1)
    v2 = os.path.join(tmpdir, "v2.txt")
    a.extract_file("sub/world.txt", v2)
r1 = open(v1).read()
r2 = open(v2).read()
print("replaced hello.txt ->", r1)
print("untouched sub/world.txt ->", r2)
assert r1 == "REPLACED!", f"Unexpected: {r1!r}"
assert r2 == "Hello, sub!!", f"Unexpected: {r2!r}"

print("\nAll AsarArchive tests passed ✓")
