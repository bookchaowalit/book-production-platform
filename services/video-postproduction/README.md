# AI Video Post-production runtime

นี่คือ runtime แบบ local-first สำหรับ Video Post-production Engine V1
ประกอบด้วย FastAPI, Celery worker, PostgreSQL, Redis และ Next.js control
surface โดย original media อยู่ใน named volume เดียวกับ worker เพื่อไม่ต้อง
ส่งวิดีโอเต็มเข้า provider ภายนอกโดย default

## เปิดใช้งาน

จาก repository root:

```text
docker compose -f services/video-postproduction/docker-compose.yml up --build
```

เปิดหน้าเว็บที่ `http://127.0.0.1:3400` และ API health ที่
`http://127.0.0.1:8000/healthz` การเริ่ม API จะรัน Alembic migration ก่อน
เปิด Uvicorn โดยรอ PostgreSQL และ Redis ให้ healthy ก่อนเสมอ

API จะรับ browser origin เฉพาะจาก `CORS_ALLOWED_ORIGINS` ซึ่งค่าเริ่มต้นครอบคลุม
`http://127.0.0.1:3400` และ `http://localhost:3400` เท่านั้น หากเปลี่ยนพอร์ตหรือ
นำ control surface ไปวางที่ origin อื่น ให้ตั้งค่าเป็นรายการคั่นด้วย comma

หยุดและลบ container ได้ด้วย:

```text
docker compose -f services/video-postproduction/docker-compose.yml down
```

ข้อมูล PostgreSQL, Redis, media และ model cache อยู่ใน named volumes
`postgres_data`, `redis_data`, `video_storage`, `whisper_cache` ตามลำดับ

## แอป Native สำหรับ Windows

ติดตั้ง Docker Desktop สำหรับ Windows และเปิดใช้ Linux engine ก่อนเปิด
Solo Empire Video Studio จาก Start Menu การติดตั้งแอปเป็นแบบต่อผู้ใช้และไม่ต้องใช้
สิทธิ์ Administrator ตัวแอปเชื่อมต่อกับ Compose project เดิมชื่อ
`video-postproduction`; หากบริการพร้อมอยู่แล้วจะเข้าใช้งาน runtime เดิมและ named
volumes เดิม ไม่สร้างชุดฐานข้อมูลหรือพื้นที่สื่อใหม่

การเริ่มครั้งแรกอาจต้องสร้าง image และดาวน์โหลด dependency ผ่านอินเทอร์เน็ต จึงควร
เผื่อพื้นที่ดิสก์ให้ Docker Desktop; การถอดเสียงครั้งแรกอาจดาวน์โหลดโมเดลเพิ่มเติม
ตัวติดตั้ง pilot ที่สร้างในเครื่องยังไม่ลงลายเซ็นดิจิทัล Windows อาจแสดงคำเตือน
SmartScreen และควรใช้เฉพาะ artifact ที่ตรวจสอบแหล่งที่มาได้

ปิดหน้าต่างแอปแล้ว runtime จะยังทำงานต่อ หากต้องการหยุดบริการที่แชร์กับหน้าเว็บ/API
ให้เลือก “หยุด Engine” โดยตั้งใจเท่านั้น การถอนการติดตั้ง desktop shell จะไม่ลบ
Compose project, named volumes (`postgres_data`, `redis_data`, `video_storage`,
`whisper_cache`) หรือไฟล์สื่อ; อย่าใช้ `docker compose down -v` หากต้องการเก็บข้อมูล

มี runtime smoke สำหรับตรวจการ start/stop ซ้ำแบบเลือกใช้ด้วยตนเองผ่าน
`npm run smoke:runtime` ใน `apps/video-postproduction-desktop` หลัง stage runtime
เท่านั้น โดยใช้ project แยก `video-postproduction-native-smoke-test` และพอร์ต
loopback `18000`/`13400`; ไม่แตะ project จริง `video-postproduction` และไม่ทำงาน
ใน test/build ปกติ ต้องใช้ Docker Compose `2.24.4` ขึ้นไป มิฉะนั้นจะแจ้งข้าม
การตรวจ สคริปต์หยุด container แต่ไม่สั่ง `down` และไม่ลบ named volumes ของ smoke
project; หากพบสถานะบางส่วนจะหยุดโดยไม่กู้คืนหรือล้างข้อมูลอัตโนมัติ
และหาก project นี้ทำงานอยู่ก่อนแล้ว จะไม่หยุดบริการเดิม

