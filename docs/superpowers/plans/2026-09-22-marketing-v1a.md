# Marketing V1A / Campaign Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ส่งมอบ Campaign Package รุ่นแรกที่รับ campaign brief แบบมีโครงสร้าง ตรวจ rules และ asset references แล้วสร้างแพ็กเกจที่ review ได้โดยไม่ publish หรือใช้งบ

**Architecture:** structured request → validated CampaignIR → deterministic channel/asset rules → immutable JSON package + manifest → reopen/content QA → idempotent replay. V1A ไม่มี LLM, network fetch, ad API หรือ side effect ภายนอก; optional copy/brief extraction เป็น capability ภายหลังที่ต้องผ่าน gateway และ validation เดิม

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, stdlib JSON/hashlib/pathlib, pytest, Ruff, mypy

**Spec:** `docs/superpowers/specs/2026-09-22-logic-first-production-platform-design.md` และ `docs/superpowers/plans/2026-09-22-production-platform-next-products.md`

## Global Constraints

- V1A รับเฉพาะ structured input; ไม่เรียก LLM หรือ hosted provider (`model_calls=0`)
- asset URI ต้องเป็น local path ภายใต้ `asset_root` หรือ `inline:`; ห้าม network URL, `data:`, `javascript:` และ path traversal
- package ไม่มี publish, budget spend, audience sync หรือ external API action
- output ต้อง deterministic, มี content hash, schema/recipe versions, source lineage และ idempotency replay
- input telemetry ไม่เก็บ raw brief หรือ private payload; API response เปิดเผยเฉพาะ package references ที่อยู่ใน output root

## Review Focus

- asset path ที่ชี้ออกนอก root หรือใช้ URL ต้อง reject ก่อนเขียนไฟล์
- channel ซ้ำ, asset ID ซ้ำ, channel ที่ไม่มี asset และ required field หายต้องคืน issue ที่แก้ได้
- copy ที่ขึ้นต้นด้วย `=`, `+`, `-`, `@` ต้องถูกเก็บเป็น text ใน JSON package โดยไม่กลายเป็น executable content
- key เดิมกับ input ใหม่ต้อง conflict และ replay ต้องตรวจ artifact hash/size/path ก่อนคืนผล
- malformed JSON หรือ package ที่ถูกแก้หลังสร้างต้องไม่ถูกยอมรับเป็น replay สำเร็จ

### Task 1: Campaign IR และ validator

**Files:**
- Create: `packages/marketing-production-ir/pyproject.toml`
- Create: `packages/marketing-production-ir/marketing_ir/models.py`
- Create: `packages/marketing-production-ir/marketing_ir/validation.py`
- Create: `packages/marketing-production-ir/marketing_ir/__init__.py`
- Test: `packages/marketing-production-ir/tests/test_validation.py`

**Interfaces:**
- `CampaignRequest.model_validate(payload)` parses structured input.
- `validate_request(payload_or_request) -> list[ValidationIssue]` returns stable issues.
- `compile_campaign(request) -> CampaignIR` creates normalized channel and asset order.

- [x] **Step 1: Write the failing tests** for valid Thai/English campaigns, missing required fields, duplicate IDs/channels, unsafe URI, path traversal, empty channel assets, and formula-like copy staying text.
- [x] **Step 2: Run the focused tests and verify they fail** because the package is absent.
- [x] **Step 3: Implement the minimal Pydantic models and pure validator** with bounded lengths, URI policy, and deterministic ordering.
- [x] **Step 4: Run the focused tests and verify they pass.**
- [x] **Step 5: Commit** `feat: add marketing campaign ir contract`.

### Task 2: Deterministic package job and QA

**Files:**
- Create: `services/marketing-production-api/pyproject.toml`
- Create: `services/marketing-production-api/marketing_production/jobs.py`
- Create: `services/marketing-production-api/marketing_production/qa.py`
- Create: `services/marketing-production-api/marketing_production/telemetry.py`
- Create: `services/marketing-production-api/marketing_production/__init__.py`
- Test: `services/marketing-production-api/tests/test_jobs.py`

**Interfaces:**
- `run_campaign_job(payload, output_dir, idempotency_key) -> CampaignJobResult`.
- `CampaignJobResult.to_dict()` exposes status, package path, hash, `model_calls`, and bytes written.

- [x] **Step 1: Write the failing tests** for package creation, reopen QA, manifest lineage, zero model calls, replay with zero writes, changed-input conflict, and tampered-artifact rejection.
- [x] **Step 2: Run the focused tests and verify they fail.**
- [x] **Step 3: Implement canonical hashing, atomic JSON writes, package/manifest QA, and per-key replay lock.**
- [x] **Step 4: Run the focused tests and verify they pass.**
- [x] **Step 5: Commit** `feat: add replay-safe marketing campaign packages`.

### Task 3: API, CLI, benchmark, and documentation

**Files:**
- Create: `services/marketing-production-api/marketing_production/main.py`
- Create: `services/marketing-production-api/marketing_production/cli.py`
- Create: `services/marketing-production-api/services/README.md`
- Create: `services/marketing-production-api/tests/test_api.py`
- Create: `services/marketing-production-api/tests/test_cli.py`
- Create: `services/marketing-production-api/tests/test_benchmark.py`
- Create: `services/marketing-production-api/marketing_production/scripts/benchmark.py`
- Create: `services/marketing-production-api/tests/fixtures/campaigns_20.json`
- Modify: `services/README.md`
- Modify: `docs/superpowers/plans/2026-09-22-production-platform-next-products.md`

- [x] **Step 1: Write the failing API, CLI, and 20-case benchmark tests** for health, 201/409/422 mapping, JSON output, acceptance counts, asset lineage, and `model_calls=0`.
- [x] **Step 2: Run the focused tests and verify they fail.**
- [x] **Step 3: Implement bounded API/CLI, synthetic benchmark, README usage, and mark Marketing V1A complete in the delivery plan.**
- [x] **Step 4: Run product tests, Ruff, mypy, benchmark, and `git diff --check`.**
- [x] **Step 5: Commit** `feat: expose marketing campaign package v1a`.

## Completion Evidence

```text
python -m pytest packages/marketing-production-ir/tests services/marketing-production-api/tests -q
ruff check services/marketing-production-api packages/marketing-production-ir
mypy --strict services/marketing-production-api/marketing_production packages/marketing-production-ir/marketing_ir
python -m marketing_production.scripts.benchmark --fixture tests/fixtures/campaigns_20.json --output-dir .runtime/marketing-v1a-benchmark
```

No deployment target exists for this local-first product; record `deployment_skipped_no_target`.

## Readiness hardening — complete

- blank identifiers are rejected before package creation
- absolute local asset references are normalized to root-relative lineage
- replay verifies manifest schema, accepted status, idempotency key, package
  path, byte size, SHA-256, and package QA before returning an accepted result
