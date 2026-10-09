# Video Studio feature parity

Compared with DaVinci Resolve 19 (free), Adobe Premiere Pro, and CapCut Desktop.
Updated 2026-10-03. Status words:

- **have**: built and covered by tests in this repository.
- **partial**: some of it works; the gap is named in the row.
- **missing**: not built.

"Engine" means the API render path
(`video_postproduction/render`). "Pipeline" means the CLI tools in
`services/video-postproduction/video_postpipeline` (used by the Production Hub).

## What changed in this round

1. **Timeline interchange export (new, have).** Every render job and every
   `POST /projects/{id}/exports` job writes three timeline files from the EDL
   the engine used: `timeline.edl` (CMX3600), `timeline.fcpxml`
   (FCPXML 1.10), and `timeline.otio` (OpenTimelineIO). It also writes
   `captions.srt`, which has the same cues and line breaks as the burned-in
   captions. A person can continue the edit in Resolve or Premiere. This
   works even for timelines the engine cannot render yet, such as several
   confirmed highlight clips.
2. **Loudness normalization (new, have).** This is a
   `loudness_normalize` audio operation in the EDL. It runs a single-pass
   FFmpeg `loudnorm` with defaults of I=-14 LUFS, TP=-1 dBTP, and LRA=11,
   then resamples to 48 kHz. Values outside a bounded range are rejected
   before rendering, and so are any other audio operations.
3. **Traceability and manual guide (new, have).** Each render, export,
   overlay, and replace-audio job writes `trace.json`, `MANUAL-STEPS.md`
   (DaVinci Resolve 19, click by click), and a `history.jsonl` line. Failed
   jobs write them too. See the README.

**Not verified:** none of the interchange files has been opened in a real
DaVinci Resolve or Premiere Pro install in this environment. The tests check
the structure, the frame-accurate timecodes, and the rational times against
the plan. The first owner smoke test should import `timeline.fcpxml` and
`timeline.edl` into Resolve 19 and check the cut points against
`MANUAL-STEPS.md`.

## Capability table

| Capability | Resolve / Premiere / CapCut | Video Studio status | Priority |
|---|---|---|---|
| Trim one source range (in/out) | yes / yes / yes | **have**: engine, frame-accurate in the guide | — |
| Multi-clip timeline (several ranges, any order) | yes / yes / yes | **partial**: EDL, export, guide, and interchange support N clips. The engine renderer still rejects more than 1 segment, and the failed job still hands over the bundle. | **P0** |
| Open the edit in a real editor (EDL / FCPXML / OTIO) | import / import (EDL; FCPXML through a converter) / no | **have** (new): see "Not verified" above | — |
| Click-by-click manual for doing it by hand | — | **have** (new): Resolve 19, with Premiere and CapCut equivalents in the glossary | — |
| 9:16 center crop reframe | yes / yes / yes | **have**: Resolve Zoom/Position matches the FFmpeg crop to about 1 px | — |
| Auto-reframe / active-speaker tracking | Smart Reframe (Studio only) / Auto Reframe / Auto reframe | **missing**: EDL capability `active_speaker` is reserved | P1 |
| Fit with letterbox (`source` profile) | yes / yes / yes | **have** | — |
| Burned-in captions from the transcript (Thai `newmm` wrap) | Subtitle track / Captions / Auto captions | **have** | — |
| Editor caption file with identical line breaks | — | **have** (new): `captions.srt` | — |
| Caption style presets | many / many / many | **partial**: the 4 presets (`minimal`, `bold`, `karaoke`, `emphasis`) change only the font size. Colour, box, and position are fixed. | P1 |
| Karaoke / word-by-word highlight | Text+ / Fusion / yes | **missing**: word timings exist in the transcript, but the ASS `\k` tags are not emitted | P1 |
| Static graphic overlay (logo, lower third) | yes / yes / yes | **have** in the pipeline (`overlay`), with trace and guide. **missing** in the engine EDL (`graphic` kind is rejected). | P1 |
| B-roll / picture-in-picture from an asset library | yes / yes / yes | **missing**: EDL kinds `asset` and `stock` are reserved | P1 |
| Replace audio with a mastered track | yes / yes / yes | **have** in the pipeline (`replace-audio`), with trace and guide. **missing** in the engine EDL. | P2 |
| Loudness normalize (LUFS target) | Deliver ▸ Normalize Audio / Loudness Auto-Match / Normalize | **have** (new): single pass. Two-pass linear `loudnorm` would be within ±0.5 LU. | P2 (two-pass) |
| Music bed + audio ducking under speech | Fairlight sidechain / Auto-ducking / Auto ducking | **missing** | P1 |
| Noise reduction / voice isolation | Voice Isolation (Studio) / Enhance Speech / Noise reduction | **missing** (`audio_cleanup` reserved) | P2 |
| Silence / filler removal (jump cuts) | Cut page tools / Text-based editing / Remove filler words | **missing**: deterministic from transcript gaps, and a good next target | P1 |
| Transitions (cross dissolve, dip) | yes / yes / yes | **missing** | P2 |
| Speed change / speed ramps | Retime / Time Remapping / Speed curve | **missing** | P2 |
| Colour presets / LUT | Color page / Lumetri / Filters | **missing** (`color_correction` and `color_grade` reserved) | P2 |
| Keyframed zoom / punch-in | yes / yes / yes | **missing** | P2 |
| Markers / chapters | markers / markers / — | **partial**: FCPXML has one marker per EDL segment. A YouTube chapter list export is missing. | P2 |
| Platform export presets (TikTok, Reels, Shorts, YouTube) | Deliver presets / Export presets / Export | **partial**: width, height, and profile come from the EDL. No named presets and no per-platform bitrate or loudness. | P1 |
| Output frame-rate conversion | yes / yes / yes | **missing**: the renderer keeps the source rate (no `-r`). The guide and trace warn when `output.fps` differs. | P2 |
| Quality / bitrate control | Restrict to Kb/s / Target bitrate / Bitrate | **partial**: fixed libx264 CRF 23 preset medium. The guide gives the bitrate that matches the engine's file. | P2 |
| Cover frame / thumbnail | — / — / Cover | **missing** | P2 |
| Versioned edits + job history + verify | project versions / auto-save / drafts | **have**: EDL versions, `history.jsonl`, and `python -m production_trace verify` | — |
| Background render queue | Render queue / Media Encoder / — | **have**: Celery worker | — |

## Roadmap (in order)

1. **P0: Multi-clip render.** Use FFmpeg `trim`/`atrim` plus `concat`, with
   captions per clip at the record offsets. `captions/cues.py` already
   computes them. The plan's record frames are the contract. This makes
   confirmed highlight proposals renderable.
2. **P1: Silence / jump-cut removal** from transcript gaps. It is
   deterministic and fits the EDL as more segments, so it needs P0 first.
3. **P1: Named platform presets** (TikTok, Reels, Shorts, YouTube 16:9).
   Each preset sets size, rate, bitrate cap, and loudness target, and is
   recorded in the trace.
4. **P1: Karaoke word highlight and real caption styles** (colour, box,
   position), using the word timings that already exist.
5. **P1: Graphic and B-roll overlay plus audio replace inside the engine EDL.**
   The pipeline tools already have the FFmpeg recipes and Resolve guides.
6. **P1: Music bed with ducking** (sidechain compress).
7. **P2:** two-pass loudness, transitions, speed, LUT and colour presets,
   frame-rate conversion, chapters, thumbnails, and noise reduction.
