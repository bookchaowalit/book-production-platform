"""Human review of rendered options, and the feedback loop back into the composer.

``build_review_page`` writes one self-contained ``review.html`` next to a compose or
campaign output: every option in every size side by side, its layout id, idea, and
design score, with pass / fail buttons and reason chips (the critique checklist of the
``graphic-design`` skill). The reviewer's verdicts are kept in the browser and exported
as ``review-feedback.json``.

``import_feedback`` appends those verdicts to ``feedback.jsonl`` in the layout library.
From then on the composer ranks every composition idea by its human track record
(:func:`idea_penalties`): an idea that people keep rejecting is ranked down, so the
design score and the reviewers' eyes are combined instead of the score alone deciding.
``feedback_report`` shows where the score and people disagree.
"""

from __future__ import annotations

import html
import json
import os
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .layouts import layout_dir

REASONS = (
    ("hierarchy", "ลำดับไม่ชัด / ไม่รู้ว่าอ่านอะไรก่อน"),
    ("readability", "อ่านยาก (สี ขนาด ตัวอักษร)"),
    ("crowded", "แน่น / รก"),
    ("empty", "ว่างเกิน / ดูไม่เสร็จ"),
    ("photo", "รูปไม่ดี (ครอป ตัดพื้นหลัง)"),
    ("brand", "ไม่เข้ากับแบรนด์ / อารมณ์"),
    ("logo", "โลโก้ (ตำแหน่ง ขนาด ความชัด)"),
    ("other", "อื่น ๆ (เขียนในหมายเหตุ)"),
)
MIN_SAMPLES = 3
PENALTY = 15.0  # design-score points taken off an idea that people reject every time


def _options(folder: Path) -> list[dict[str, Any]]:
    """Every option of a compose (``compose-summary.json``) or campaign (``campaign-summary.json``) output."""

    rows: list[dict[str, Any]] = []
    compose = folder / "compose-summary.json"
    if compose.is_file():
        summary = json.loads(compose.read_text(encoding="utf-8"))
        for number, option in enumerate(summary.get("options", []), start=1):
            rows.append({**option, "post": "", "prefix": f"option{number}-"})
    campaign = folder / "campaign-summary.json"
    if campaign.is_file():
        summary = json.loads(campaign.read_text(encoding="utf-8"))
        for post in summary.get("posts", []):
            for number, option in enumerate(post.get("options", []), start=1):
                rows.append({**option, "post": post["id"], "prefix": f"{post['id']}-option{number}-"})
    return rows


def build_review_page(folder: str | Path) -> Path:
    """Write ``review.html`` into ``folder`` (a compose or campaign output) and return its path."""

    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    pngs = sorted(root.rglob("*.png"))
    cards: list[str] = []
    for option in _options(root):
        images = [path for path in pngs if path.name.startswith(option["prefix"]) and "-preview" not in path.name]
        if not images:
            continue
        figures = "".join(
            f'<figure><img loading="lazy" src="{html.escape(os.path.relpath(path, root))}" alt="">'
            f"<figcaption>{html.escape(path.stem.rsplit('-', 1)[-1])}</figcaption></figure>" for path in images
        )
        chips = "".join(f'<label><input type="checkbox" value="{key}"> {html.escape(text)}</label>' for key, text in REASONS)
        score = option.get("score")
        meta = " · ".join(str(part) for part in (option.get("post"), option.get("archetype"), f"score {score}" if score else "") if part)
        cards.append(
            f'<article data-layout="{html.escape(str(option["layout_id"]))}" data-archetype="{html.escape(str(option.get("archetype", "")))}"'
            f' data-score="{html.escape(str(score or ""))}" data-post="{html.escape(str(option.get("post", "")))}">'
            f'<header><b>{html.escape(str(option["layout_id"]))}</b><span>{html.escape(meta)}</span></header>'
            f'<div class="sizes">{figures}</div>'
            f'<div class="verdict"><button data-v="pass">ผ่าน</button><button data-v="fail">ไม่ผ่าน</button></div>'
            f'<fieldset>{chips}</fieldset><textarea placeholder="หมายเหตุ (ไม่บังคับ)"></textarea></article>'
        )
    page = root / "review.html"
    page.write_text(_PAGE.replace("{{CARDS}}", "\n".join(cards) or "<p>ไม่พบงานในโฟลเดอร์นี้</p>")
                    .replace("{{TITLE}}", html.escape(root.name)), encoding="utf-8")
    return page


