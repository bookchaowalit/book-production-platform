from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "graphics_fork_parity.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("graphics_fork_parity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parity = _load()


def test_import_paths_and_formatting_are_normalised() -> None:
    ours = "from pathlib import Path\n\nfrom book_production_platform.graphics_ir import (\n    A,\n    B,\n)\n\nX = 1\n"
    theirs = "from graphics_ir import A, B\nfrom pathlib import Path\nX = 1\n"

    assert parity.normalise(ours) == parity.normalise(theirs)


def test_compare_classifies_modules(tmp_path: Path) -> None:
    ours, theirs = tmp_path / "ours", tmp_path / "theirs"
    ours.mkdir()
    theirs.mkdir()
    (ours / "same.py").write_text("X = 1\n", encoding="utf-8")
    (theirs / "same.py").write_text("X = 1\n", encoding="utf-8")
    (ours / "changed.py").write_text("X = 2\n", encoding="utf-8")
    (theirs / "changed.py").write_text("X = 1\n", encoding="utf-8")
    (ours / "export.py").write_text("", encoding="utf-8")
    (theirs / "legacy.py").write_text("", encoding="utf-8")

    report = parity.compare(ours, theirs)

    assert report == {
        "identical": ["same.py"],
        "diverged": ["changed.py"],
        "only_in_platform": ["export.py"],
        "only_in_fork": ["legacy.py"],
    }


def test_missing_fork_checkout_exits_2(tmp_path: Path) -> None:
    assert parity.main(["--fork", str(tmp_path / "absent")]) == 2
