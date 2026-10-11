---
title: Logic-first Production Platform — Architecture and Cost Design
tags: [design, ai, production-platform, cost, domain-ir]
created: 2026-09-22
updated: 2026-09-22
status: proposed-written-design
description: กระบวนการผลิตข้ามโดเมนที่ใช้ logic เป็นหลักและเลือกโมเดลเฉพาะขั้นตอนตามคุณภาพและต้นทุนจริง
---

# Logic-first Production Platform

## 1. เป้าหมายและขอบเขต

ผู้ใช้ยืนยันแนวทาง structured representation / IR / API และให้ใช้ logic
แทน AI ทุกจุดที่แก้ด้วยกฎ สูตร หรือกระบวนการที่รู้แล้วได้ งานนี้ออกแบบส่วนที่เหลือ
นอกเหนือจาก Video ซึ่งกำลังพัฒนาแยกอยู่ ไม่แก้ Video หรือบังคับย้าย runtime ของมัน
เอกสารนี้เป็นข้อเสนอการออกแบบ ไม่ใช่รายงานว่าความสามารถทั้งหมดทำงานแล้ว

สมมติฐานสำหรับงานนำร่อง: ผู้ใช้เริ่มจากผลิตงานภายใน Solo Empire ด้วย Documents
แล้วเพิ่ม Graphics และ Spreadsheet เพื่อพิสูจน์การใช้ Core ร่วมกัน ก่อนเปิดเป็นสินค้า
หลายผู้ใช้ เป้าหมายคือผลลัพธ์แก้ไขต่อได้ ตรวจได้ และมีต้นทุนต่อชิ้นงานที่ผ่านเกณฑ์ต่ำ
ไม่ใช่จำนวน agent หรือจำนวนโมเดลมากที่สุด

ไม่อยู่ในรุ่นแรก: universal editor, autonomous research agent, การฝึก foundation model,
GPU ที่เปิดตลอดเวลา, production deployment, การซื้อบริการ และการสั่งเครื่องจักรจริง

## 2. หลักฐานและขอบเขตเดิม

- Architecture (Solo Empire `docs/ARCHITECTURE.md`): Solo Empire เป็น control plane/catalog;
  ผลิตภัณฑ์มี runtime, tests และ release lifecycle ของตัวเอง
- Central AI Gateway (Solo Empire `systems/ai-data/central-ai-gateway.md`): มีสัญญา
  hosted text generation แบบ opt-in; specialist GPU lanes เป็นแผนที่ต้องพิสูจน์
  ไม่ถือว่ามี OCR/STT/vision endpoints พร้อมใช้แล้ว
- [Video design](2026-09-22-ai-post-production-engine-design.md): ใช้ EDL และ
  engine แยกจาก reasoning; เชื่อมภายหลังผ่าน adapter ที่มีเวอร์ชัน
- AI agent guide (Solo Empire `docs/AI-AGENT-GUIDE.md`): ใช้ Thai-first, domain boundaries
  และ lake-first ingress ตามนโยบายเดิม

ต้องตรวจ implementation และ configuration อีกครั้งก่อน reuse ความสามารถที่เอกสารกล่าวถึง
ห้ามตีความว่าการมีเอกสารเท่ากับ deployed หรือมี credentials พร้อมใช้งาน

## 3. ทางเลือกและข้อสรุป

| ทางเลือก | ผลได้ | ต้นทุน/ข้อจำกัด |
|---|---|---|
| LLM planner ทุกงาน | รองรับคำสั่งอิสระง่าย | งานซ้ำยังเสีย inference และมีความไม่แน่นอน |
| Rules/templates เท่านั้น | ควบคุมง่าย ใช้โมเดลเป็นศูนย์ | ไม่ครอบคลุมข้อมูลไร้โครงสร้างหรือโจทย์ใหม่ |
| Logic-first + specialist models + bounded LLM | ใช้ของแน่นอนก่อน และเพิ่ม AI เฉพาะช่องว่าง | ต้องมี capability registry และชุดประเมิน |

เลือกทางที่สาม โดย router เป็นโค้ดและ policy ไม่ใช่ agent อีกตัวหนึ่ง
เลือกวิธีต้นทุนต่ำที่สุดจากวิธีที่ผ่านข้อกำหนดด้านคุณภาพ ความเป็นส่วนตัว และเวลา
ไม่บังคับลำดับ CPU → specialist → LLM ถ้าวิธีนั้นไม่รองรับงานตั้งแต่ต้น

