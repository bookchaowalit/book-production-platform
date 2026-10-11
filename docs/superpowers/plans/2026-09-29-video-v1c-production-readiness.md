# Video V1C — Long Clips, Thai Captions, and Installer Readiness

**สถานะ:** W1-machine-work-complete; W0 closed; human gates remain open

**Spec:** [Production Tools SDD](../specs/2026-09-29-production-tools-spec-driven-development-design.md)  
**Owning code:** `services/video-postproduction-api` and `apps/video-postproduction-desktop`

## Goal

Close the remaining deterministic Video V1C gates with repeatable local evidence:
60-minute transcription behavior, Thai word-boundary caption wrapping, packaged
runtime contents, and a fresh Windows installation smoke test.

## Decisions and limits

- **D1 partially supplied; 60-minute gate remains open:** the owner provided
  an authorized local video on 2026-09-30. `ffprobe` reports **2,292.466667
  seconds (38m 12s)**, H.264 1280x720 and AAC 48 kHz stereo. Both qualities
  completed on the unchanged v0.1.4 candidate with the existing documented
  `TRANSCRIPTION_LANGUAGE=th` hint. This is real-input evidence, not a
  60-minute test or proof of transcription accuracy. Owner listening/scoring
  remains pending. The earlier 30-minute English synthetic fixture remains
  performance-only; do not repeat, concatenate or extrapolate either source
  to claim the 60-minute requirement.
- **D4:** Owner selected PyThaiNLP `newmm` for deterministic local Thai word
  boundaries. Do not download a tokenizer model. Measure and record the Docker
  image-size delta before packaging; obtain owner review before packaging if
  the measured increase exceeds 100 MB (100,000,000 bytes) or a model download
  is required.
- No paid provider, external transcription service, new model download,
  production deployment, or change to unrelated working-tree files is allowed.
- Per-project `fast`/`accurate` controls apply only to `faster_whisper`.
  `openai_compatible` keeps its configured model; its capability response hides
  the selector and the API rejects explicit quality overrides. Capability
  lookup failures keep project creation disabled and provide a retry action.
- Do not begin W1 implementation until W0 T0.1–T0.5 is closed. In particular,
  the provider-specific quality contract must be covered by API and browser tests
  before relying on the `fast`/`accurate` comparison.

## Tasks

### T1.0 — Installer v0.1.3 hotfix before long-clip work

- [x] Bump desktop/package lock/launcher to `0.1.3` and add release notes.
- [x] Stage the current `av>=11,<19` dependency and reject a missing pin before
  replacing an existing runtime; add regression tests.
- [x] Include production web `lib/` helpers, excluding tests. Cold smoke exposed
  missing `output-format.ts` and `transcript-range.ts` in the previous bundle.
- [x] Run desktop tests, typecheck, launcher smoke, and runtime smoke.
- [x] Build/verify the x64 installer and preserve it locally.
- [x] Build the staged API with Compose `build --no-cache`; verify PyAV 18.x
  and transcribe an exactly 75-second synthetic upload.
- [x] Update the catalog without closing the human gate.

**Requirement:** REQ-VID-05  
**Evidence (2026-09-30):** desktop **107 passed**, typecheck/launcher smoke
passed; runtime smoke started/stopped twice and preserved four named volumes.
Installer SHA-256:
`ca96fe5142d561d30a766a9cbbdd2fddc84ac56e0eb7b6ba1976e58803d8c1c0`.
Archived locally under `.runtime/video-w1/installer-0.1.3/`.
The no-cache API image is
`sha256:23ca1c8f7459c61731937b7cc8fbfb6d83f1b2e5d61860909485a684cb5a4a35`
(1,615,469,886 bytes), with PyAV `18.1.0`. The 75-second upload succeeded with
`faster_whisper/small`; transcription stage wall time **36.824 seconds**,
sampled peak worker RAM **1.865 GiB**, no timeout. Models were read from the
existing cache read-only with `HF_HUB_OFFLINE=1`; no model was downloaded.
All uploads used the separate `video-postproduction-w1-20260930` test project,
not the live project's database or media volumes.

