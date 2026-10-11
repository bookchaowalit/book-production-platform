# Audio V1A — Loudness and Trim Batch Design

**Status:** Implementation complete; technical acceptance passed; human listening gate pending (2026-09-24)

**Date:** 2026-09-23

**Product:** Audio V1A / Loudness and Trim Batch

**Product owner:** `book-audio-production` under the Book Dev portfolio

**Related:** [Logic-first platform design](2026-09-22-logic-first-production-platform-design.md), [Production platform roadmap](../plans/2026-09-22-production-platform-next-products.md)

## 1. User and outcome

The user approved this design and later instructed implementation to proceed.
The implementation is complete in the separate product repository. Technical
acceptance evidence and the remaining human listening gate are recorded below.

The first Audio product is for a creator who needs to prepare several local
audio files with the same explicit trim and loudness requirements. The user
submits a versioned JSON batch manifest, selects an output format and a loudness
target for each item, and receives trimmed audio masters with a machine-readable
quality report.

V1A is useful when the files are already recorded and the desired loudness is
known. It does not decide which loudness target is right for a platform or
content type. If the request has no valid trim range or loudness target, the job
stops with an actionable validation error.

## 2. Product boundary

The product is a local command-line application in the separate repository
`book-audio-production` at
`bookchaowalit/book-audio-production`.
The Solo Empire root owns this design and portfolio evidence. The product
repository owns audio schemas, processing code, fixtures, tests, dependencies,
runtime output, and its README. The local checkout is on `main`; it has no
configured remote. No product source or runtime data belongs in the Solo Empire
control plane.

V1A is CLI-first. It does not add a web UI, HTTP service, database, background
queue, or shared audio runtime. It follows the existing Video product's
versioned artifact boundary and does not move or modify Video ownership.

## 3. Input and output contract

The input is an `audio-batch.v1` JSON manifest with a batch identifier and a
bounded list of tracks. Each track contains a local source reference, optional
trim start and end in integer milliseconds, an explicit integrated loudness
target in LUFS, an optional loudness-range target, a maximum true-peak target in
dBTP, and an output format (`flac` or `wav_pcm24`). A track without explicit
loudness and peak targets is rejected; there is no universal target default.
Target values must be within the selected FFmpeg filter's supported range; when
no loudness-range target is supplied, the recipe passes `50 LU` explicitly so
the filter's implicit default cannot change behavior between runs.

The initial contract supports mono and stereo audio. It accepts only local
single-file inputs with `.aac`, `.aif`, `.aiff`, `.flac`, `.mka`, `.mkv`, `.mp3`,
`.oga`, `.ogg`, `.opus`, `.wav`, `.wave`, or `.webm` extensions under an
explicitly selected input root. It forces a matching FFmpeg demuxer and rejects
playlist and unknown formats before probing. FFprobe and FFmpeg are limited to
`file` and `pipe` protocols; this is a local input policy, not an OS-level
filesystem sandbox. Proposed pilot caps are 20 tracks per batch, 2 GiB per
source, 2 hours per track, and 8 hours of combined source duration; the limits
are configurable locally but cannot be disabled. The processor rejects
unsupported streams, missing files, symlink escapes, invalid trim ranges, empty
decoded audio, and files exceeding those caps. It never fetches URLs or modifies
source files. The CLI limits input manifests to 1 MiB.

Each successful track produces one lossless audio output and a report containing
the source content hash, recipe and tool versions, requested trim, measured
pre-render and post-render integrated loudness, loudness range when available,
sample peak, true peak, output duration, output hash, QA result, processing
times, and bytes written. Source paths in reports are relative to the selected
input root; telemetry contains no raw audio or absolute paths. Global tags,
stream tags, and chapters are stripped by default and this is stated in the
report.

The selected output root must not overwrite existing files or contain redirected
symlink descendants. Filenames are generated from validated track IDs and
content hashes, and all writes use temporary files followed by atomic
publication.

The batch manifest records `accepted` or `rejected` per track. A batch is
`accepted` when every track passes, `rejected` when none pass, and
`completed_with_errors` when at least one passes and at least one fails. Output
files are staged and exposed only after the individual file passes QA. A failed
track does not hide successful tracks, and the final manifest makes every partial
result explicit.

## 4. Processing and AI policy

The deterministic path is:

```text
batch manifest
  → schema/path/limit validation
  → ffprobe stream inspection
  → trim analysis pass and loudness/peak measurement
  → two-pass FFmpeg loudness normalization and lossless encoding
  → output re-measurement and duration/peak QA
  → atomic artifact publication and manifest
```

The processor invokes `ffprobe` and `ffmpeg` with argument arrays, never a
shell-interpolated command. It pins and records the binaries' versions, applies
per-file timeouts and output size limits, uses isolated temporary directories,
and removes temporary output after a failed job. Recipe version, source hash,
trim values, target parameters, format, and tool versions form the replay key.
Reusing the same key with changed source bytes or settings is a conflict; replay
verifies the prior manifest and output hashes before returning the result.

The V1A standard path uses no LLM, speech recognizer, or image/audio generator.
It reports `model_calls=0`. Local CPU time, wall time, and output bytes are
measured; monetary cost remains `unknown` until an approved local rate card is
configured. No inference cache is needed for this deterministic workflow.

## 5. Loudness, clipping, and intelligibility