## 4. กระบวนการหลัก

```text
Input / event / structured form
  → ingest + source metadata + provenance
  → exact recipe match / deterministic capability routing
  → typed processing DAG
       ├─ parser / rules / formula / solver / template
       ├─ specialist inference เฉพาะ step ที่ต้องใช้
       └─ bounded LLM เฉพาะตีความหรือวางแผนใหม่
  → domain IR + semantic/permission/budget validation
  → engine/API execution
  → output QA + evidence
  → preview / export / authorized downstream operation
```

คำสั่งภาษาคนที่ไม่ตรง recipe จึงค่อยใช้ intent extraction; structured input ที่ข้อมูลครบ
ไม่ต้องผ่าน LLM งานที่กำกวมและกระทบผลลัพธ์ต้องขอข้อมูลเพิ่ม ไม่เดา default สำคัญ
การทำงานแบบ generative เป็น capability เฉพาะ ไม่ใช่ fallback หลัง computer use

## 5. ชั้นประมวลผลและการเลือกโมเดล

| ชั้น | ตัวอย่าง | เกณฑ์เรียกใช้ |
|---|---|---|
| Deterministic | parse, resize, formulas, geometric constraints, render | มี input และ algorithm รองรับ |
| Specialist perception | OCR, STT, detection, segmentation | ต้องอ่านภาพ/เสียงที่ parser ปกติอ่านไม่ได้ |
| Specialist retrieval/prediction | embeddings, reranker, classifier | baseline search/rules ไม่ผ่าน benchmark ของงาน |
| Small language model | extract fields, classify intent, normalize brief | ต้องเข้าใจภาษาและผ่านชุดทดสอบเฉพาะโดเมน |
| Stronger language model | วางแผนหลายข้อจำกัดหรือซ่อมแผนที่ยาก | วิธีเล็กไม่ผ่านเกณฑ์และมี budget สำหรับ escalation |
| Generative media | สร้างภาพ/เสียง/asset ใหม่ | requirement ต้องการ asset ใหม่จริง |

คำว่า specialist ไม่รับประกันว่าเป็น non-LLM: registry ต้องระบุ model family,
capability, modality, revision และ runtime จริง OCR บางตัวอาจเป็น vision-language model
ชื่อชั้นไม่ใช่หลักฐานต้นทุนหรือคุณภาพ

เริ่มจาก model slots ตาม capability ยังไม่เลือกชื่อโมเดลหรือราคา ก่อนเลือกต้อง benchmark
ภาษาไทย/อังกฤษ รูปแบบข้อมูลจริง license, deployment, memory และต้นทุนทั้งหมด
ไม่ติดตั้งหรือดาวน์โหลดน้ำหนักโมเดลระหว่างขั้น design

## 6. Contracts ที่ต้องมี

ใช้ job envelope ร่วมกัน แต่ schema ภายในเป็นของแต่ละ domain

| Contract | ข้อมูลบังคับ |
|---|---|
| JobSpec | job_id, tenant/workspace, domain, recipe_version, input_refs, output_spec, privacy_class, deadline, budget_policy |
| StepSpec | operation, implementation_version, dependencies, input/output_schema, resource_limits, side_effect_class, retry_policy |
| Capability | supported_tasks/formats/languages, implementation_kind, model/engine_revision, device, privacy_support, benchmark_id, rate_card_version |
| DomainIR | schema_version, units, operations, constraints, source_refs, assumptions |
| ArtifactManifest | content_hash, producer/version, source_lineage, privacy, QA result, storage_ref |
| CostEvent | job/step/attempt_id, resource usage, reserved/estimated/actual cost, currency, rate_card_version, attribution |

facts จาก parser, derived values จากสูตร และ inferred values จากโมเดลต้องแยกชนิด
ทุก inference มี source reference และสถานะการตรวจ ไม่เชื่อ confidence ที่ LLM ให้ตัวเอง
model output เรียก tool ไม่ได้จนผ่าน schema, domain constraints และ allowlist
LLM ไม่ประกอบ shell command เพื่อรันโดยตรง; v1 ใช้ typed operations ที่ลงทะเบียนไว้

Core owns routing, execution bookkeeping, policy และ artifact metadata;
domain adapter owns IR, operations, validators และ quality rubric;
engine worker owns execution และ resource limits

## 7. Cost policy และ fallback

