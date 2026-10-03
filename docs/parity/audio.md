# Audio Master V1A — feature parity with Audacity, Adobe Audition, and iZotope

Scope: `audio_production` + `audio_ir` (tool id `audio-master-v1a`). The
engine masters one local PCM WAV with a named preset: an optional fixed
clean-up chain, FFmpeg `loudnorm` two-pass linear normalisation to the
preset's integrated loudness under its true-peak ceiling, and 24-bit/48 kHz
WAV export. Batch trimming and per-track targets belong to
`book-audio-production` (its own contract); this matrix only covers the
single-file master.

Status: **have** / **partial** / **missing**; priority P1 next, P2 later,
P3 on demand, — out of scope or owned by the sibling repository.

## Capability matrix

| Capability | Audacity / Audition / iZotope | Engine status | Priority | Notes |
|---|---|---|---|---|
| Import PCM WAV (mono/stereo, 16/24/32-bit) | yes | have | — | ≤ 2 GiB, ≤ 2 h |
| Import MP3 / FLAC / M4A | yes | missing | P1 | decode to WAV upstream for now |
| Loudness normalisation to LUFS | Loudness Normalization / Match Loudness / Ozone | have | — | two-pass, linear when the ceiling allows |
| True-peak ceiling | Audition Hard Limiter (true peak), Ozone Maximizer | partial | P2 | enforced by `loudnorm` and QA-checked; no standalone brickwall limiter stage |
| Platform loudness targets | presets in all three | **have (new: 7 presets)** | — | see `presets.PLATFORM_TARGETS` |
| High-pass filter | High-Pass Filter / Parametric EQ | **have (new)** | — | 80 Hz, 12 dB/oct in `voice-clean-mono.v1` |
| Parametric / tonal EQ | yes | missing | P2 | |
| Noise gate / expander | Noise Gate / Dynamics / RX | **have (new)** | — | `agate` expander in `voice-clean-mono.v1` |
| Spectral noise reduction | Noise Reduction / RX Spectral De-noise | missing | P1 | FFmpeg `afftdn`/`arnndn` candidates; needs listening review |
| De-esser | Audition DeEsser / RX De-ess (Audacity: plugin only) | **have (new, approximate)** | — | FFmpeg `deesser` in `voice-clean-mono.v1`; parameter units differ from Audition |
| Compressor | Compressor / Dynamics / Ozone | missing | P1 | `acompressor` for voice levelling |
| Trim, fades, silence removal | yes | — | — | owned by `book-audio-production` |
| Batch processing | Macros / Batch Process | — | — | owned by `book-audio-production` |
| Export WAV 24-bit/48 kHz | yes | have | — | metadata stripped |
| Export MP3 / AAC / FLAC, 16-bit with dither | yes | missing | P1 | delivery formats for podcasts and audiobooks |
| Metering: integrated LUFS, true peak, LRA | Loudness meters | have | — | measured before and after, QA within ±0.5 LU / +0.1 dB |
| ACX check (RMS −18…−23 dB, peak ≤ −3 dB, noise floor ≤ −60 dB) | Audacity ACX Check | partial | P1 | `audiobook-acx-mono.v1` approximates with −20 LUFS / −3 dBTP; RMS and noise floor are not measured |
| Spectral repair, de-click, de-reverb | iZotope RX | missing | P3 | |
| Multitrack mixing, music/voice ducking | Audition multitrack | — | — | |
| History, provenance, redo by hand | — | **have (new)** | — | `trace.json` + `history.jsonl` + Audacity `MANUAL-STEPS.md` |

## Presets after this pass

| Preset | Channels | Target | True peak | Chain before loudnorm |
|---|---|---|---|---|
| `podcast-mono.v1` | 1 | −19 LUFS | −1.5 dBTP | — |
| `podcast-stereo.v1` | 2 | −16 LUFS | −1.5 dBTP | — |
| `music-stereo.v1` (Spotify/YouTube) | 2 | −14 LUFS | −1.0 dBTP | — |
| `apple-music-stereo.v1` (new) | 2 | −16 LUFS | −1.0 dBTP | — |
| `broadcast-r128-stereo.v1` (new, EBU R128) | 2 | −23 LUFS | −1.0 dBTP | — (LRA target 20 LU) |
| `audiobook-acx-mono.v1` (new, ACX approximation) | 1 | −20 LUFS | −3.0 dBTP | — |
| `voice-clean-mono.v1` (new) | 1 | −19 LUFS | −1.5 dBTP | `highpass` 80 Hz → `agate` → `deesser` |

Presets without a chain keep their previous fingerprints, so existing masters
still replay.

**Runtime-dependent, not verified here:** FFmpeg is not installed in this
environment. The new presets are tested with a scripted process runner that
checks the exact filter graph order and writes a real PCM24/48k WAV; whether
FFmpeg's `agate`/`deesser` settings sound right on real speech, and whether
every preset passes QA on real material, still needs a run with FFmpeg and a
listening review.

## Roadmap (prioritised)

1. P1 — Run all presets through real FFmpeg on the synthetic fixtures and a reviewed voice sample.
2. P1 — Voice compressor stage (`acompressor`) and spectral noise reduction preset.
3. P1 — ACX RMS and noise-floor measurement in QA for `audiobook-acx-mono.v1`.
4. P1 — MP3/AAC/FLAC delivery export with 16-bit dither as separate traced artifacts.
5. P2 — Parametric EQ presets (voice presence, de-mud).
6. P2 — Standalone true-peak limiter stage for masters that fall back to dynamic mode.
