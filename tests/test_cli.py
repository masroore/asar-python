"""CLI smoke tests."""

import tempfile, os, struct, json, subprocess, sys

PY = os.path.join(os.path.dirname(__file__), "../.venv", "bin", "python3")


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
        f.write(b"Hello, world!")
        f.write(b"Hello, sub!!")


def run(*args, expect_fail=False):
    r = subprocess.run(
        [PY, "-m", "asar.cli"] + list(args),
        capture_output=True,
        text=True,
        cwd=os.path.dirname(__file__),
    )
    if not expect_fail:
        assert r.returncode == 0, (
            f"Non-zero exit {r.returncode}\nSTDOUT: {r.stdout}\nSTDERR: {r.stderr}"
        )
    return r


tmpdir = tempfile.mkdtemp()
asar = os.path.join(tmpdir, "test.asar")
make_asar(asar)

print("--- list ---")
r = run("list", asar)
print(r.stdout.strip())
assert "hello.txt" in r.stdout
assert "sub/world.txt" in r.stdout

print("\n--- list -l ---")
r = run("list", "-l", asar)
print(r.stdout.strip())
assert "SIZE" in r.stdout

print("\n--- extract-file ---")
out = os.path.join(tmpdir, "out.txt")
r = run("extract-file", asar, "hello.txt", out)
print(r.stdout.strip())
assert open(out).read() == "Hello, world!"

print("\n--- extract ---")
dest = os.path.join(tmpdir, "extracted")
r = run("extract", asar, dest)
print(r.stdout.strip())
assert os.path.isfile(os.path.join(dest, "hello.txt")), (
    f"extracted dir contents: {os.listdir(dest)}"
)

print("\n--- replace (new output) ---")
repl = os.path.join(tmpdir, "r.txt")
open(repl, "w").write("NEW CONTENT")
patched = os.path.join(tmpdir, "patched.asar")
r = run("replace", asar, "hello.txt", repl, "-o", patched)
print(r.stdout.strip())

check = os.path.join(tmpdir, "check.txt")
run("extract-file", patched, "hello.txt", check)
assert open(check).read() == "NEW CONTENT", f"Got: {open(check).read()!r}"

# Untouched file should still be intact
check2 = os.path.join(tmpdir, "check2.txt")
run("extract-file", patched, "sub/world.txt", check2)
assert open(check2).read() == "Hello, sub!!", f"Got: {open(check2).read()!r}"

print("\n--- replace (in-place) ---")
import shutil

inplace = os.path.join(tmpdir, "inplace.asar")
shutil.copy(asar, inplace)
run("replace", inplace, "hello.txt", repl)
check3 = os.path.join(tmpdir, "check3.txt")
run("extract-file", inplace, "hello.txt", check3)
assert open(check3).read() == "NEW CONTENT"

print("\n--- error: bad archive ---")
r = run("list", __file__, expect_fail=True)
assert r.returncode != 0
print(f"Correctly rejected with exit {r.returncode}: {r.stderr.strip()}")

print("\nALL CLI TESTS PASSED ✓")