สูตรตัดสินใจคือ minimize expected total job cost ภายใต้ quality floor, privacy,
deadline และ authorization โดยรวม retry, failure และ human correction เมื่อวัดได้

```text
cost_per_accepted_output =
  (model + CPU/GPU + startup/idle allocation + storage/egress + external APIs
   + retries/failed jobs + measured human correction cost)
  / number_of_outputs_passing_QA
```

ถ้าไม่มี output ผ่าน QA ให้รายงาน undefined พร้อมต้นทุนที่เสีย ไม่หารด้วยศูนย์
แยก cost ที่วัดจริง ค่าประมาณ และรายการที่ยังไม่ทราบ; ห้ามรายงาน unknown เป็นศูนย์
บันทึกเวลาแก้โดยคนแยกต่างหากเมื่อยังไม่มีอัตราค่าแรง
ไม่บวก GPU session cost ซ้ำกับ cost ที่จัดสรรให้ job แล้ว

กติกาบังคับ:

1. กรอง capability ที่ไม่รองรับ task, privacy, language หรือ deadline ออกก่อน
2. ใช้ benchmark ที่ผ่านเกณฑ์เลือก candidate; งานประจำใช้ recipe ที่ pin version
3. reserve worst-case budget ของ step แบบ atomic ก่อน dispatch และ reconcile หลังจบ
4. shared job budget ต้องกัน concurrent workers ใช้เกิน; gateway budget เป็นอีกชั้น
   ไม่อ้างว่าตัวจำกัด process-local เดิมเพียงพอสำหรับ distributed jobs
5. หากไม่มี rate card หรือคำนวณเพดานทรัพยากรไม่ได้ ห้ามเริ่ม paid step
6. timeout/transport failure retry ได้เฉพาะเมื่อปลอดภัยและอยู่ใน attempt budget;
   ไม่เปลี่ยนไปโมเดลใหญ่เพียงเพราะ network ล่ม
7. quality failure ส่งเฉพาะ error และ context ที่เกี่ยวข้องเพื่อซ่อมได้หนึ่งครั้ง;
   escalation นับรวม model-call cap เดียวกัน ห้ามสร้าง loop ใหม่
8. งบไม่พอ ให้หยุดพร้อมเหตุผลและ preview ที่มี ห้ามลด quality floor เงียบ ๆ
9. cancellation ไม่ทำให้ค่าใช้จ่ายที่เกิดแล้วหายไป; ยุติ worker และ reconcile usage

ใช้ local CPU, warm GPU, batch หรือ hosted endpoint ตาม measured break-even
รวม queue latency, startup, idle และ utilization; local ไม่ถือว่าฟรี
ไม่มี auto-provision GPU หรือเพิ่มวงเงินเพราะ queue ยาว

## 8. ลดงานซ้ำและควบคุม context

- อ่าน embedded text ก่อน OCR และใช้ OCR เฉพาะหน้า/พื้นที่ที่จำเป็น
- ใช้ metadata/thumbnail/segment ที่เพียงพอก่อนโหลดไฟล์ใหญ่เข้าโมเดล
- cache key รวม tenant, input hash, privacy, operation, model/engine/schema/prompt
  versions และ parameters; ตรวจ ACL อีกครั้งทุกครั้งที่อ่าน
- shared AI result cache ใช้เฉพาะ public/internal ตามนโยบายเดิม;
  private/restricted ไม่เข้าระบบ shared cache และ hosted generation เดิมต้อง reject
- แยก artifact retention ที่อนุญาตออกจาก inference cache; deletion/retention policy
  ต้องครอบคลุม derived artifacts และ cache invalidation
- เปลี่ยน input แล้ว rerun เฉพาะ descendants ใน DAG; render ใหม่ไม่ต้องวิเคราะห์ใหม่
- batch เฉพาะงานที่ privacy, deadline และ capability เข้ากันได้
- recipe ที่ AI เสนอจะเป็น reusable recipe ได้หลัง regression tests และ review
- telemetry เก็บ usage/versions/errors ไม่เก็บ raw prompt, response หรือ private payload

## 9. งานนำร่อง Documents

V1A: structured form → template → document IR → DOCX/PDF → QA → preview
ต้องทำงานได้โดยไม่มี provider configuration และ model calls เท่ากับศูนย์
ใช้ fixed templates และ inputs แบบ text/numeric ที่กำหนด; ไม่รับ scanned uploads ใน V1A