def feedback_path() -> Path | None:
    root = layout_dir()
    return root / "feedback.jsonl" if root is not None else None


def import_feedback(data: Mapping[str, Any], reviewer: str = "") -> int:
    """Append exported verdicts to the library's ``feedback.jsonl``; returns how many were stored."""

    target = feedback_path()
    if target is None:
        raise ValueError("layout_library_not_configured")
    target.parent.mkdir(parents=True, exist_ok=True)
    known = {key for key, _text in REASONS}
    stored = 0
    with target.open("a", encoding="utf-8") as handle:
        for item in data.get("items", []):
            verdict = item.get("verdict")
            if verdict not in ("pass", "fail") or not item.get("layout_id"):
                continue
            row = {
                "at": datetime.now(UTC).isoformat(timespec="seconds"), "reviewer": reviewer[:60],
                "layout_id": str(item["layout_id"])[:40], "archetype": str(item.get("archetype", ""))[:40],
                "score": float(item["score"]) if str(item.get("score", "")).replace(".", "", 1).isdigit() else None,
                "verdict": verdict, "reasons": [r for r in item.get("reasons", []) if r in known],
                "note": str(item.get("note", ""))[:500],
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            stored += 1
    return stored


def _rows() -> list[dict[str, Any]]:
    path = feedback_path()
    if path is None or not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def idea_penalties() -> dict[str, float]:
    """Design-score points to take off each composition idea, from its human reject rate (needs a few verdicts)."""

    seen: dict[str, list[bool]] = defaultdict(list)
    for row in _rows():
        if row.get("archetype"):
            seen[row["archetype"]].append(row.get("verdict") == "fail")
    return {idea: round(PENALTY * sum(fails) / len(fails), 2) for idea, fails in seen.items() if len(fails) >= MIN_SAMPLES}


def feedback_report() -> dict[str, Any]:
    """Verdict counts, reasons, per-idea reject rates, and how well the design score agrees with people."""

    rows = _rows()
    passes = [row["score"] for row in rows if row.get("verdict") == "pass" and row.get("score") is not None]
    fails = [row["score"] for row in rows if row.get("verdict") == "fail" and row.get("score") is not None]
    ideas: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        ideas[row.get("archetype") or "?"][row.get("verdict", "?")] += 1
    return {
        "verdicts": len(rows), "pass": sum(1 for row in rows if row.get("verdict") == "pass"),
        "fail": sum(1 for row in rows if row.get("verdict") == "fail"),
        "reasons": Counter(reason for row in rows for reason in row.get("reasons", [])).most_common(),
        "ideas": {idea: dict(counts) for idea, counts in sorted(ideas.items())},
        "mean_score_pass": round(sum(passes) / len(passes), 1) if passes else None,
        "mean_score_fail": round(sum(fails) / len(fails), 1) if fails else None,
        "high_score_fails": sum(1 for score in fails if score >= 85),
        "penalties": idea_penalties(),
    }


_PAGE = """<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review {{TITLE}}</title>
<style>
:root { --bg:#f4f2ee; --card:#fff; --ink:#1d1d1f; --muted:#6b6b70; --ok:#1f7a4a; --bad:#b3261e; --line:#dddad3 }
* { box-sizing:border-box } body { margin:0; padding:16px; background:var(--bg); color:var(--ink);
  font:14px/1.5 "Sarabun","Kanit",system-ui,sans-serif }
h1 { font-size:20px; margin:0 0 4px } .bar { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:16px }
.bar span { color:var(--muted) } button { font:inherit; padding:6px 14px; border-radius:8px; border:1px solid var(--line);
  background:#fff; cursor:pointer } main { display:grid; gap:16px }
article { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px }
article.pass { border-color:var(--ok); box-shadow:0 0 0 2px var(--ok) } article.fail { border-color:var(--bad); box-shadow:0 0 0 2px var(--bad) }
header { display:flex; gap:12px; flex-wrap:wrap; align-items:baseline; margin-bottom:8px } header span { color:var(--muted) }
.sizes { display:flex; gap:10px; overflow-x:auto; align-items:flex-start } figure { margin:0 }
figure img { height:320px; width:auto; border-radius:6px; border:1px solid var(--line); background:#eee }
figcaption { color:var(--muted); font-size:12px; text-align:center }
.verdict { display:flex; gap:8px; margin:10px 0 6px } .verdict button[data-v=pass].on { background:var(--ok); color:#fff }
.verdict button[data-v=fail].on { background:var(--bad); color:#fff }
fieldset { border:0; padding:0; margin:0; display:flex; flex-wrap:wrap; gap:6px 14px; color:var(--muted) }
textarea { width:100%; margin-top:8px; min-height:44px; font:inherit; border:1px solid var(--line); border-radius:8px; padding:6px }
</style></head><body>
<h1>ตรวจงาน: {{TITLE}}</h1>
<div class="bar"><span id="count"></span><button id="export">บันทึกผลตรวจ (review-feedback.json)</button>
<span>ดูทุกขนาด ทำ checklist ในใจ (อ่าน 3 วินาทีรู้เรื่องไหม ตาไปไหนก่อน หรี่ตาแล้วเห็นจุดเด่นไหม ทุกคำถูกไหม) แล้วกดผ่าน/ไม่ผ่าน</span></div>
<main>
{{CARDS}}
</main>
<script>
const KEY = "review:" + location.pathname;
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { state = {}; }
function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} count(); }
function count() { const n = Object.values(state).filter(s => s.verdict).length;
  document.getElementById("count").textContent = `ตรวจแล้ว ${n} / ${document.querySelectorAll("article").length}`; }
document.querySelectorAll("article").forEach(card => {
  const id = card.dataset.layout + "|" + card.dataset.post;
  const s = state[id] || (state[id] = { reasons: [], note: "" });
  const paint = () => { card.classList.toggle("pass", s.verdict === "pass"); card.classList.toggle("fail", s.verdict === "fail");
    card.querySelectorAll(".verdict button").forEach(b => b.classList.toggle("on", b.dataset.v === s.verdict)); };
  card.querySelectorAll(".verdict button").forEach(b => b.addEventListener("click", () => { s.verdict = b.dataset.v; paint(); save(); }));
  card.querySelectorAll("fieldset input").forEach(i => { i.checked = s.reasons.includes(i.value);
    i.addEventListener("change", () => { s.reasons = [...card.querySelectorAll("fieldset input:checked")].map(x => x.value); save(); }); });
  const t = card.querySelector("textarea"); t.value = s.note || ""; t.addEventListener("input", () => { s.note = t.value; save(); });
  paint();
});
count();
document.getElementById("export").addEventListener("click", () => {
  const items = [...document.querySelectorAll("article")].map(card => {
    const s = state[card.dataset.layout + "|" + card.dataset.post] || {};
    return { layout_id: card.dataset.layout, archetype: card.dataset.archetype, score: card.dataset.score, post: card.dataset.post,
             verdict: s.verdict || null, reasons: s.reasons || [], note: s.note || "" };
  }).filter(item => item.verdict);
  const blob = new Blob([JSON.stringify({ schema_version: "graphic-review.v1", items }, null, 2)], { type: "application/json" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "review-feedback.json"; a.click();
});
</script></body></html>
"""

__all__ = ["REASONS", "build_review_page", "feedback_report", "idea_penalties", "import_feedback"]