The cold web build initially failed on missing helpers; this was fixed before
the verified installer was rebuilt. Docker `info` also hit its existing
30-second preflight limit during concurrent cold builds. The runtime smoke
passed from a stopped test stack after builds completed; no production service
was restarted and no smoke volume was deleted.

### T1.1 — Prepare the long-clip fixture

- [x] Generate the 30-minute fixture under `.runtime/video-w1/`.
- [x] Verify it is ignored by Git and probe its duration and audio format.
- [x] Record that the fixture is synthetic and performance-only in test evidence.
- [x] Convert to an upload-compatible MP4 and verify an exact 1,800-second
  duration with AAC audio at 16 kHz mono and H.264 video.

**Requirement:** REQ-VID-02  
**Verification:** `ffprobe` reports 1,800 seconds, 16 kHz, mono PCM; `git check-ignore`
matches the fixture path.
The WAV and MP4 probes both report `1800.000000` seconds. They are ignored
under `.runtime/video-w1/`; neither is a real Thai meeting or 60-minute proof.

### T1.2 — Measure both transcription qualities

- [x] Confirm the local runtime and both required Whisper models are already
  available; if not, stop rather than download or install them.
- [x] Run the identical fixture through `fast` and `accurate`; record input
  duration, wall time, peak RAM, selected provider/model, and any timeout.
- [x] Keep each run's transcript and media local; do not put raw content in
  logs, telemetry, Git, or the evidence summary.

**Requirements:** REQ-VID-01, REQ-VID-02  
**Acceptance:** AC-VID-02a, plus proof that the selected model follows the
requested quality for the configured provider.

**Measured evidence (2026-09-30), AC-VID-02a at 30 minutes only:**

| Fixture duration | Quality | Provider/model | Transcription stage wall time | Sampled peak worker RAM | Timeout |
|---|---|---|---|---|---|
| 1,800 seconds, synthetic English | fast | `faster_whisper/small` | 828.356 seconds (13m 48s) | 3.299 GiB | No |
| 1,800 seconds, synthetic English | accurate | `faster_whisper/medium` | 1,785.917 seconds (29m 46s) | 9.327 GiB | No |

Wall time is the Docker-timestamped worker `stage_started` → `stage_finished`
interval for the transcription stage, not upload/probe/queue time. Worker RAM
was sampled every five seconds with `docker stats`; this is a sampled peak,
not a continuous maximum. The Docker memory limit was 31.26 GiB. Tests ran on
a shared host alongside existing workloads; the same worker retained the small
model when medium was loaded. These are observed local results, not a latency
guarantee or an isolated-machine comparison.
Both transcript responses confirmed the selected provider/model and an
1,800-second duration. Raw transcripts remain only in the isolated local
runtime's storage/database; the evidence contains metadata only. Existing
small/medium model snapshots were reused read-only with network access to
Hugging Face disabled. The following real-input follow-up extends duration
coverage, but does not replace the 60-minute or owner-quality gates.

**Real owner-provided input (2026-09-30), AC-VID-02a at 38m 12s only:**

| Input duration | Quality | Provider/model | Transcription stage wall time | Sampled peak worker RAM | Timeout |
|---|---|---|---|---|---|
| 2,292.466667 seconds, real Thai input | fast | `faster_whisper/small` | 2,935.163 seconds (48m 55s) | 2.744 GiB | No |
| 2,292.466667 seconds, real Thai input | accurate | `faster_whisper/medium` | 6,405.802 seconds (1h 46m 46s) | 12.190 GiB | No |

