import json
import struct
from pathlib import Path

import pytest


def test_acceptance_fixture_has_five_goldens_five_replays_and_ten_rejections() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "scene3d" / "scene_cases.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))["cases"]
    kinds = [case["kind"] for case in cases]

    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert kinds.count("accept") == 5
    assert kinds.count("replay") == 5
    assert kinds.count("reject") == 8
    assert kinds.count("tamper_replay") == 2


def test_synthetic_glb_revolution_uses_gltf_y_up_axis() -> None:
    from book_production_platform.scene3d.benchmark import _synthetic_glb

    glb = _synthetic_glb("canister", [0.3, 0.5, 0.7], "axis-check")
    json_length = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20 : 20 + json_length])
    position_bounds = document["accessors"][0]

    assert position_bounds["min"][1] == -1.0
    assert position_bounds["max"][1] == 1.0
    assert position_bounds["min"][2] == -0.7
    assert position_bounds["max"][2] == 0.7


def test_benchmark_fixture_with_wrong_field_types_has_a_stable_error(tmp_path: Path) -> None:
    from book_production_platform.scene3d.benchmark import _load_cases
    from book_production_platform.scene3d.errors import SceneInputError

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    source = Path(__file__).parent / "fixtures" / "scene3d" / "scene_cases.json"
    fixture = json.loads(source.read_text(encoding="utf-8"))
    fixture["cases"][0]["shape"] = []
    (fixtures_dir / "scene_cases.json").write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(SceneInputError) as error:
        _load_cases(fixtures_dir)

    assert error.value.code == "invalid_benchmark_fixtures"
