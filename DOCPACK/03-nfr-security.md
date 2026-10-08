---
title: Book Production Platform — NFR และความปลอดภัย
doc_id: BPP-DOC-03
doc_type: nfr-security
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: Solution Architect + Security (Book Dev)
related: 02-architecture.md, 04-runbook.md, 07-test-uat.md
---

# Book Production Platform — NFR และความปลอดภัย

## 1. NFR พร้อมเป้าหมายที่วัดได้

| ID | หมวด | ข้อกำหนด | เป้าหมาย | หลักฐาน/สถานะ |
|---|---|---|---|---|
| NFR-01 | Determinism | input เดิม → ผลเดิม; random motif seed จาก theme/request/item; compose ซ้ำได้ด้วย `--seed` | replay ได้ `replayed` 100% | `test_jobs.py`, `test_compose.py`, README |
| NFR-02 | Integrity | artifact หรือ manifest ที่ถูกแก้ต้องถูกปฏิเสธ ไม่เขียนทับ | `replay_integrity_error` / `artifact_exists_with_different_content` | `test_jobs.py`, UPGRADE-PLAN pass 2 |
| NFR-03 | Performance (ไม่ใช้ AI) | compose ต่องาน | ~0.2 วินาที (EVAL-GFX-compose14, 2026-10-06) | guide §5D |
| NFR-04 | Performance (campaign) | 10 โพสต์ × 2 ขนาด | ≤ 30 วินาทีบน 4 workers **[สมมติฐาน]** (วัดได้ 21 วินาที) | guide §5D |
| NFR-05 | Performance (AI director) | latency/token ต่องาน | ~57 วินาที, ~10.6k token (v2) | guide §5D |
| NFR-06 | Cost | ค่า AI ต่อแบบ | ~$0.003 (art director, บันทึก 2026-10-06); ค่า compute รายงานเป็น `unknown` | guide §5D, README |
| NFR-07 | Capacity | batch ≤ 100 items (API), spec pipeline ≤ 64 KiB, campaign ≤ 200 โพสต์ | ปฏิเสธเมื่อเกิน | `main.py`, README, guide |
| NFR-08 | AI bounds | ≤ 2 model calls ต่องาน, structured case 0 calls, canary leak 0 | ตาม eval gate | README §AI brief adapter |
| NFR-09 | Quality | ข้อความไม่ล้น/ไม่ทับ/ไม่หลุดขอบ, contrast WCAG AA, story safe zone 10.5%–85.5% | ทุกแบบที่ส่งออก | `qa.py`, guide §5D |
| NFR-10 | Thai typography | วรรณยุกต์ไม่ขึ้นต้นบรรทัด, คำไทยไม่ถูกตัดจนถึงขนาดเล็กสุด | 100% | `test_layout.py`, `test_box_text.py` |
| NFR-11 | Visual regression | golden ของทุกธีม + compose 3 งาน | ส่วนต่างไม่เกินเกณฑ์ | `test_golden.py` (ต้องตั้งฟอนต์ จึง skip ในรอบนี้) |
| NFR-12 | Portability | Windows + Linux; mypy strict ผ่าน | CI Linux เขียว; Windows CI ยังไม่มี (P1) | `docs/UPGRADE-PLAN.md` |
| NFR-13 | Availability | — | ไม่ใช่บริการออนไลน์; ไม่มี SLO | — |

## 2. Security controls

