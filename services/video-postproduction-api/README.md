# Video Post-production API

Python package `video_postproduction` (FastAPI, worker stages, EDL, render).
The runtime guide (Docker Compose, providers, secrets, smoke tests) lives in
[`services/video-postproduction/README.md`](../video-postproduction/README.md);
the EDL contract lives in
[`packages/video-postproduction-edl`](../../packages/video-postproduction-edl/README.md).

## Job history, trace & manual steps

Every render job (`RenderService`) and every export job (`POST
/api/v1/projects/{project_id}/exports`) writes one job directory in storage.
Failed jobs write one too.

```text
projects/<project_id>/jobs/history.jsonl        one production-history.v1 line per job
projects/<project_id>/jobs/render-<render_id>/  (or export-<export_id>/)
  trace.json        production-trace.v1
  MANUAL-STEPS.md   rebuild the same edit by hand in DaVinci Resolve 19 (free)
  edl.json          the exact EDL the engine executed (job IR; hash in trace.ir)
  timeline.edl      CMX3600 cut list
  timeline.fcpxml   FCPXML 1.10 (cuts + 9:16 zoom + segment markers)
  timeline.otio     OpenTimelineIO JSON
  captions.srt      editor captions: same cues and line breaks as the burn-in
  captions-burned.ass|srt, render.mp4   render jobs only
```

`trace.json` records the following:

- **Inputs.** Source file name, size, sha256 (from upload), duration, fps,
  resolution, and codecs. It also records the transcript hash.
- **Steps.** `validate`, `probe` (ingest probe), `highlight_proposal` (when
  the EDL came from a confirmed AI proposal: route, fingerprint, and
  segment-id ranges), `plan` (every clip's source/record in/out in seconds
  and frames, the reframe filters, and the Resolve zoom), `transcribe`
  (provider, model, language, quality, segment count, and fingerprint),
  `captions`, `interchange`, `render` (FFmpeg options and filters
  summarized structurally, with no paths), `qa`, and `publish`.
- **Artifacts.** Every artifact with its sha256.
- **Outcome.** The QA result, and the failure code when the job failed.

It never contains transcript or caption text. That text lives only in the
caption files and in `MANUAL-STEPS.md`, next to the video it describes.
Tests use a canary to check this. The render response
`metadata.trace` gives the storage keys of the job directory, trace,
manual, and history.

```bash
ROOT=services/video-postproduction-api/.runtime/storage/projects/<project_id>/jobs
python -m production_trace history --root "$ROOT"
python -m production_trace show    --root "$ROOT" <render_id>
python -m production_trace manual  --root "$ROOT" <render_id>
python -m production_trace verify  --root "$ROOT" <render_id>   # re-hash artifacts
```

API: `GET /api/v1/projects/{id}/history` returns the metadata history,
newest first. `GET /api/v1/projects/{id}/jobs/{render-…|export-…}/files/{name}`
downloads one file. Only the files listed above are allowed.

`MANUAL-STEPS.md` comes from the same `TimelinePlan` the renderer uses
(`video_postproduction/timeline.py`). It covers these steps:

1. **Project Settings.** Timeline resolution (for example 1080 x 1920) and
   the frame rate (the source fps from probe).
2. **Media Pool and New Timeline.** Import the source and create a timeline
   with Start Timecode `00:00:00:00`.
3. **Blade and Ripple Delete.** Razor (Ctrl+B) at every cut, shown as
   `HH:MM:SS:FF (= HH:MM:SS.mmm)`. Then use Ripple Delete
   (Shift+Backspace) on each removed range, last first. Out-of-order clips
   use Source Viewer I/O + Append at End instead, with the inclusive Out
   frame.
4. **Check the clips.** The resulting clip order with record in/out.
5. **Reframe.** Inspector ▸ Transform Zoom/Position that matches the FFmpeg
   center crop.
6. **Captions.** File ▸ Import ▸ Subtitle `captions.srt` onto ST1, with the
   ASS style values (font, size, outline, shadow, margins, and the wrap
   limit). It also lists every cue.
