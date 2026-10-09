"""Optional hero image shipped inside the landing bundle (Production Hub E7b)."""

import hashlib
import json
from pathlib import Path

import pytest

from web_production.cli import main
from web_production.jobs import run_page_job
from web_production.qa import run_page_qa

PAGE = {
    "page_id": "hero-1", "slug": "hero-page", "title": "เสื้อลายใหม่", "description": "หน้าเปิดตัว",
    "hero_heading": "ลายใหม่มาแล้ว", "hero_body": "พิมพ์สดทุกตัว", "hero_cta": {"label": "สั่งเลย", "href": "#signup"},
    "features": [{"title": "ส่งไว", "body": "ภายใน 3 วัน"}],
}
PNG = b"\x89PNG\r\n\x1a\nhero-bytes"


def _page_with_image(tmp_path: Path, data: bytes = PNG) -> dict:
    (tmp_path / "hero.png").write_bytes(data)
    return {**PAGE, "hero_image": {"filename": "hero.png", "content_hash": hashlib.sha256(PNG).hexdigest(),
                                   "alt": "เสื้อลายใหม่สีขาว"}}


def test_hero_image_is_shipped_and_referenced_locally(tmp_path: Path) -> None:
    result = run_page_job(_page_with_image(tmp_path), tmp_path / "out", "hero", asset_root=tmp_path)
    assert result.status == "accepted", result.error_code
    names = [artifact.filename for artifact in result.artifacts]
    assert len(names) == 3 and names[2].endswith("-hero.png")
    html = (tmp_path / "out" / names[0]).read_text(encoding="utf-8")
    assert f'<img class="hero-image" src="{names[2]}" alt="เสื้อลายใหม่สีขาว">' in html
    assert (tmp_path / "out" / names[2]).read_bytes() == PNG
    replay = run_page_job(_page_with_image(tmp_path), tmp_path / "out", "hero", asset_root=tmp_path)
    assert replay.replayed and len(replay.artifacts) == 3


@pytest.mark.parametrize(("asset_root", "data", "code"), [(None, PNG, "hero_image_asset_root_required"),
                                                          ("root", b"other", "hero_image_hash_mismatch")])
def test_hero_image_must_match_its_hash(tmp_path: Path, asset_root: str | None, data: bytes, code: str) -> None:
    page = _page_with_image(tmp_path, data)
    result = run_page_job(page, tmp_path / "out", f"bad-{code}", asset_root=tmp_path if asset_root else None)
    assert result.status == "rejected"
    assert result.error_code == code


def test_page_without_image_is_unchanged(tmp_path: Path) -> None:
    result = run_page_job(PAGE, tmp_path, "plain")
    assert result.status == "accepted" and len(result.artifacts) == 2


def test_qa_rejects_foreign_src_and_missing_alt() -> None:
    head = (
        '<meta name="viewport" content="x"><title>t</title><meta name="description" content="d">'
        '<meta property="og:type" content="website"><meta property="og:title" content="t">'
        '<meta property="og:description" content="d"><meta property="og:locale" content="en_US">'
        '<meta name="twitter:card" content="summary">'
    )
    base = f'<!doctype html><html><head>{head}</head><body><h1>t</h1><main></main><footer></footer>'
    css = "@media (max-width: 991px) {} @media (max-width: 767px) {} :focus-visible {}"
    assert "external_asset_reference" in run_page_qa(base + '<img src="https://x/y.png" alt="a"></body></html>', css)
    assert "image_alt_missing" in run_page_qa(base + '<img src="a.png" alt=" "></body></html>', css, None,
                                              frozenset({"a.png"}))
    assert run_page_qa(base + '<img src="a.png" alt="ok"></body></html>', css, None, frozenset({"a.png"})) == []


def test_cli_asset_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    request = tmp_path / "page.json"
    request.write_text(json.dumps({"page": _page_with_image(tmp_path)}, ensure_ascii=False), encoding="utf-8")
    assert main(["build", "--input", str(request), "--output", str(tmp_path / "out"), "--idempotency-key", "cli",
                 "--asset-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "accepted"
