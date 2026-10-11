# Workflow V1A / Form-to-Document Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ส่งมอบ Form-to-Document Flow รุ่นแรกที่ตรวจ DAG, retries, permissions และ idempotency แล้วสร้าง execution plan กับเอกสารที่ review ได้โดยไม่เรียก external API หรือสั่งงานจริง

**Architecture:** structured workflow request → validated WorkflowIR → topological execution plan → deterministic Markdown document → QA → replay-safe manifest. V1A เป็น plan-only; form values ผ่าน schema validation, human-review steps fail closed, และ external effects ปิดเสมอ

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, stdlib JSON/hashlib/pathlib, pytest, Ruff, mypy

**Spec:** `docs/superpowers/specs/2026-09-22-logic-first-production-platform-design.md` และ `docs/superpowers/plans/2026-09-22-production-platform-next-products.md`

## Global Constraints

- V1A รับเฉพาะ structured input และรายงาน `model_calls=0`
- DAG ต้องไม่มี cycle, dependency ที่ไม่มีอยู่ หรือ duplicate step/field IDs
- retry สูงสุด 3 attempts ต่อ step; human-review step ต้องมี role ที่อยู่ใน permission policy
- external effects, notifications, API calls และ document publishing ปิดเป็นค่าเริ่มต้น
- replay ตรวจ manifest schema/key/status, artifact paths, byte sizes และ SHA-256

## Review Focus

- cycle, dangling dependency และ duplicate IDs ต้อง reject ก่อนเขียน artifact
- required form values และ field types ต้อง validate ก่อน document render
- missing approver role หรือ retry เกิน cap ต้อง fail closed
- document values ต้อง escape Markdown-sensitive text และไม่มี unresolved placeholders
- tampered execution/document artifacts และ manifest ต้องไม่ผ่าน replay

### Task 1: Workflow IR, DAG validator, and document compiler

**Files:** `packages/workflow-production-ir/`

- [x] Write failing tests for valid Thai/English flows, typed values, duplicate IDs, dangling deps, cycles, missing values, permissions, and retry caps.
- [x] Run focused tests and observe import failure.
- [x] Implement Pydantic models, topological compiler, stable issue codes, and plan-only policy.
- [x] Run tests and commit `feat: add workflow form-to-document ir contract`.

### Task 2: Plan/document renderer and replay-safe job

**Files:** `services/workflow-production-api/`

- [x] Write failing tests for execution plan, Markdown output, QA, replay, conflict, and tampering.
- [x] Run focused tests and observe failure.
- [x] Implement deterministic render, QA, atomic writes, per-key lock, and manifest.
- [x] Run tests and commit `feat: add replay-safe workflow form-to-document jobs`.

### Task 3: API, CLI, benchmark, and docs

- [x] Write failing API/CLI/20-workflow benchmark tests.
- [x] Implement bounded interfaces, fixtures, README, and update the platform roadmap.
- [x] Run product tests, Ruff, mypy, benchmark, and `git diff --check`.
- [x] Commit `feat: expose workflow form-to-document v1a`.

No deployment target exists; record `deployment_skipped_no_target`.
