from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from production_hub.cli import main
from production_hub.contracts import ContractError
from production_hub.hub import ArtifactInput, Hub, Producer
from production_hub.store import Store, StoreError

EXTERNAL = Producer("external", "n/a", "intake-1")
VIZ = Producer("data-viz-v1a", "1.0.0", "job-7", "data-viz.v1")


def _file(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def test_store_put_is_content_addressed_and_deduplicated(tmp_path: Path) -> None:
    store = Store(tmp_path / "home")
    first = store.put(_file(tmp_path, "a.csv", b"x,y\n1,2\n"))
    second = store.put(_file(tmp_path, "copy.csv", b"x,y\n1,2\n"))
    assert first.artifact_id == second.artifact_id == _sha(b"x,y\n1,2\n")
    assert first.created and not second.created
    assert store.verify(first.artifact_id)
    assert not list((tmp_path / "home" / "store" / "tmp").iterdir())


def test_store_rejects_bad_ids_and_missing_sources(tmp_path: Path) -> None:
    store = Store(tmp_path / "home")
    with pytest.raises(StoreError):
        store.path_for("sha256:../../etc")
    with pytest.raises(StoreError):
        store.put(tmp_path / "missing.csv")


def test_register_indexes_record_and_lineage(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    with hub.transaction() as conn:
        source = hub.register(conn, ArtifactInput(_file(tmp_path, "in.csv", b"a\n1\n"), "dataset.table", EXTERNAL))
        chart = hub.register(
            conn,
            ArtifactInput(
                _file(tmp_path, "chart.svg", b"<svg/>"),
                "chart.svg",
                VIZ,
                lineage=[source.artifact_id],
                project_id="client-a",
                qa_manifest_ref="jobs/job-7/report.json",
            ),
        )
    with hub.db.connect() as conn:
        record = hub.db.get(conn, chart.artifact_id)
        assert record is not None
        assert record["media_type"] == "image/svg+xml"
        assert record["lineage"] == [source.artifact_id]
        assert record["producer"]["recipe_version"] == "data-viz.v1"
        assert hub.db.outputs(conn, source.artifact_id) == [chart.artifact_id]
        tree = hub.lineage_tree(conn, chart.artifact_id)
    assert tree["inputs"][0]["artifact_id"] == source.artifact_id
    assert tree["inputs"][0]["inputs"] == []


def test_reregistering_same_content_adds_no_duplicate(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    item = ArtifactInput(_file(tmp_path, "in.csv", b"a\n1\n"), "dataset.table", EXTERNAL)
    with hub.transaction() as conn:
        assert hub.register(conn, item).created
        assert not hub.register(conn, item).created
    with hub.db.connect() as conn:
        assert len(hub.db.list(conn)) == 1


def test_unregistered_lineage_is_rejected_and_object_removed(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    content = b"<svg>orphan</svg>"
    with pytest.raises(KeyError), hub.transaction() as conn:
        hub.register(
            conn,
            ArtifactInput(_file(tmp_path, "c.svg", content), "chart.svg", VIZ, lineage=["sha256:" + "0" * 64]),
        )
    assert not hub.store.path_for(_sha(content)).exists()


def test_contract_violation_rolls_back_store(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    content = b"not really a pdf"
    with pytest.raises(ContractError), hub.transaction() as conn:
        hub.register(
            conn, ArtifactInput(_file(tmp_path, "x.pdf", content), "document.pdf", VIZ, media_type="text/plain")
        )
    assert not hub.store.path_for(_sha(content)).exists()
    with hub.db.connect() as conn:
        assert hub.db.list(conn) == []


def test_transaction_rolls_back_every_record_on_failure(tmp_path: Path) -> None:
    hub = Hub(tmp_path / "home")
    with pytest.raises(ContractError), hub.transaction() as conn:
        hub.register(conn, ArtifactInput(_file(tmp_path, "ok.csv", b"a\n"), "dataset.table", EXTERNAL))
        hub.register(conn, ArtifactInput(_file(tmp_path, "bad.bin", b"z"), "no.such-kind", EXTERNAL))
    with hub.db.connect() as conn:
        assert hub.db.list(conn) == []


def test_cli_import_show_lineage_list_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = ["--home", str(tmp_path / "home")]
    source = _file(tmp_path, "in.csv", b"a\n1\n")
    assert main([*home, "import-file", str(source), "--kind", "dataset.table", "--tool", "external", "--job", "i1"]) == 0
    source_id = json.loads(capsys.readouterr().out)["artifact_id"]
    chart = _file(tmp_path, "chart.svg", b"<svg/>")
    assert main([
        *home, "import-file", str(chart), "--kind", "chart.svg", "--tool", "data-viz-v1a",
        "--job", "j1", "--input", source_id, "--project", "client-a",
    ]) == 0
    chart_id = json.loads(capsys.readouterr().out)["artifact_id"]

    assert main([*home, "show", source_id]) == 0
    assert json.loads(capsys.readouterr().out)["used_by"] == [chart_id]
    assert main([*home, "lineage", chart_id]) == 0
    assert json.loads(capsys.readouterr().out)["inputs"][0]["artifact_id"] == source_id
    assert main([*home, "list", "--project", "client-a"]) == 0
    assert "1 artifact(s)" in capsys.readouterr().out
    assert main([*home, "verify"]) == 0
    assert "2 stored artifact(s)" in capsys.readouterr().out


def test_cli_error_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = ["--home", str(tmp_path / "home")]
    assert main([*home, "show", "sha256:" + "1" * 64]) == 3
    bad = _file(tmp_path, "x.txt", b"x")
    assert main([*home, "import-file", str(bad), "--kind", "nope", "--tool", "external", "--job", "j"]) == 4
    assert main([*home, "import-file", str(tmp_path / "gone"), "--kind", "dataset.table",
                 "--tool", "external", "--job", "j"]) == 2
    capsys.readouterr()


def test_verify_detects_corruption(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hub = Hub(tmp_path / "home")
    with hub.transaction() as conn:
        item = hub.register(conn, ArtifactInput(_file(tmp_path, "in.csv", b"a\n"), "dataset.table", EXTERNAL))
    hub.store.path_for(item.artifact_id).write_bytes(b"tampered")
    assert main(["--home", str(tmp_path / "home"), "verify"]) == 5
    assert "corrupted" in capsys.readouterr().out


def test_relative_home_is_made_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    hub = Hub(Path("relative-home"))
    assert hub.home.is_absolute()
    assert hub.home == (tmp_path / "relative-home").resolve()