7. **Audio.** Source audio on A1, with a Normalize Audio target when the EDL
   asks for one.
8. **Deliver.** H.264 MP4, the resolution, the fps, `Restrict to` the
   engine's measured bitrate, AAC, and Burn into video.

A glossary maps each Resolve action to Premiere Pro and CapCut.

## Open in DaVinci Resolve / Premiere

| Editor | File | How |
|---|---|---|
| DaVinci Resolve 19 | `timeline.fcpxml` | Import the source into the Media Pool first. Then use `File ▸ Import ▸ Timeline…` and clear *Automatically import source clips into media pool*. You get the cuts, the 9:16 Zoom, and one marker per EDL segment. |
| DaVinci Resolve 19 | `timeline.edl` | `File ▸ Import ▸ Timeline…`, then set the frame rate. Clips match by `FROM CLIP NAME`. This file holds only the cuts, so do the reframe, captions, and Deliver steps from `MANUAL-STEPS.md` afterwards. |
| DaVinci Resolve 18.5+ / OTIO tools | `timeline.otio` | `File ▸ Import ▸ Timeline…` |
| Premiere Pro | `timeline.edl` | `File ▸ Import…`. FCPXML needs a converter to FCP 7 XML first. |
| Any editor | `captions.srt` | Import onto a subtitle or captions track at `00:00:00:00`. |

If the source shows offline, use `Media Pool ▸ Relink Selected Clips…`.
Source timecode assumes the file starts at `00:00:00:00`, which is true for
most phone and screen recordings.

The export route works for timelines the engine cannot render yet (several
confirmed highlight clips). When a render fails, the job directory still has
the timelines and the manual guide.

**Not verified:** these files have not yet been opened in a real Resolve or
Premiere install. See [`docs/PARITY.md`](docs/PARITY.md) for the capability
comparison with DaVinci Resolve, Premiere Pro, and CapCut, and for the
prioritized roadmap.

EDL audio operation (optional, renderer-supported):

```json
{"audio": {"operations": [{"type": "loudness_normalize", "integrated_lufs": -14, "true_peak_dbtp": -1, "loudness_range_lu": 11}]}}
```

This runs a single-pass FFmpeg `loudnorm`, then `-ar 48000`. Accepted
ranges: I from -31 to -5, TP from -9 to 0, LRA from 1 to 20. The renderer
rejects any other audio operation before FFmpeg starts.

`production_trace` (`packages/production-trace`, stdlib only) must be
installed next to this package. For tests, `tests/conftest.py` adds it to
`sys.path`.

## AI highlight proposals (V2)

Spec: card 5.1 `REQ-VID-04` / `AC-VID-04a` in the
Production Tools SDD program (Solo Empire `docs/superpowers/specs/2026-09-29-production-tools-spec-driven-development-design.md`),
built on the shared [brief adapter kit](../../packages/brief-adapter-kit/README.md).
Status: **shadow only**. Nothing is wired into the API, worker, or web app yet.

The model only chooses **segment-id ranges** and a short title. It never sends
timestamps, never builds an EDL, and never renders.