| ID | Control | รายละเอียด |
|---|---|---|
| SEC-01 | Offline by default | asset ไม่ออกจากเครื่อง; เครือข่ายเฉพาะขั้น AI ไปยัง gateway |
| SEC-02 | AI fail closed | ไม่มี provider config → `provider_not_configured`; `mode: shadow` → `shadow_mode_only` |
| SEC-03 | Token handling | provider config เก็บ *ชื่อ* env ของ token (`token_env`) ไม่เก็บค่า |
| SEC-04 | Prompt injection | brief/ข้อความที่เป็นคำสั่ง, path, สคริปต์ → `needs_input` หรือถูกปฏิเสธโดยไม่เรียกโมเดล; eval มีเคส adversarial และ canary |
| SEC-05 | Grounded text | ข้อความที่ AI เสนอต้องคัดลอกตรงจาก brief/instruction; campaign-brief วัดได้ข้อความแต่งเอง 0 |
| SEC-06 | Input validation | Pydantic `extra="forbid"`, schema IR, allowlist ของ preset/registry |
| SEC-07 | Unsafe SVG / path | รูปเฉพาะไฟล์ local, SVG ไม่ปลอดภัยถูกปฏิเสธ; pipeline ห้าม absolute path/backslash/traversal (`unsafe_output_path`) |
| SEC-08 | Browser isolation | ใช้ profile แยก (`GRAPHICS_PRODUCTION_BROWSER_PROFILE`) |
| SEC-09 | ข้อจำกัดที่ยอมรับ | ยังไม่ใช่ OS sandbox สำหรับไฟล์จากผู้ใช้ที่ไม่น่าเชื่อถือ (README §ขอบเขต V1A) |
| SEC-10 | HTTP API | ไม่มี authentication ใน `main.py` — ต้องผูกกับ localhost เท่านั้น **[ต้องยืนยัน]** ว่าไม่มีการเปิดออกนอกเครื่อง |
| SEC-11 | CI permissions | `permissions: contents: read` |

## 3. Privacy / PDPA

- Trace เก็บเฉพาะ hash, id, ขนาด และจำนวน ไม่เก็บข้อความเนื้อหา (README §Job history)
- ไฟล์งานลูกค้า (รูป, โลโก้, ข้อความ) อยู่ในโฟลเดอร์งานบนเครื่อง operator; นโยบายเก็บ/ลบหลังส่งงาน **[ต้องยืนยัน]** (ดู `graphic-terms.md` ของ book-sales)
- รูปคนในงานลูกค้าเป็นข้อมูลส่วนบุคคล: ควรมีความยินยอมจากลูกค้า **[สมมติฐาน]**
- ชุดรูป eval ใช้รูป CC0 พร้อม `CREDITS.md`; ฟอนต์ OFL

## 4. Observability

| สัญญาณ | ที่มา |
|---|---|
| ประวัติงาน | `history.jsonl`, `production_trace history` |
| รายละเอียดงาน | `trace.json`, `ir.json`, `MANUAL-STEPS.md`, `production_trace verify` |
| คุณภาพ | `campaign-summary.json`, `feedback-report`, รายงาน shadow eval |
| Runtime | `book-production doctor --format json` |
| Telemetry | `telemetry.py` นับ fresh/replay, bytes ที่เขียน และ model calls ต่อ batch |

## 5. Backup / DR

- ไม่มีฐานข้อมูล; ข้อมูลสำคัญคือโฟลเดอร์งานลูกค้า, คลังเลย์เอาต์ (`feedback.jsonl` ใช้ในการจัดอันดับ) และ style library
- แผน backup ของ `.runtime/` และโฟลเดอร์งานลูกค้ายังไม่มีหลักฐาน **[ต้องยืนยัน]**
- กู้คืนงาน: เนื่องจาก deterministic งานที่มี request/plan เดิมสามารถ render ใหม่ได้ ยกเว้นผลที่มาจาก AI (ต้องเก็บ `design-request.json`/แผนไว้)

## การอนุมัติ

| บทบาท | สถานะ | วันที่ | ลงนาม |
|---|---|---|---|
| Solution Architect (Book Dev) | รอตรวจ | — | [ต้องยืนยัน] |
| Security reviewer | รอตรวจ | — | [ต้องยืนยัน] |

## แหล่งข้อมูล

- `README.md` (Graphics V1A, AI brief adapter, Job history, Pipeline ขอบเขตและ error, ขอบเขต V1A)
- `docs/GRAPHICS-THEMES-GUIDE.md` §5D, `docs/UPGRADE-PLAN.md`
- `src/book_production_platform/graphics_production/main.py`, `provider_config.py`, `qa.py`
- `.github/workflows/ci.yml`, `pyproject.toml`
