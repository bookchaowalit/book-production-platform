# Brief Adapter Kit

Shared, fail-closed kit for the `brief-adapter.v1` pattern: an AI model turns
a human brief into a **proposal of a tool's IR** (a small structured JSON),
and the tool's deterministic engine does the real work. The model never
creates the final artifact, never writes code/SQL/HTML/formulas, and never
calculates numbers. This keeps each paid call small (input ≤ 8,000 tokens,
output ≤ 2,000 tokens, ≤ 2 calls per job) and makes every result checkable.

Spec: Production Tools SDD program (Solo Empire `docs/superpowers/specs/2026-09-29-production-tools-spec-driven-development-design.md`)
(REQ-AI-01..09, section 4 eval framework). Reference implementation:
[Documents V1B](../../services/document-production-api/README.md).

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | `ProviderConfig`: gateway URL (https, or loopback http), the *name* of the token env var, route, prompt version, profile, privacy class (`public`/`internal` only), rate card, per-job/day/month caps, owner approval, `mode` (`shadow` default, `live` only by owner) |
| `config.py` (reasoning) | `reasoning_effort` (`none`…`max`) + `reasoning_tokens` headroom on top of the 2,000-token JSON answer; with a reasoning effort, `timeout_seconds` ≤ 120 and `job_deadline_seconds` ≤ 300 (gateway limits). Missing headroom fails closed |
| `ledger.py` | SQLite `BudgetLedger`: atomic call/token reservations (≤ 2 calls/job) and cost reservations against all three caps; an unknown actual cost stays charged |
| `transport.py` | The only network/env code: `POST {gateway}/api/ai/generate` with a bearer token from the configured env var; errors become a short class (`timeout`, `rate_limited`, `gateway_auth`, …), bodies are never surfaced |
| `runner.py` | `GatewayProposalRunner.propose()`: injection guard → reserve call + worst-case cost → one call → settle → JSON parse → at most one repair call |
| `grounding.py` | Evidence helpers: dates (ISO, D/M/Y, English/Thai month names, Buddhist Era), numbers (Thai digits, thousands separators), currencies, verbatim text |
| `shadow.py` | Eval helpers: case loading, composition counts, canary scan over the report and ledger, metadata-only report assembly |
| `testing.py` | `ScriptedTransport` offline double (queued replies or an oracle responder) |

## Adapter pattern (apply per tool)

1. **IR proposal schema.** Define the smallest JSON the model may return:
   registry IDs (template, section, preset, node, intent), copied text, and
   copied values with `provided` / `inferred` / `missing` provenance. Nothing
   the engine computes (totals, formulas, SQL, HTML, geometry, timings).
2. **Prompt.** One compact system prompt naming the allowlists and the JSON
   shape, stating that the brief is untrusted data. Put the brief inside
   `<brief>…</brief>` in the user message. Do not add a response-language
   instruction; the gateway owns the Thai-first policy (REQ-AI-09).
3. **Run.** `GatewayProposalRunner(config, ledger_root=...)` and
   `propose(job_id=..., system_prompt=..., user_content=..., guard_text=brief,
   accept=shape_check)`. An `abstained` outcome means instruction-like content;
   return `needs_input` with zero calls.
4. **Ground and validate (REQ-AI-02/03/07).** Reject anything outside the
   allowlist. Keep a critical value only when the brief states it (use
   `grounding.py`); otherwise move it to `missing`. Run the tool's existing
   IR validator. Any doubt → `needs_input`, never a guess.
5. **Fake adapter + eval suite (REQ-AI-08).** Offline tests with
   `ScriptedTransport`; an eval fixture with structured ≥ 1/3, clear ≥ 1/3,
   ambiguous ≥ 1/6, adversarial ≥ 1/6, half Thai/half English per category,
   a canary per case, and a dev/held-out split. Composition is a test.
6. **Shadow → live.** A `<tool> brief-shadow` command runs the real adapter
   over the suite and writes a metadata-only report; the gate uses held-out
   cases (spec §4.2 plus the tool card's AC). Only after it passes and the
   owner approves the rate card does the owner set `mode` to `live`.

## Provider configuration

Copy `examples/provider-config.example.json` next to the tool's runtime (for
example under `.runtime/`), set the tool fields, the owner's rate card and
caps, and the approval record. The gateway token stays in the environment
variable named by `token_env`; it is never written to the file.

## Tests

```bash
PYTHONPATH=packages/brief-adapter-kit python -m pytest packages/brief-adapter-kit/tests -q
```

The book-production-platform repository carries a vendored copy under
`src/book_production_platform/brief_adapter_kit/`; keep both copies identical
(the test `tests/test_vendored_packages_parity.py` checks it).