V1B: เพิ่ม free-text brief → small-model extraction → validated fields → เส้นทาง V1A
missing required fields ต้องถามเพิ่ม ตัวเลขยอดรวมคำนวณด้วยสูตร ไม่ใช้ผลคำนวณจากโมเดล
V1C จึงเพิ่ม scanned input พร้อม OCR adapter และ provenance แยกตามหน้า

เพดานเริ่มต้นที่เสนอสำหรับ benchmark ไม่ใช่ผลที่วัดได้แล้ว:

| รายการ | Pilot policy |
|---|---|
| Input V1A | สูงสุด 100 line items, output สูงสุด 10 หน้า |
| V1B LLM context | สูงสุด 8,000 input tokens ต่อ call รวม schema/instructions |
| V1B LLM output | สูงสุด 2,000 tokens ต่อ call |
| LLM attempts | สูงสุด 2 calls ต่อ job รวม repair/escalation/retry |
| LLM timeout | 30 วินาทีต่อ call; ไม่เกิน job deadline |
| Job deadline | 120 วินาทีรวม queue และ processing; หมดเวลาแล้วหยุด |
| Latency target | V1A warm p95 ≤ 10 วินาทีบนเครื่อง benchmark ที่บันทึก specs |
| Paid budget | disabled โดย default; เปิดเมื่อกำหนด job/daily caps และ rate card |
| Cost target | V1B cost/accepted output ต่ำกว่า LLM-every-step baseline ≥ 30% ที่ quality floor เดียวกัน |

ตัวเลขทั้งหมดต้องทบทวนหลัง benchmark; หากไม่ผ่านให้รายงาน ไม่เปลี่ยน threshold ย้อนหลัง
inputs ใหญ่กว่า cap ให้คืน actionable error ไม่ truncate เนื้อหาสำคัญเงียบ ๆ
ไม่ต้องเรียก paid baseline จริงหากยังไม่มีสิทธิ์: แสดง modeled estimate และระบุว่ายังไม่พิสูจน์

## 10. การตรวจรับและ benchmark

สร้าง fixtures สังเคราะห์ 60 งาน: structured 20, clear free-text 20,
ambiguous/missing fields 10 และ invalid/adversarial 10; ไทยและอังกฤษอย่างละครึ่ง
ใช้ 40 งานพัฒนาและ 20 งาน held-out แบบ stratified โดยไม่จูนกับ held-out
ไม่ใช้ข้อมูลลูกค้าจริงเป็น fixture

- V1A structured cases: required content, currency rounding และ totals ถูกทุกกรณี;
  zero model calls; ไฟล์เปิดได้และ visual inspection ยืนยันว่าไม่ล้นในชุด golden
- V1B critical fields (ชื่อ จำนวน ราคา หน่วย) ต้องถูกหรือ abstain;
  ห้ามมี critical error ที่ไม่แจ้งใน acceptance suite
- clear briefs ต้องได้ accepted result อย่างน้อย 90% เพื่อกันการ abstain ทุกงาน
- invalid/ambiguous cases ต้อง reject/ขอข้อมูลตาม expected outcome ไม่เดาเอง
- ทดสอบ worker crash, timeout, exhausted budget, concurrent reservations,
  duplicate request, stale cache, missing asset และ unsupported format
- ตรวจ side effect replay ด้วย idempotency key และ outcome reconciliation;
  uncertain external write ห้าม retry อัตโนมัติจนรู้ผลเดิม
- เก็บ cold/warm latency, peak memory, all attempts, abstention, manual edits,
  quality และ cost/accepted output แยกตาม input class
- ตรวจด้วย logic ก่อน ใช้คนตรวจ golden layout/semantic correctness;
  ไม่ใช้โมเดลเดียวกันเป็นผู้ตัดสินความถูกต้องเพียงแหล่งเดียว

ผล pilot ไม่ใช่หลักฐาน production readiness ทุกโดเมน ต้องมี evaluation ของแต่ละ adapter

## 11. แผนขยาย 19 โดเมน