Users supply targets per track. A later named preset may be added only when its
delivery context and measured acceptance limits are explicit. EBU R 128 uses
programme loudness and maximum true peak as distinct descriptors, and FFmpeg's
`loudnorm` filter supports file-based two-pass normalization with integrated
loudness, loudness range, and true-peak targets. The output report must retain
each measurement rather than reducing quality to a single “normalized” flag.

V1A measures sample peak and true peak and records possible full-scale/sample
clipping warnings. It does not repair clipped audio. A peak warning must not be
reported as proof that a recording is clipped or unintelligible. V1A does not
score speech intelligibility, perform transcription, remove noise, separate
sources, or synthesize audio. Users receive no promise that loudness processing
will improve those properties.

For measurement tests, use representative speech-like and noise fixtures;
pure tones may exercise the command path but are not the basis for subjective
loudness acceptance thresholds. The proposed initial post-render tolerances are
±0.5 LU for integrated loudness and no more than 0.1 dB above the requested
true-peak ceiling. The exact tolerances are pinned with the recipe version.
Record `near_full_scale_peak` when sample peak is at or above -0.1 dBFS; this is
a warning only and must never be presented as a definitive clip detector.

## 6. Quality gates

- A valid batch with explicit settings produces the expected number of lossless
  outputs, and no source file changes.
- Trimmed output duration matches the requested interval within one decoded
  sample plus container rounding tolerance.
- Post-render integrated loudness and true peak meet the requested targets within
  the pinned initial tolerances; any dynamic processing mode is disclosed.
- Unsupported media, malformed manifests, unsafe paths, missing files, invalid
  trim ranges, timeouts, and output-limit violations fail with stable error
  codes and never produce an accepted artifact reference.
- A modified source or changed settings under an existing idempotency key
  returns a conflict. A changed manifest or output artifact fails replay
  integrity checks.
- Concurrent duplicate requests for the same key publish one accepted result.
- A deterministic synthetic benchmark reports accepted/rejected tracks,
  partial batches, `model_calls`, replay hits, QA failures, processing time, and
  bytes written. Cost is unknown when no rate card is present.
- Audio review on a small golden set checks audible boundary clicks, channel
  count, duration, and obvious render damage. A reviewer listens to paired
  speech-source and output samples to check that trimming and loudness processing
  did not make the speech harder to understand. This manual gate applies only to
  the small golden set; numeric loudness tests do not claim to prove
  intelligibility or artistic quality.

## 7. Failure and recovery behavior

Validation errors reject a track before invoking FFmpeg. A missing executable,
unsupported codec, timeout, or nonzero process exit produces a stable failure
code with a sanitized diagnostic. FFmpeg command lines and absolute paths are
not returned in public JSON output. Temporary files are cleaned up on failure.
Successful tracks remain available only with a final manifest that identifies
the batch as partial when other tracks failed.

Each attempt stages artifacts and its manifest under `.staging/<batch-id>`;
publication verifies the artifact hashes before atomically renaming the complete
batch directory into place. A retry with the same ID discards leftover
incomplete staging under the batch lock, while a lock left by a crashed process
is retained for manual inspection. A replay validates existing manifests and
hashes before reusing an output. One deadline covers source fingerprinting,
lock waiting, processing, artifact hashing, and verification. Changed inputs,
settings, limits, toolchain, or recipe under an existing idempotency key return a
conflict.

## 8. Rollback and rollout

V1A is local and opt-in. Rollback consists of returning to a prior pinned recipe
and FFmpeg build; outputs already created remain ordinary local files and are
never deleted automatically. There is no automatic upload, publishing,
notification, or external write. A later hosted service requires a separately
reviewed privacy, authentication, storage, and deployment design.

The product code and its verification stay in the separate product repository.
The Solo Empire root records the design, plan, and acceptance evidence. The
product has no configured remote or deployment target; no catalog entry is
claimed.

## 9. Implementation and acceptance evidence

Recorded 2026-09-24 from product commit `f53440661389982392390a152f99a40e64ced812`:

- The product repository is clean on `main`, with no configured remote. Python
  is 3.12.10; FFmpeg and FFprobe match the pinned
  `9.0.1-full_build-www.gyan.dev` build.
- `.\.venv\Scripts\python.exe -B -m pytest -q` passed 142 tests; one symlink
  test was skipped because this host did not permit symlink creation. The CLI
  help check passed.
- The final 20-sample synthetic benchmark reported 21 accepted tracks, 2
  rejected tracks, 1 partial batch, 0 QA failures, 1 replay hit, 633,297 output
  bytes, 12.297 seconds wall time, CPU measurement unavailable,
  `model_calls=0`, and unknown monetary cost.
- The `audio-v1a.2` offline synthetic speech source/output pair passed all seven
  numeric QA checks and manifest/artifact verification. It is ready for manual
  listening; no human listening result is recorded, and it does not establish
  intelligibility on real speech.
- No user recording was committed. No remote, hosted repository, or deployment
  target exists, so there is no push, catalog entry, or deployment claim.

## 10. References

- [FFmpeg Filters Documentation — `loudnorm`](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)
- [EBU R 128 — Loudness normalisation and permitted maximum level](https://tech.ebu.ch/publications/r128)
- [ITU-R BS.1770-5 — Algorithms to measure audio programme loudness and true-peak audio level](https://www.itu.int/rec/R-REC-BS.1770-5-202311-I)
