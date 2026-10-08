---
title: Book Production Platform — Runbook การติดตั้งและดูแลระบบ
doc_id: BPP-DOC-04
doc_type: runbook
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: Operator / DevOps (Book Dev)
related: 02-architecture.md, 03-nfr-security.md, 06-ux-flows.md, 07-test-uat.md
---

# Book Production Platform — Runbook การติดตั้งและดูแลระบบ

> ขั้นตอนรับงานลูกค้าแบบเต็ม (brief → ส่งไฟล์ → ปิดงาน) อยู่ที่ `projects/marketing/growth/book-sales/templates/offers/graphic-job-runbook.md` ของ Solo Empire ส่วนคำสั่งกราฟิกทั้งหมดอยู่ใน `docs/GRAPHICS-THEMES-GUIDE.md`

## 1. Environments

| Env | ใช้ทำอะไร | หมายเหตุ |
|---|---|---|
| Linux workstation (Solo Empire) | graphics, spreadsheet, data, audio | venv `.runtime/venv-platform` ของ Solo Empire |
| Windows host | scene3d (Blender), CAD/BIM (FreeCAD), game (Unity) | `docs/UPGRADE-PLAN.md`: ทดสอบ runtime หนักบน Windows เท่านั้น |
| CI | `scripts/check.py` บน ubuntu-24.04 | ไม่มี Blender/FreeCAD/Unity → test ที่ต้องใช้จะ skip พร้อมเหตุผล |
| Production server | ไม่มี | เครื่องมือ local; ไม่มี deploy target |

## 2. ติดตั้ง / build

```bash
python3.12 -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[test]"            # พื้นฐาน + test
python -m pip install -e ".[preview,thai,cutout]"   # resvg, PyThaiNLP, rembg (ไม่บังคับ)
book-production doctor                         # ตรวจ runtime แบบ offline
```

ไม่มีขั้น build/deploy แยก การ "ปล่อย" คือ commit ที่ CI ผ่านแล้วอัปเดต venv บนเครื่องที่ใช้งาน

## 3. Config (ชื่อตัวแปรเท่านั้น)

| กลุ่ม | ตัวแปร |
|---|---|
| Graphics output | `GRAPHICS_PRODUCTION_OUTPUT_ROOT` |
| Rasterizer | `GRAPHICS_PRODUCTION_BROWSER`, `GRAPHICS_PRODUCTION_BROWSER_PROFILE`, `GRAPHICS_PRODUCTION_RASTERIZER` (`resvg`) |
| ฟอนต์ | `GRAPHICS_PRODUCTION_FONT_DIRS`, `GRAPHICS_PRODUCTION_FALLBACK_FONT` |
| Cutout | `GRAPHICS_PRODUCTION_CUTOUT_CACHE`, `GRAPHICS_PRODUCTION_CUTOUT_MODEL`, `U2NET_HOME`, `XDG_CACHE_HOME` |
| Style library | `GRAPHICS_PRODUCTION_STYLE_DIR` |
| Golden test | `GRAPHICS_UPDATE_GOLDENS` |
| สายอื่น | `BLENDER_BIN`, `FFMPEG_BIN`, `FFPROBE_BIN`, `FREECARDCMD_BIN`, `UNITY_BIN`, `SPREADSHEET_PRODUCTION_OUTPUT_ROOT`, `DATA_PIPELINE_OUTPUT_ROOT` |
| AI provider | ไฟล์ config (`graphics-provider-config.v1` ฯลฯ) ใน `.runtime/provider-configs/` เก็บ gateway URL และ *ชื่อ* env ของ token; เปลี่ยน `mode` เป็น `live` ได้เฉพาะเจ้าของหลังดูผล eval |

## 4. Health check

```bash
book-production doctor --format json      # runtime ที่พร้อม
book-production graphics themes           # registry โหลดได้
book-production graphics make --theme synthwave --variant square \
  --set headline=TEST --output /tmp/gfx-smoke --idempotency-key smoke-1
python -m book_production_platform.production_trace verify --root /tmp/gfx-smoke <batch_id>
```

