# Documents — เทียบความสามารถกับโปรแกรมจริง

เทียบ `documents-v1a` (+ V1B brief adapter, V1C scan import) กับ Microsoft Word,
Google Docs, และโปรแกรมออกเอกสารธุรกิจ (เช่น FlowAccount, Zoho Invoice)
สถานะ: **มี** = ทำได้และมีเทสต์, **บางส่วน** = ทำได้แบบจำกัด, **ยังไม่มี**

| ความสามารถ | โปรแกรมจริง | สถานะ | ลำดับ |
|---|---|---|---|
| สร้าง DOCX + PDF จากข้อมูลโครงสร้าง | Word / Docs | มี | — |
| ภาษาไทยใน PDF (ฟอนต์ฝัง) | Word | มี (ต้องตั้ง `DOCUMENT_PRODUCTION_FONT`) | — |
| ตาราง (Table Grid), รูปพร้อม hash, คำบรรยาย | Word | มี | — |
| ยอดรวมคำนวณด้วย Decimal + QA ตรวจซ้ำ | โปรแกรมบัญชี | มี | — |
| เทมเพลต: invoice, transcript, report, proposal, spec sheet | Word templates | มี | — |
| ส่วนท้ายพร้อมเลขเอกสาร + เลขหน้า | Word (Insert ▸ Page Number) | **มี (2026-10-03)** | — |
| ประวัติงานย้อนหลัง + trace + ตรวจ hash | Version history | **มี (2026-10-03)** `production_trace` | — |
| ขั้นตอนทำเองใน Word ทีละคลิก | — | **มี (2026-10-03)** `MANUAL-STEPS.md` | — |
| AI แปลง brief → ข้อมูล (fail-closed, grounded) | Copilot in Word | มี (shadow จนกว่าเจ้าของอนุมัติ) | — |
| อ่านสแกน PDF → เสนอค่า field | Adobe Acrobat OCR | บางส่วน (OCR runtime ยังไม่ติดตั้ง) | สูง |
| หัวกระดาษ/โลโก้บริษัท (letterhead) | Word header | ยังไม่มี | สูง |
| เลขที่เอกสารรันต่อเนื่อง + สถานะ (draft/issued/void) | FlowAccount | ยังไม่มี | สูง |
| ภาษีหัก ณ ที่จ่าย, ส่วนลด, มัดจำ | โปรแกรมบัญชีไทย | ยังไม่มี | สูง |
| ลายเซ็นอิเล็กทรอนิกส์ / ตราประทับ | DocuSign, Adobe Sign | ยังไม่มี | กลาง |
| สารบัญอัตโนมัติ, หัวข้อหลายระดับ | Word TOC | ยังไม่มี | กลาง |
| สไตล์/ธีมแบรนด์ (ฟอนต์, สี) | Word themes | ยังไม่มี | กลาง |
| ลายน้ำ (DRAFT/COPY) | Word watermark | ยังไม่มี | กลาง |
| Track changes / comments | Word review | ยังไม่มี | ต่ำ |
| Mail merge หลายฉบับจาก CSV | Word mail merge | ยังไม่มี (ทำได้ด้วยการเรียก job หลายครั้ง) | กลาง |

## ลำดับถัดไปที่แนะนำ

1. Letterhead + ข้อมูลผู้ขาย (ชื่อ ที่อยู่ เลขผู้เสียภาษี) จาก brand kit
2. เลขที่เอกสารรันอัตโนมัติ + สถานะเอกสาร (ต่อกับ job history)
3. ส่วนลด / หัก ณ ที่จ่าย / มัดจำ (คำนวณใน recipe เท่านั้น)
4. OCR runtime จริง (Poppler + Tesseract) เพื่อปิด V1C