| Step | Owner | Behaviour |
|---|---|---|
| Constraints | engine (`highlights/brief.py`) | Reads the clip count, the duration range, and whether a topic is named from a Thai or English brief (for example `3 clips of 30–60s about pricing` or `ขอ 3 คลิป ยาว 30-60 วินาที เรื่องราคา`). If any of them is missing, the result is `needs_input` with `missing`. More than 10 clips, or clips outside 1–180 s, is rejected. |
| Privacy | engine | `privacy_class` is a required request field that must come from the project. `private`/`restricted` are never dispatched (`privacy_class_blocks_dispatch`). |
| Prompt | adapter (`highlights/provider.py`) | Compact `s<n>\|start\|end\|text` lines inside `<transcript>`, plus the brief inside `<brief>`. Real segment ids never reach the model. Instruction-like text in the transcript or brief abstains with zero calls. |
| Size | kit runner | Input ≤ 8,000 tokens per call. A longer transcript is **rejected** (`input_token_limit_exceeded`), never truncated or chunked. The user selects a shorter range instead. |
| Calls | kit runner | Budget is reserved before every dispatch, with at most 2 calls per job (one JSON repair) and the owner-approved rate card and caps. |
| Proposal | engine (`highlights/engine.py`) | Each id range becomes the exact `start` of its first segment and the exact `end` of its last segment, so clips are aligned to segment boundaries by construction. Unknown ids, reversed ranges, overlaps, more clips than requested, out-of-range durations, and any model-supplied timestamp key are rejected. The output is an `EDLProposal` (`video-edl-proposal.v1`, `status=awaiting_confirmation`, `requires_confirmation=true`). |
| Confirmation | user | `build_confirmed_edl(proposal, confirmation=..., base_edl=...)` returns an `EDLDocument` only when `confirmed=true` and the confirmation fingerprint matches an untampered proposal. It does not persist or render. Saving and rendering stay with the existing EDL and render routes. |

The structured route (request `ranges` given as segment ids) skips the model
entirely (`model_calls=0`). It needs no provider configuration or adapter kit,
and works for private projects.

Transcript text and briefs appear only in the gateway request. They are never
written to logs, issues, the budget ledger, or the shadow report (canary
tests).

### Configuration

Copy `packages/brief-adapter-kit/examples/provider-config.example.json` to a
gitignored `.runtime/` path. Then set `schema_version` to
`video-highlight-provider-config.v1`, `tool_id` to `video-highlight-v2`, and
`route` to `video.highlight`, and add the owner's rate card, caps, and
approval. `max_token_budget` defaults to the kit ceiling (10,048). Keep
`mode=shadow` until a held-out shadow report passes and the owner approves
`live`.

### Commands

```bash
# Structured ranges or a live brief proposal (writes a proposal; never renders)
python -m video_postproduction.highlights.cli propose --request request.json --output proposal.json \
  [--provider-config .runtime/video-highlight-provider-config.json]

# EVAL-VID-highlight shadow run (metadata-only report; exit 1 when the gate fails)
python -m video_postproduction.highlights.cli brief-shadow \
  --provider-config .runtime/video-highlight-provider-config.json \
  --cases tests/fixtures/highlight_eval/eval-vid-highlight.json \
  --split tests/fixtures/highlight_eval/split.json --output .runtime/highlight-shadow.json
```

`propose` exits 2 with `provider_not_configured` (missing config, invalid
config, or the kit not installed) or `shadow_mode_only` (`mode != live`).

### Eval and gate

`tests/fixtures/highlight_eval/eval-vid-highlight.json` holds 30 synthetic
cases: 10 structured, 10 clear, 5 ambiguous, and 5 adversarial (transcript
prompt injection in Thai and English, too many clips, over-long clips, and an
oversized transcript). The suite is 15 Thai and 15 English, with at most a
one-case difference inside each category. Every case has a canary in its
transcript (and in its brief, if it has one). `split.json` names 9 dev cases;
the other 21 are held out.

The gate is computed on held-out cases only:

- boundary alignment = 100%, checked against the raw transcript independently of the engine
- duration in the requested range = 100%
- ambiguous → `needs_input` = 100%
- adversarial silent passes = 0
- structured `model_calls` = 0
- clear briefs proposed ≥ 95%
- ≤ 2 calls per job
- canary leaks = 0

Clear-brief exact range match is reported but not gated.

### Tests

```bash
uv venv .runtime/venv-video --python 3.12
uv pip install --python .runtime/venv-video/bin/python \
  -e "services/video-postproduction-api[dev]" -e packages/brief-adapter-kit
cd services/video-postproduction-api && ../../.runtime/venv-video/bin/python -m pytest tests/test_highlights.py
```

`brief_adapter_kit` is imported lazily, only by the highlight adapter, shadow,
and CLI modules. The API and worker import path does not depend on it, and
the Docker image does not include it yet.
