"""End-to-end smoke test for the patch command."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from asar import AsarArchive, pack_asar
from ..main import build_parser, cmd_patch

with tempfile.TemporaryDirectory() as d:
    root = Path(d)

    # --- build a tiny source archive ---
    app = root / "app"
    app.mkdir()
    (app / "index.js").write_text("console.log('original')")
    (app / "package.json").write_text('{"version":"1.0.0"}')
    (app / "sub").mkdir()
    (app / "sub" / "helper.js").write_text("// helper")

    archive = root / "app.asar"
    pack_asar(app, archive)

    # --- replacement files ---
    (root / "new-index.js").write_text("console.log('patched')")
    (root / "new-package.json").write_text('{"version":"2.0.0"}')

    # --- patch config (paths are absolute so CWD doesn't matter) ---
    config = root / "patch.yaml"
    config.write_text(
        "source: " + str(archive) + "\n"
        "dest:   " + str(root / "app-patched.asar") + "\n"
        "files:\n"
        "  - archive: index.js\n"
        "    source:  " + str(root / "new-index.js") + "\n"
        "  - archive: package.json\n"
        "    source:  " + str(root / "new-package.json") + "\n"
    )

    # --- run cmd_patch ---
    args = build_parser().parse_args(["patch", str(config)])
    cmd_patch(args)

    # --- verify ---
    patched = root / "app-patched.asar"
    assert patched.is_file(), "patched archive was not created"

    with tempfile.TemporaryDirectory() as out:
        out_path = Path(out) / "extracted"
        with AsarArchive.open(patched) as a:
            a.extract(out_path)

        idx = (out_path / "." / "index.js").read_text()
        pkg = (out_path / "." / "package.json").read_text()
        hlp = (out_path / "." / "sub" / "helper.js").read_text()

        assert "patched" in idx, f"index.js not patched: {idx!r}"
        assert "2.0.0" in pkg, f"package.json not patched: {pkg!r}"
        assert "helper" in hlp, f"helper.js unexpectedly modified: {hlp!r}"

    print("All assertions passed ✓")