## Flow ทดลอง

1. สร้าง project ที่หน้า New project
2. เลือกไฟล์ `mp4`, `mov`, `webm` หรือ `mkv`
3. รอ job เปลี่ยนจาก `queued` เป็น `running` และ `succeeded`
4. เปิด Project Editor เพื่อดู metadata, video stream และ transcript
5. ในส่วน `V1B first cut` กำหนด `Start`, `End` และ caption preset
6. กดบันทึก EDL และ render แล้วเปิด preview หรือ download เมื่อสถานะเป็น
   `succeeded`

Upload ถูกตรวจ MIME/นามสกุล, จำกัดขนาด, เขียนแบบ atomic และตรวจซ้ำด้วย
FFprobe ก่อนบันทึก asset ถ้า provider หรือ worker ล้มเหลว หน้า editor จะแสดง
stage และ safe error พร้อมปุ่ม retry

## ขอบเขต V1B

V1B render จาก EDL ที่ persist แล้ว รองรับ source range หนึ่งช่วง, caption จาก
normalized transcript (SRT/ASS), caption preset และ center crop 9:16 เป็น MP4
1080x1920 โดย FFmpeg/FFprobe รันด้วย typed argv และไม่รับ shell command จาก UI
หรือ provider การแก้ EDL จะสร้าง version ใหม่ caption จะถูก rebased ตาม source
range ของ output และการส่งงาน render เดิมซ้ำจะไม่สร้างไฟล์ซ้ำ render output จะถูก
เก็บแบบ atomic พร้อมสถานะที่ตรวจสอบได้ผ่าน API

ยังไม่เปิดใช้งาน active speaker, color operation, B-roll, asset search,
generated media หรือ automatic highlight selection; EDL ที่ร้องขอ capability
เหล่านี้จะถูกปฏิเสธอย่างชัดเจน

## Provider และ secrets

คัดลอก `services/video-postproduction-api/.env.example` เป็นไฟล์ env สำหรับ
local override เท่านั้น ห้าม commit password, API key, bearer token หรือ
private endpoint ลง Git

ค่าเริ่มต้นใช้ `faster_whisper` แบบ CPU; image จะติดตั้ง package optional และ
ดาวน์โหลด model ครั้งแรกลง `whisper_cache` จึงอาจใช้เวลานานและพื้นที่มาก
runtime pin `av>=11,<19` เพื่อรองรับ audio decoder ของ `faster-whisper`;
ซับ ASS ไทยตัดบรรทัดด้วย PyThaiNLP `newmm` และ dictionary ที่ติดมากับ package
ไม่ดาวน์โหลด tokenizer model ถ้าตัดคำล้มเหลวหรือทำตัวอักษรหาย จะเตือน
`caption_tokenization_fallback` โดยไม่ log ข้อความ แล้วใช้ safe character split
สำหรับคำยาวเกินบรรทัดหรือ fallback โดยเก็บตัวอักษรและช่องว่างครบ
หาก source เป็นภาษาไทยเป็นหลัก ให้ตั้ง `TRANSCRIPTION_LANGUAGE=th` เพื่อส่ง
language hint เข้า provider; เว้นว่างไว้เพื่อให้ provider ตรวจภาษาเอง
การตรวจภาษาอัตโนมัติอาจผิดเมื่อช่วงต้นคลิปไม่มีเสียงพูด: W1 พบคลิปไทยจริง
ที่ตรวจเป็น `nn` ทั้งคลิป จึงรันทดสอบใน runtime แยกด้วย hint `th` ที่มีอยู่แล้ว
โดยไม่เปลี่ยนค่าเริ่มต้นของระบบ การจบ job สำเร็จไม่รับรองความแม่นยำของภาษา
หรือเนื้อหา ต้องฟังเทียบต้นฉบับก่อนใช้จริง รายละเอียดและ ticket อยู่ใน
แผน V1C (Solo Empire `docs/superpowers/plans/2026-09-29-video-v1c-production-readiness.md`)
สามารถเปลี่ยนเป็น OpenAI-compatible endpoint ได้ด้วย:

```text
TRANSCRIPTION_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=https://your-compatible-gateway.example/v1
OPENAI_COMPATIBLE_TRANSCRIPTION_MODEL=your-model
```

การส่ง authorization ให้ gateway ต้องเพิ่มผ่าน deployment secret/transport
ของ environment จริง; ค่า secret ไม่ถูกเก็บใน database, transcript, EDL หรือ
structured logs ของ engine

### Real Thai transcription smoke test

ชุดทดสอบปกติใช้ deterministic provider และจะไม่ดาวน์โหลด model หรือส่ง media
ออกนอกเครื่อง หากมี model cache และไฟล์วิดีโอภาษาไทยจริง ให้เปิดการตรวจแบบ
opt-in ใน PowerShell:

```powershell
$env:VIDEO_POSTPRODUCTION_REAL_TRANSCRIPTION = "1"
$env:VIDEO_POSTPRODUCTION_THAI_SAMPLE_VIDEO = "C:\path\to\thai-sample.mp4"
$env:TRANSCRIPTION_LANGUAGE = "th"
python -m pytest services/video-postproduction-api/tests/e2e/test_real_thai_transcription.py -q
```

การตรวจนี้ใช้ `faster-whisper` จริง, persist transcript ภาษาไทย, สร้าง EDL และ
render captioned MP4 ด้วย FFmpeg/FFprobe; ถ้าไม่ได้ตั้ง opt-in test จะถูก skip
โดยตั้งใจ

### Thai caption golden และ W1 human gates

หลัง build image ในเครื่องแล้ว ตรวจฟอนต์ไทยและ golden frame 6 กรณีโดยไม่ใช้ network:

```powershell
$env:VIDEO_POSTPRODUCTION_GOLDEN_IMAGE = "video-postproduction-api:local"
python -X utf8 -m pytest services/video-postproduction-api/tests/e2e/test_thai_caption_golden.py -q
```

test ไม่ build/pull image และไม่ดาวน์โหลดโมเดล; ถ้าไม่ระบุ image จะ skip
ตรวจทั้ง 1080×1920 และ 1920×1080 ด้วย FFmpeg จริง พร้อม exact line boundaries,
glyph visibility และ frame margins หลักฐาน W1, hash ตัวติดตั้ง และ checklist
สำหรับ Windows user ใหม่อยู่ใน
แผน V1C (Solo Empire `docs/superpowers/plans/2026-09-29-video-v1c-production-readiness.md`)
คลิปสังเคราะห์/เสียง tone ไม่รับรองความแม่นยำภาษาไทยหรือ timing ของประชุมจริง;
owner review และ fresh install ยังต้องผ่านก่อนเปลี่ยนเป็น `pilot-ready`
รอบ W1 เพิ่มเติมถอดคลิปไทยจริง **38m 12s** ครบทั้ง `small` และ `medium`
ด้วย `TRANSCRIPTION_LANGUAGE=th` โดยไม่มี timeout และเตรียมคลิปจริง 3 ช่วง
ทั้ง 9:16 และ 16:9 ไว้ที่ `.runtime/video-w1/owner-review-thai/` พร้อม manifest
และซับในเครื่องเท่านั้น ผลนี้ยังไม่ครอบคลุมคลิป 60 นาที และยังไม่ได้รับคะแนน
จากเจ้าของเรื่องความแม่นยำ อ่านง่าย หรือ timing ห้ามนำ media/transcript เข้า Git

