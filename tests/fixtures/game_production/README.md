# Game Scene Builder benchmark fixtures

The synthetic benchmark contains five valid projects in `accepted/` and nine
rejected inputs in `rejected/`. Each valid project runs Unity compile,
EditMode, PlayMode, and a fresh-process reopen, then runs once more to verify an
exact replay without starting Unity. A tenth rejection case tampers with one
accepted scene and checks that replay fails closed.

The accepted cases cover an empty wall set, multiple wall groups, two
objectives, movement beside a wall, and a larger room with four objectives.
Rejected files cover malformed JSON, an oversized payload, an unknown
code-like field, unsupported template, duplicate ID, out-of-bounds position,
overlapping entities, out-of-range speed, and the wall-cell cap. The two small
`benchmark_case` descriptors are expanded by the benchmark into a payload just
over 256 KiB and a valid 193-wall-cell GameIR; generated payloads are kept in a
temporary directory and removed after the check.

Run the suite from the product repository root:

```powershell
book-production game benchmark `
  --fixtures .\tests\fixtures\game_production `
  --output .\.runtime\game-production\benchmark.json `
  --unity "C:\Program Files\Unity\Hub\Editor\6000.6.2f1\Editor\Unity.exe"
```

The report contains aggregate counts, stable rejection codes, build/replay
latency, project byte totals, Unity test totals, trace-event summaries, the
Unity executable hash, and `model_calls: 0`. It contains no absolute paths or
input payloads. Unity/editor availability failures are reported with
`runtime_acceptance: "blocked"` and their stable error codes.

## Latest local evidence

The 2026-09-24 run at `.runtime/game-production/benchmark-final.json` passed
5 builds, 5 exact replays, 9 invalid-input rejections, and 1 tampered-replay
rejection. EditMode passed 10/10 and PlayMode passed 5/5 with
`model_calls: 0`. `manual_scene_review` remains `pending`; automated gameplay
checks do not stand in for opening and visually reviewing the Unity scene.