| กลุ่ม | Domain | Logic/engine หลัก | AI เฉพาะส่วนที่อาจต้องใช้ |
|---|---|---|---|
| A | Documents | templates, document tree, render | extraction, drafting, OCR |
| A | Graphics | SVG/layers, layout, resize | brief interpretation, segmentation, new assets |
| A | Spreadsheet | formulas, workbook graph | map columns, explain anomalies |
| B | Data | SQL, contracts, pipeline DAG | semantic mapping/classification |
| B | Data Viz | chart spec, aggregations | chart intent, narrative |
| B | Web | component templates, validation | content, constrained page planning |
| B | Workflow | DAG/state machine | propose unfamiliar workflow |
| B | Marketing | rules, campaign graph, API | creative brief, copy, audience hypotheses |
| C | Audio | DSP, timeline, mastering | STT, separation, synthesis |
| C | 3D | procedural geometry, scene graph | scene planning, specialized asset creation |
| C | Game | scene/ECS templates, runtime | scene/dialogue planning |
| D | CAD | feature tree, geometry kernel, solver | interpret requirements |
| D | BIM | building graph, quantity/rule checks | classify elements, interpret brief |
| D | GIS | spatial queries, CRS/geometry checks | imagery classification |
| D | Electronics | netlist, ERC/DRC, simulation | component search/requirements interpretation |
| E | Software | AST, build, tests | constrained code patch/review assistance |
| E | Infrastructure | declarative config, plan, policy | propose configuration change |
| E | Robotics | kinematics, trajectory, simulation | perception/task planning |
| E | Manufacturing | process/toolpath, simulation | interpret manufacturing intent |

AI column เป็น optional capabilities ไม่ใช่ dependency บังคับ ไม่เปิดทุก domain พร้อมกัน
งานทางวิศวกรรมต้องมี domain-specific acceptance และผู้รับผิดชอบก่อนใช้กับของจริง
Marketing เริ่มจาก campaign package; ไม่เปิดโฆษณาหรือใช้งบอัตโนมัติ

## 12. Ownership, rollout และขั้นถัดไป

เก็บ spec ใน control-plane documentation; implementation ของผลิตภัณฑ์นำร่องอยู่ใน
product boundary ตาม repository registry โดยตรวจของเดิมก่อนตั้งชื่อ repository ใหม่
ส่วนกลางที่พิสูจน์ว่าใช้ข้าม domain ได้จึงเป็น versioned shared package/service
ไม่เริ่มด้วยสร้าง 19 codebases และไม่ย้าย Video ในงานนี้

ลำดับส่งมอบหลัง design ผ่าน review:

1. Inventory implementation ที่ reuse ได้ และระบุ owner ของ Documents pilot
2. กำหนด schemas, deterministic recipe และ fixtures ของ V1A
3. ทำ V1A end-to-end พร้อม QA, resource caps และ cost events
4. พิสูจน์ failure/replay/budget behavior ก่อนเปิด model adapters
5. เพิ่ม V1B แบบ feature flag และ benchmark เทียบ baseline
6. เพิ่ม Graphics แล้ว Spreadsheet เพื่อทดสอบ Core reuse
7. เลือกสาย B/C/D/E ตามงานจริงและผลวัด ไม่เปิดพร้อมกัน

Pin recipe/schema/model/engine versions ในทุก job; rollout แบบ opt-in;
rollback โดยปิด adapter ใหม่และกลับ version ก่อนหน้า ไม่แก้ IR เก่าในที่เดิม
ยังไม่มี deployment target สำหรับ platform นี้: deployment_skipped_no_target
เอกสารฉบับนี้รอ review ก่อนแตก implementation plan และเขียน product code

## 13. Documents V1A pilot evidence — 2026-09-22

เริ่ม implementation ของ Documents V1A ใน worktree แยกแล้วผ่านชุด synthetic
fixtures 23 งาน (ภาษาอังกฤษ 10, ภาษาไทย 10, invalid/boundary 3):

| Metric | Measured result |
|---|---:|
| Accepted | 20/23 |
| Rejected boundary cases | 3/3 |
| Model calls | 0 |
| p50 latency | 0.037311 seconds/job |
| p95 latency | 0.046459 seconds/job |
| CPU time | 0.720110 seconds total |
| Bytes written | 1,195,238 |
| Cost status | unknown — no approved local rate card |

การวัดนี้เกิดจาก local Windows/Python 3.12 ด้วย synthetic data และ local DOCX/PDF
renderers จึงยืนยันเฉพาะ deterministic path, validation, replay และ QA ของ V1A
ไม่ใช่ production readiness หรือหลักฐานต้นทุนต่อหน่วยจริง งานที่ยอมรับทั้งหมดมี
`model_calls=0`; fixture ที่ตกถูกปฏิเสธก่อนมี accepted manifest
