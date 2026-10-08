---
title: Book Production Platform — Test Strategy และ UAT
doc_id: BPP-DOC-07
doc_type: test-uat
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: QA (Book Dev)
related: 03-nfr-security.md, 05-srs.md, 08-rtm.md
---

# Book Production Platform — Test Strategy และ UAT

## 1. ระดับการทดสอบ

| ระดับ | เครื่องมือ | ขอบเขต | หมายเหตุ |
|---|---|---|---|
| Unit / integration | pytest | 107 ไฟล์ `test_*.py`: graphics 29, spreadsheet 13, BIM 12, CAD 10, game 8, data 7, audio 3, pipeline 1, IR packages และ scene3d/doctor/CLI ที่ระดับบน | test ที่ต้องใช้ Blender/FreeCAD/Unity/browser skip พร้อมเหตุผลเมื่อไม่มี runtime |
| Visual regression | `tests/graphics/test_golden.py` | ทุกธีม + compose 3 งาน | ต้องตั้ง `GRAPHICS_PRODUCTION_FONT_DIRS` และ resvg |
| Static | ruff, mypy `--strict` (pin win32) | โมดูลที่ระบุใน `scripts/check.py` | ใน CI |
| Acceptance benchmark | `scene benchmark` (20 กรณี), graphics benchmark `batch_30.json` | สายละ fixture สังเคราะห์ | รันเอง |
| AI eval (shadow) | `brief-shadow`, `edit-shadow`, `compose-shadow`, `campaign-brief-shadow` | held-out split, adversarial, canary | ต้องมี provider config |
| UAT | เจ้าของตรวจผ่าน `review.html` และส่งงานจริง | ตาม TC-U* ด้านล่าง | ยังไม่มีบันทึกงานลูกค้า **[ต้องยืนยัน]** |

## 2. สภาพแวดล้อม

- CI: ubuntu-24.04, Python 3.12, FFmpeg, Google Chrome ที่ติดมากับ runner; รัน `book-production doctor` แล้ว `python scripts/check.py`
- Local Linux: venv `.runtime/venv-platform` ของ Solo Empire
- Windows host: runtime หนัก (Blender 5.2.2, FreeCAD 1.1.3, Unity)

## 3. Test cases

| ID | FR | กรณีทดสอบ | ผลคาดหวัง | หลักฐาน |
|---|---|---|---|---|
| TC-01 | FR-01 | batch ใหม่แล้วรันซ้ำด้วย key เดิม | ครั้งที่สอง `replayed` ไม่เขียนไฟล์ | `test_jobs.py` |
| TC-02 | FR-01 | `request_id` ชนกัน / ไฟล์เดิมต่างเนื้อหา | `artifact_name_collision` / `artifact_exists_with_different_content` | `test_jobs.py`, `test_request_validation.py` |
| TC-03 | FR-02 | คอมไพล์ทุกธีมทุกขนาด | ได้ IR ที่ถูกต้อง ข้อความพอดี | `test_themes.py`, `test_templates.py` |
| TC-04 | FR-03 | override target ที่ไม่มีจริง | ถูกปฏิเสธ | `test_design_edit.py` |
| TC-05 | FR-04 | คำสั่ง AI ที่ไม่ชัด/โจมตี | `needs_input` หรือปฏิเสธ ไม่เปลี่ยนข้อความเอง | `test_design_edit.py`, `test_design_edit_art.py` |
| TC-06 | FR-05 | brief ที่มีค่านอก registry / ข้อความล้น | `rejected` / `needs_input` (`text_overflow`) | `test_brief_adapter.py` |
| TC-07 | FR-06 | compose หลายทางเลือก | ผ่าน QA ทุกขนาด, ไม่ซ้ำกันเกิน 80%, ซ้ำได้ด้วย seed | `test_compose.py`, `test_aesthetics.py`, `test_compose_eval.py` |
| TC-08 | FR-07 | campaign และ campaign-brief | เลย์เอาต์ไม่ซ้ำในแคมเปญ; ไม่แต่งข้อความ | `test_campaign.py`, `test_campaign_brief.py` |
| TC-09 | FR-08 | ใช้ art style กับธีม | สีตัวหนังสืออ่านง่ายบนลุคนั้น | `test_art_styles.py` |
| TC-10 | FR-09 | รูป, cutout, โลโก้ | วางถูก ไม่บังข้อความ; ไม่ตัดรูปที่ไม่ควรตัด | `test_assets.py`, `test_image_fx.py`, `test_type_and_hero.py` |
| TC-11 | FR-10 | ข้อความไทยยาว, contrast ต่ำ, story safe zone | ตัดคำถูก, รายงาน contrast, ข้อความอยู่ในโซน | `test_layout.py`, `test_box_text.py`, `test_qa.py` |
| TC-12 | FR-11 | export scale/DPI | PNG ขนาดถูก + PDF | `test_export.py`, `test_export_paths.py` |
| TC-13 | FR-12 | trace และ manual | มี `trace.json`/`MANUAL-STEPS.md` ไม่มีข้อความเนื้อหาใน trace | `test_trace_manual.py` |
| TC-14 | FR-13 | review → feedback → การจัดอันดับ | แนวที่ไม่ผ่านบ่อยถูกหักคะแนน | `test_review.py` |
| TC-15 | FR-14 | POST batch ผ่าน API | 201; key ชน 409; rejected 422 | `test_api.py` |
| TC-16 | FR-15 | scene-to-graphics | manifest โยงสอง stage; แก้ contract → `pipeline_conflict` | `tests/pipeline/test_scene_to_graphics.py` |
| TC-17 | FR-16 | doctor ไม่มี runtime บางตัว | exit 0 พร้อมรายงาน; lock เสีย → exit 2 | `test_doctor.py` |
| TC-18 | FR-24 | fork มีโมดูลที่ repo นี้ไม่มี | script fail | `test_graphics_fork_parity.py` |
| TC-19 | FR-17 ถึง FR-23 | test ของแต่ละสาย | ตาม README ของสาย | `tests/<สาย>/` (ไม่ได้รันในรอบนี้) |
| TC-U1 | FR-06, FR-13 | เจ้าของตรวจ 1 แคมเปญจริงใน `review.html` | ≥ 1 ทางเลือกผ่านต่อโพสต์ | **[ต้องยืนยัน]** |
| TC-U2 | FR-11 | ลูกค้ารับไฟล์และนำไปใช้ | ไม่มีการแก้กลับเรื่องข้อความผิด | **[ต้องยืนยัน]** |