## หลักฐานการตรวจสอบล่าสุด (2026-09-23)

- API `python -m pytest -ra`: **58 passed, 1 skipped**; skipped คือ real Thai
  transcription ที่ต้อง opt-in มี Alembic `path_separator` deprecation warning 1 รายการ
- Compose `python -m pytest -q`: **2 passed**; `docker compose ... config --quiet`: **ผ่าน**
- `docker compose ... up -d --build api worker web`: **ผ่าน**; PostgreSQL, Redis,
  API, worker และ web healthy โดย API/web เปิดพอร์ตบน `127.0.0.1` เท่านั้น
- Browser smoke ที่ `http://127.0.0.1:3400`: สร้างโปรเจกต์และอัปโหลดวิดีโอ MP4
  สังเคราะห์ 2 วินาที (320×180, 30,450 bytes); probe และ `faster_whisper small`
  สำเร็จ งานจบที่ **Succeeded 100%** และได้ transcript “BEEP BEEP” จากเสียงทดสอบ
  โมเดลถูกเก็บใน volume ราว 464 MB การทดสอบนี้ยังไม่ครอบคลุม render/preview/download
- Docker build ของเว็บผ่าน Next.js production build และ TypeScript check

## หลักฐานก่อนหน้า (2026-09-22)

- `ruff check services/video-postproduction-api/video_postproduction services/video-postproduction-api/tests`: **ผ่าน**
- `mypy services/video-postproduction-api/video_postproduction`: พบ 5 baseline
  errors ในไฟล์เดิมของ storage/jobs/routes
- frontend `npm test -- --run`: **2 passed**, `npm run typecheck`: **ผ่าน**,
  `npm run build`: **ผ่าน**; browser smoke ตอนนั้นตรวจเฉพาะ UI
- `alembic -c services/video-postproduction-api/alembic.ini upgrade head`: **ผ่าน**
- `python -X utf8 infra/tests/quality/smoke_test.py`: **18 passed**,
  `python infra/tests/integrations/calendar_task_sync_test.py`: **24 passed**,
  และ module boundary guard: **0 errors, 0 warnings**
- `task typecheck`: **ผ่าน**; `task test` ติดข้อจำกัด symlink/path บน Windows และ
  `task docs:check` พบ task parity ที่ยังไม่ map `package:browser-extension`

## Pipeline tools T6.2 (deterministic)

นอกจาก runtime Compose ด้านบน บริการนี้ยังมี package `video_postpipeline`
สำหรับงาน pipeline แบบ deterministic (roadmap T6.2) ซึ่งส่งงานต่อกันผ่าน
versioned artifact contract โดยไม่ย้ายความเป็นเจ้าของ code:

- `transcript-artifact.v1` — contract ของ artifact ถอดเสียง (JSON Schema ที่
  `schemas/transcript-artifact.v1.schema.json` พร้อม validator ทั้ง JSON Schema
  และ pydantic) ห้ามมี absolute local path ใน artifact ที่เผยแพร่ (`source_name`
  ต้องเป็นชื่อไฟล์ล้วน)
- `python -m video_postpipeline.cli subtitles` — แปลง artifact เป็น SRT/VTT
  timestamp ตรงรูปแบบ `HH:MM:SS,mmm` (SRT) หรือ `HH:MM:SS.mmm` (VTT) และตัดบรรทัด
  cue ที่ 42 อักขระแบบ greedy word wrap (คำยาวไม่มีช่องว่าง เช่น ภาษาไทย ถูกตัด
  ทุก 42 อักขระพอดี)
- `python -m video_postpipeline.cli extract-audio --format wav|mp3` — ดึงเสียง
  ด้วย FFmpeg (typed argv, shell=False, timeout มีขอบเขต) `wav` ผลิต WAV PCM
  24-bit พร้อม manifest `audio-batch.v1.json` ที่ตรวจกับ schema copy ของ
  `book-audio-production` (เจ้าของเดิมคือ nested repo บริการนี้ consume เท่านั้น)
  ส่วน `mp3` เป็น preview เท่านั้นและไม่ออก manifest เพราะ audio batch contract
  รับเฉพาะ `flac` และ `wav_pcm24`
