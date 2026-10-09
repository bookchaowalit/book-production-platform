# Production Trace

Stdlib-only package that gives every production tool the same three
outputs per job, so any past job can be looked up, verified, and redone by
hand:

| File | Where | What |
|---|---|---|
| `trace.json` (`production-trace.v1`) | job directory | Inputs (hashes), validated IR hash, whether AI proposed it, every engine step (stage, operation, parameters, duration, status), artifacts (size + sha256), QA outcome |
| `MANUAL-STEPS.md` | job directory | How a person makes the same output in a real program: setup, layer/track/sheet table, numbered click-level steps (menu or tool, where, value), and checks against the engine's file |
| `history.jsonl` (`production-history.v1`) | output root | One metadata line per job (success and failure) pointing at its trace |

Traces never contain raw briefs, transcripts, or private text — long values are
replaced by their hash. The manual guide does contain the content a person
would type (same sensitivity as the artifact it describes) and stays next to
the artifact.

## Use in an engine

```python
from production_trace import ManualGuide, TraceRecorder

trace = TraceRecorder(tool_id="documents-v1a", job_id=job_id, engine_version=ENGINE, recipe_version=RECIPE)
trace.input_json("request", request_payload)
with trace.step("recipe", "build_ir"):
    ir = build_ir(request)
trace.ir_document(ir_dict, schema="document-ir.v1", path=job_dir / "ir.json")
with trace.step("render", "render_docx") as step:
    files = render(ir)
    step.outputs = [f.name for f in files]
for f in files:
    trace.artifact(f)
trace.finish(status="accepted", job_dir=job_dir, output_root=root, guide=build_manual_guide(ir))
```

Build the `ManualGuide` from the same IR the engine executed, mirroring the
renderer (positions, sizes, timecodes, fonts, formulas), and say which values
must be copied from the engine rather than recalculated.

## Look back at any job

```bash
python -m production_trace history --root .runtime/document-out          # list jobs
python -m production_trace show    --root .runtime/document-out <job_id>  # steps + hashes
python -m production_trace manual  --root .runtime/document-out <job_id>  # manual guide
python -m production_trace verify  --root .runtime/document-out <job_id>  # re-hash artifacts
```

## AI-written manual for every job (`MANUAL-AI.md`)

Every piece of work is different, so besides the deterministic
`MANUAL-STEPS.md` each job can get a guide written by AI for that exact piece,
in the program the person uses. `TraceRecorder.finish` also saves the guide's
facts as `manual-facts.json`, then (only when configured) calls
`ai_manual.maybe_write_ai_manual`:

- The model gets the job's facts with ids (`L1` structure, `S1` steps) and the
  program name, and returns an approach, ordered sections and steps, tips, and
  checks. It refers to values by id; the renderer inserts the exact values.
- Every number the model writes itself must exist in the facts (or be a small
  count, or a percentage of a 0..1 fact); otherwise one repair call, then the
  line is marked ⚠️. Unknown ids are marked too. A program that cannot make the
  work gives `needs_input`; an instruction-like program name is refused with no
  call; text inside the facts is data, never instructions.
- It never fails, delays, or changes the job: after `trace.json` and the
  history entry are written, the guide is written by a detached background
  process, so the job, its CLI, and any caller with a timeout (Production
  Hub stages, HTTP APIs) return at once. Its outcome is `ai-manual.json`
  (`queued`, then `status`, `program`, `model_calls`, `ungrounded_values`,
  `unknown_refs`, `covered_layers`, `issue`); `trace.json` is never rewritten.
  `PRODUCTION_AI_MANUAL_INLINE=1` runs it in-process instead.
- Test runs never call the model: under pytest the writer stays off even with
  a live config in the shell (this package's own tests opt in with
  `PRODUCTION_AI_MANUAL_IN_TESTS=1` and scripted transports).

Enable it for all tools on a machine:

```bash
export PRODUCTION_AI_MANUAL_CONFIG=/path/to/ai-manual.json        # brief-adapter-provider-config.v1, mode live
export PRODUCTION_AI_MANUAL_PROGRAMS='{"graphics-v1a": "Affinity Designer 2", "video-postproduction": "DaVinci Resolve"}'
export PRODUCTION_AI_MANUAL_BUDGET_ROOT=.runtime/ai-manual-budget   # persistent cost ledger
export SOLO_EMPIRE_AI_GATEWAY_TOKEN=...                            # the env var the config names
```

Without `PRODUCTION_AI_MANUAL_PROGRAMS` a tool's own guide program is used.
The config starts in `mode: shadow` (nothing is written) until the owner
switches it to `live` after the gate passes:

```bash
python -m production_trace ai-manual-shadow --provider-config ai-manual.json \
  --cases tests/fixtures/eval_ai_manual12.json --split tests/fixtures/eval_ai_manual12_split.json \
  --output .runtime/ai-manual-shadow.json
python -m production_trace explain --root <output-root> <job_id> --program "Adobe Photoshop"  # re-explain one job
```

EVAL-MANUAL-12 uses real guides from Graphics (v1 and themed v2),
Spreadsheet, and Data Viz with eight programs; the gate needs clear guides
complete (no ungrounded value or unknown id, ≥ 90% structure covered) ≥ 95%,
unsuitable programs answered `needs_input`, and no injection followed.

`seconds_to_timecode` / `frames_to_timecode` help timeline tools (video,
audio) write exact positions.

The book-production-platform repository carries an identical vendored copy
under `src/book_production_platform/production_trace/`
(`tests/test_vendored_packages_parity.py` checks it).