## 4. เกณฑ์ผ่าน (entry / exit)

- Entry: `book-production doctor` ผ่าน, ติดตั้งฟอนต์ที่ pin ไว้
- Exit สำหรับ merge: `python scripts/check.py` ผ่าน (pytest + ruff + mypy strict)
- Exit สำหรับเปลี่ยนหน้าตา: golden test ผ่าน หรืออัปเดต golden หลังดูภาพส่วนต่าง
- Exit สำหรับเปิด AI live: shadow eval ผ่าน gate ของ flow นั้น + เจ้าของอนุมัติ
- Exit สำหรับส่งลูกค้า: คนเปิดดูทุกภาพก่อนส่งทุกครั้ง (คะแนนดีไซน์ไม่แทนสายตาคน)

## 5. หลักฐานที่มี

| หลักฐาน | วันที่ | ผล |
|---|---|---|
| รอบเขียนเอกสารนี้: `pytest tests/graphics tests/pipeline tests/test_doctor.py -q -p no:cacheprovider -x` ด้วย venv `.runtime/venv-platform` | 2026-10-08 | **445 passed, 17 skipped** (skip = golden test ที่ต้องตั้งฟอนต์และ resvg), 11 warnings (Pillow `getdata` deprecation) ใช้เวลา 101.9 วินาที |
| EVAL-GFX-compose14 (ไม่ใช้ AI / AI director v2) | 2026-10-06 | 10/10 คะแนน 89.4 / 9/10 คะแนน 94.5 |
| EVAL-GFX-compose-real14 (ไม่ใช้ AI) | 2026-10-08 | 14/14 คะแนน 88.1; director v3 11/12 คะแนน 96.1 |
| EVAL-GFX-campaign-brief10 กับ AI จริง | 2026-10-07 | ข้อความตรง 100%, ข้อความแต่งเอง 0, บล็อกโจมตี 2/2 |
| CI run แรกบน GitHub | — | **[ต้องยืนยัน]** (P0 ใน UPGRADE-PLAN) |
| test สายอื่น (spreadsheet, data, CAD, audio, game, BIM, scene3d) | — | ไม่ได้รันในรอบนี้ |

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| QA (Book Dev) | รอตรวจ | — | [ต้องยืนยัน] |

## แหล่งข้อมูล

- `tests/`, `scripts/check.py`, `.github/workflows/ci.yml`, `pyproject.toml`
- `docs/GRAPHICS-THEMES-GUIDE.md` §5D (ผล eval), `docs/UPGRADE-PLAN.md`
- ผลรัน pytest ในรอบเขียนเอกสาร 2026-10-08