HTTP API (ถ้าเปิดใช้): `GET /health` ต้องตอบ `{"service": "graphics-production-api", "contract_version": "graphics.v1a", ...}`

## 5. Rollback

1. กลับไป commit/tag ก่อนหน้าแล้ว `pip install -e .` ใหม่
2. หน้าตาเพี้ยนหลังอัปเดต: รัน `pytest tests/graphics/test_golden.py` (ตั้ง `GRAPHICS_PRODUCTION_FONT_DIRS`) ดูภาพส่วนต่างก่อนใช้งานกับลูกค้า
3. AI ให้ผลแย่: เปลี่ยน config กลับเป็น `mode: shadow` แล้วใช้เส้นทางไม่ใช้ AI (ค่าเริ่มต้น)
4. งานเดิมของลูกค้า render ใหม่ได้จาก request/plan ที่เก็บไว้ (deterministic)

## 6. Incident / troubleshooting

| อาการ / error code | สาเหตุที่พบบ่อย | แก้ |
|---|---|---|
| ฟอนต์ไม่ตรงใน PNG/Affinity | ยังไม่ติดตั้งฟอนต์ที่ธีมใช้ | ติดตั้งฟอนต์ใน `.runtime/fonts`, ตั้ง `GRAPHICS_PRODUCTION_FONT_DIRS` |
| ไม่มี PNG | ไม่มี Chrome/Edge | ติดตั้ง `.[preview]` และตั้ง `GRAPHICS_PRODUCTION_RASTERIZER=resvg` |
| `text_overflow` / `needs_input` | ข้อความยาวเกินกล่อง หรือ brief ไม่ชัด | ย่อข้อความหรือตอบคำถามใน `questions` |
| `artifact_name_collision` | `request_id` ซ้ำใน batch เดียว | เปลี่ยน id |
| `artifact_exists_with_different_content` | โฟลเดอร์ batch เดิมมีไฟล์ต่างเนื้อหา | ใช้ idempotency key ใหม่หรือ output ใหม่ |
| `provider_not_configured` / `shadow_mode_only` | ไม่มี config หรือยังเป็น shadow | ใช้เส้นทางไม่ใช้ AI หรือขออนุมัติเจ้าของ |
| `pipeline_conflict` | contract/manifest ที่เผยแพร่ถูกแก้ | ใช้ output root ใหม่ |
| cutout ช้า 5–20 วินาที | ครั้งแรกของรูปนั้น | ปกติ; ครั้งต่อไปใช้ cache |
| รายละเอียดอื่น | — | `docs/GRAPHICS-THEMES-GUIDE.md` §9 แก้ปัญหาที่พบบ่อย |

## 7. Backup / restore

- สิ่งที่ควร backup: โฟลเดอร์งานลูกค้า (`clients/<ลูกค้า>/graphics/<job-id>/`), `.runtime/graphics-layouts` (รวม `feedback.jsonl`), style library, provider configs (ไม่รวมค่า token)
- วิธี backup ที่ใช้จริง **[ต้องยืนยัน]**; ข้อเสนอ **[สมมติฐาน]**: สำรองผ่านกลไก backup ของ Solo Empire workspace รายวัน
- Restore: คัดลอกโฟลเดอร์กลับ แล้วรัน `production_trace verify` เพื่อตรวจ hash

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Operator (Book Dev) | รอตรวจ | — | [ต้องยืนยัน] |

## แหล่งข้อมูล

- `README.md` (Doctor, Graphics V1A, Job history, Pipeline)
- `docs/GRAPHICS-THEMES-GUIDE.md` §2, §5D, §6, §9
- `docs/UPGRADE-PLAN.md`, `pyproject.toml`, `.github/workflows/ci.yml`
- `src/book_production_platform/graphics_production/main.py`
- Solo Empire: `projects/marketing/growth/book-sales/templates/offers/graphic-job-runbook.md`