Both runs used the complete original upload, cached models mounted read-only,
`HF_HUB_OFFLINE=1`, CPU execution and the existing **explicit Thai hint**.
Returned language was `th`; 211/212 small and 252/257 medium segments
contained Thai characters. These counts check output language only, not
accuracy. Stage timing again uses worker lifecycle events, excluding upload,
probe and queue time. The sampler targeted five-second intervals and saved
589 fast / 1,283 accurate samples; largest observed gaps were 12.620 / 7.637
seconds respectively. Peaks are sampled, not continuous maxima. Medium ran
on the same worker after small; retained model memory and shared-host load
prevent a clean per-model memory or latency comparison.
Metadata and durable samples are local under `.runtime/video-w1/`; source,
transcripts, ASS and review media remain ignored and are not included here.
**REQ-VID-02 at 60 minutes and real Thai accuracy remain unverified.**

**Rejected automatic-language baseline and separate follow-up:** with no
language hint, the initial small run completed in 4,719.162 seconds but
reported `nn` and only 1/97 Thai-bearing segments. A local, network-disabled
VAD/language probe found no speech in the first 30 seconds; three later
30-second voiced windows detected `th` with probabilities 0.949, 0.973 and
0.983. The original automatic medium experiment was deliberately stopped,
not timed out, before starting the separate known-Thai runtime. Automatic
detection is therefore **not accepted** for this source. The existing hint
is a scoped workaround, not a change to generic defaults or an automatic
language fix; track the opening-silence regression separately in
[#95](https://github.com/bookchaowalit/solo-empire/issues/95).

### T1.3 — Fix only demonstrated timeout failures

- [x] Check whether either measured run times out before considering a
  duration-aware policy and regression tests for short, long, and capped inputs.
- [x] If neither run times out, record the result and make no timeout change.

**Requirement:** REQ-VID-02
**Outcome:** both 30-minute synthetic runs and both complete 38m 12s
known-Thai runs succeeded without stage timeout. The conditional timeout
implementation was **not needed and not made**; do not infer that the
existing policy is validated at 60 minutes.

### T1.4 — Add Thai word-boundary wrapping

- [x] Add failing tests first for Thai leading vowels, combining marks,
  punctuation, long words, and unspaced text.
- [x] Add PyThaiNLP `newmm` in `captions/ass.py`; preserve every source
  character and retain safe fallback behavior for overlong tokens.
- [x] Add the runtime dependency and build the image locally; record the
  image-size delta and stop for owner review before packaging if the increase
  is not acceptable or requires a model download.
- [x] Render golden ASS frames and verify no line splits a token or loses
  combining marks.

**Requirement:** REQ-VID-03  
**Acceptance:** AC-VID-03a  
**Expected files:** `services/video-postproduction-api/pyproject.toml`,
`services/video-postproduction-api/Dockerfile`,
`services/video-postproduction-api/video_postproduction/captions/ass.py`,
`services/video-postproduction-api/tests/test_captions.py`.

**Evidence (2026-09-30):** tests first reproduced six failures, then passed
after `newmm` wrapping and character-preserving packing. PyThaiNLP `5.3.8`
uses its bundled dictionary without model/network access. The candidate image
is 1,703,231,792 bytes, an increase of **87,761,906 bytes (87.762 MB / 83.696 MiB)**
over the no-cache hotfix image, below the 100 MB limit. Six real FFmpeg golden
frames verify exact line layouts, Thai font availability, visible glyphs and
safe frame margins. The golden test runs with
`VIDEO_POSTPRODUCTION_GOLDEN_IMAGE=video-postproduction-api:w1-newmm`;
without an explicit local image it is deliberately skipped.
Fallback emits `caption_tokenization_fallback` without raw text or exception
payload; no character or whitespace is silently discarded.

### T1.5 — Review portrait and landscape clips

- [x] Render three short clips in 9:16 and 16:9 using the available authorized
  or synthetic fixture.
- [ ] Have the owner review the actual rendered frames and audio; record
  readability, timing, and any changes requested.

**Requirements:** REQ-VID-03, REQ-VID-05
The six 3-second synthetic clips, ASS files, PNG frames and hash manifest are
local under `.runtime/video-w1/caption-review/`. Audio is a **440 Hz tone, not
speech**: these establish rendering only and cannot establish real-meeting
caption timing or transcription accuracy. Owner review remains pending.

**Real-source review pack (2026-09-30):** six additional clips rendered
through persisted EDL versions and the worker/API on the unchanged candidate,
using the completed `accurate/medium` transcript with the explicit Thai hint.
Each range has both 1080x1920 (9:16 center crop) and 1920x1080 (16:9 full
source frame) outputs:

| Case | Source range, seconds | Clip duration | Caption preset |
|---|---|---|---|
| 1 | 431.240-446.240 | 15 seconds | `minimal` |
| 2 | 1,128.140-1,148.140 | 20 seconds | `bold` |
| 3 | 1,843.500-1,863.500 | 20 seconds | `emphasis` |

Clips, active-caption PNG frames, ASS files, transcript responses and a
SHA-256 manifest are **local only** under
`.runtime/video-w1/owner-review-thai/`. All six clips passed full FFmpeg
decode, FFprobe dimensions/duration checks, AAC 48 kHz stereo presence,
hash checks, and API preview byte-range delivery. Frame times were verified
against actual ASS dialogue intervals. This establishes render/preview
integrity, **not** listening, readability, transcription accuracy or
subtitle/speech synchronization approval.
The owner must still watch and listen to the six files and score readability,
timing and Thai wrapping. Record any further defects as separate tickets;
do not mark T1.5 passed based only on these automated checks.

### T1.6 — Build and inspect the desktop installer

- [x] Build with the desktop package's existing `make` workflow.
- [x] Verify the staged runtime includes the current API Dockerfile, Thai font,
  and the required API/runtime files.
- [x] Run artifact verification and record installer version and hashes.

**Requirement:** REQ-VID-05
**Candidate, not approved release (2026-09-30):** installer `0.1.4` is built and
verified, with **109 desktop tests** passing, typecheck and launcher smoke.
The newmm candidate also passed runtime smoke twice with four preserved
named volumes.
All **68 runtime files** were checked against the manifest both in Electron's
packaged resources and inside the delivered `.nupkg`. The bundle contains
`av>=11,<19`, `pythainlp>=5.1,<6`, the Dockerfile's `fonts-noto-core`, migrations
`0001`–`0003`, and the current web helpers. Installer SHA-256:
`4138a3caf8e4aea192e7e3739508b705ad1fc0c644eeaedef4fc590398003941`.
Package SHA-256:
`0775cc545b49fded5f191b913b07cb8363e1e33ea55721fe0b9d7bb42bf0cc50`.
Runtime manifest SHA-256:
`e69487fb606bc7884125194b4d6da576fc79d3fa399a61c0f151af1eb610dd49`.
Archived locally under `.runtime/video-w1/installer-0.1.4/`.
T1.5 owner feedback could require rebuilding this candidate; T1.7 is not passed.

### T1.7 — Fresh-install human gate

- [ ] Run AC-VID-05a from a clean Windows user profile with an authorized local
  test input.
- [ ] Verify install, first launch, upload, transcription, and render without
  using an existing configured profile.
- [ ] Change the catalog gate to `pilot-ready` only after the human smoke passes.

**Requirement:** REQ-VID-05  
**Acceptance:** AC-VID-05a (human gate)

## Verification and handoff

- API: `python -m pytest services/video-postproduction-api/tests -q` and the
  API's configured Ruff check.
- Desktop: `npm test`, `npm run typecheck`, `npm run smoke:launcher`,
  `npm run smoke:runtime`, `npm run make`, and `npm run verify:artifacts`.
- Root documentation/catalog: `task docs:check` and `task production-tools:check`.
- Record each result against its REQ/AC ID. Keep raw media, transcript, and
  personal paths out of the evidence. Do not commit or push until the owner
  explicitly authorizes delivery and the scoped diff is reviewed.

**Initial W1 integration verification (2026-09-30):** API **94 passed, 1 skipped**
(the real-Thai transcription opt-in), with six real golden frame renders;
Ruff and focused caption Mypy pass. Three existing Alembic `path_separator`
deprecation warnings remain. Root `task test`, `task typecheck`,
`task docs:catalog:check` and `task production-tools:check` pass.
Root `task docs:check` remains blocked at `entity-graph:check` by two missing
active Git-commit entities (`82578efc...`, `b161c37c...`) outside this scope.
No entity-graph/database regeneration was performed to hide this blocker.
Launcher edits only change the displayed release version. Existing design-hook
contrast warnings were left unchanged and no design-rule ignore was added.
Scoped code/artifact verification passes, but no push or production deployment
was initiated while the root documentation gate remains blocked.

**Real-input follow-up:** both full-input transcriptions and all six real
review artifact checks passed as described above. No product code,
dependency, generic language default or timeout policy changed. The retained
v0.1.4 Setup.exe and `.nupkg` hashes still match T1.6; no installer rebuild
was needed for the documented runtime hint. Scope remains local verification
and evidence; owner review and fresh-install approval have not been inferred.
`task production-tools:write`, `task production-tools:check` and the scoped
`git diff --check` pass. The latest root `task docs:check` stops earlier than
the initial entity-graph failure, at `boundaries:check`: concurrent
`book-job-data` changes introduce `src/book_job_data/privacy.py` and
`tests/test_privacy_and_api.py` without a declared runtime ownership boundary.
These unrelated sources/registry changes were neither edited nor staged by
this work. A separate document-catalog check also reported pre-existing
generated catalog drift during the real-input run; no broad regeneration
was used to conceal it. Follow-up evidence changes remain uncommitted and
were not pushed or deployed.

## Fresh-install checklist ภาษาไทย — AC-VID-05a

- [ ] เจ้าของสร้าง Windows user ใหม่ที่ยังไม่มี Video Studio configuration
  แล้วติดตั้ง/เปิด Docker Desktop Linux engine; ห้ามใช้ profile เดิมแทน
- [ ] ตรวจ version และ SHA-256 ของ installer เทียบหลักฐานก่อนติดตั้ง
- [ ] ติดตั้งแบบต่อผู้ใช้ เปิดจาก Start Menu และบันทึกผล first-build/first-launch
- [ ] อัปโหลดวิดีโอที่ได้รับอนุญาต รอ probe/ถอดเสียงสำเร็จ และยืนยัน model/quality
- [ ] Render ทั้ง 9:16 และ 16:9 เปิด preview/download และฟังเสียงจริง
- [ ] ให้คะแนนอ่านง่าย, timing และการตัดคำไทย พร้อมระบุข้อผิดพลาดเป็น ticket ย่อย
- [ ] ปิดแล้วเปิดแอปใหม่ ยืนยัน project/media ยังอยู่; ไม่ลบ named volumes
- [ ] บันทึกวันทดสอบ, installer version/hash และผลผ่าน/ไม่ผ่านโดยไม่มี transcript
- [ ] เปลี่ยน catalog เป็น `pilot-ready` เฉพาะเมื่อมีหลักฐาน owner gate ผ่านจริง

**ยังไม่ผ่าน:** D1 (ประชุมไทยจริง 60 นาที), T1.5 owner review และ T1.7 fresh install.
Synthetic performance/golden tests ไม่ทดแทน gate เหล่านี้ และไม่มี production deploy
target สำหรับงานนี้ (`deployment_skipped_no_target`).
W3 atomic budget reservation and deleting the four smoke projects in the live
database were not mixed into W1. The live cleanup needs exact project IDs and
explicit production-data approval; this work used only its own isolated
test database/volumes and did not delete live records.