- `python -m video_postpipeline.cli replace-audio --video V --audio M.wav --output-dir D`
  — แทนเสียงของวิดีโอด้วยเสียงที่ master แล้ว (copy ภาพเดิม, เข้ารหัสเสียงเป็น AAC,
  ยึดความยาววิดีโอ: เสียงสั้นกว่าเติมความเงียบ ยาวกว่าตัดทิ้ง) ตรวจผลด้วย ffprobe
  (ภาพ 1 stream, เสียง AAC 1 stream, ความยาวเท่าวิดีโอภายใน 0.5 วินาที) แล้วเขียน `video-remux.json` (`video-remux.v1`) ที่มี
  sha256 ของวิดีโอต้นทาง เสียงต้นทาง และไฟล์ผลลัพธ์ — Production Hub ใช้ผ่าน handoff
  `E1-master-to-video`
- `python -m video_postpipeline.cli overlay --video V --image logo.png --output-dir D`
  — วางภาพ (โลโก้, lower third) ลงบนวิดีโอ ย่อภาพตาม `--width-ratio` ของความกว้างวิดีโอ
  วางตาม `--position` (มุมทั้งสี่หรือ bottom-center) encode ภาพใหม่เป็น H.264 และ copy เสียงเดิม
  ตรวจ ffprobe แล้วเขียน `video-overlay.json` (`video-overlay.v1`) — Production Hub ใช้ผ่าน
  handoff `E3-graphic-to-video`
- `python -m video_postpipeline.cli transcript-request` — สร้าง payload
  `transcript-document-request.v1` สำหรับ `document-production` โดยตรวจกับ schema
  copy ที่ `services/document-production-api` เป็นเจ้าของ
- `python -m video_postpipeline.cli validate` — ตรวจ artifact กับ contract

ขอบเขต exit code: `0` สำเร็จ, `2` input/validation error, `4` media/tool error
(ffmpeg/ffprobe หาย ล้มเหลว หรือ timeout)

ทุกครั้งที่รัน `overlay` และ `replace-audio` (ทั้งตอนสำเร็จและตอนล้มเหลว)
เครื่องมือจะเขียนไฟล์ 3 ไฟล์ไว้ใน `--output-dir`:

- `trace.json` (`production-trace.v1`) มี hash และข้อมูล probe ของ input,
  ทุกขั้นตอนที่ทำ (option ของ FFmpeg แบบไม่มี path), sha256 ของ artifact และผล QA
- `MANUAL-STEPS.md` บอกวิธีทำไฟล์เดียวกันเองใน DaVinci Resolve 19
  - overlay: บอกแทร็ก V2 พร้อมค่า Zoom/Position ที่ตรงกับ `scale` + `overlay` ของ FFmpeg
  - replace-audio: บอกวิธี unlink ภาพกับเสียง, ลบเสียงเดิม, วางเสียง master ที่
    A1 และตัดเสียงที่ timecode จบวิดีโอ
- `history.jsonl` เพิ่มหนึ่งบรรทัดต่อการรันหนึ่งครั้ง

ใช้ `python -m production_trace verify --root <output-dir> <job_id>` เพื่อตรวจ
artifact ซ้ำ ถ้า interpreter ไม่มี `production_trace` (`packages/production-trace`)
เครื่องมือจะทำงานต่อตามปกติแต่ข้ามการเขียน trace

การสรุปความ (semantic summarization) ต้องใช้ AI และถูกเลื่อนไป T8 / Phase 4 ตาม
การตัดสินใจของ roadmap เอกสารที่ได้จาก pipeline นี้จึงเป็นบทถอดเสียง verbatim
แบบมี timestamp เท่านั้น ไม่ใช่บทสรุปจาก AI

รันทดสอบของ package นี้จาก directory บริการ: `python -m pytest tests`
