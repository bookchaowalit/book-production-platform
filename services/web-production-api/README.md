# Web Production API — V1A Template Landing Builder

Web V1A turns a structured page request into a fixed, reviewable landing page
with local HTML and CSS artifacts. The sections are hero, features, optional
pricing (1–3 plans), optional FAQ (up to 8 `<details>` items), call to action,
and footer. The engine:

- escapes user text and validates relative links;
- writes SEO and OpenGraph/Twitter meta;
- emits responsive CSS with Webflow-aligned breakpoints (Tablet ≤991px,
  Mobile ≤767px) and a keyboard-visible focus state;
- records replay-safe manifest hashes.

See [docs/PARITY.md](docs/PARITY.md) for how this compares with Webflow,
Framer, and WordPress, and for the roadmap.

The standard path is deterministic and makes zero model calls. It accepts no
raw HTML or JavaScript, fetches no external assets, and has no deploy or
publishing endpoint. A future content or page-planning adapter must produce a
validated `PageRequest` before it can enter this renderer.

## Local CLI

```text
python -m web_production.cli build \
  --input page.json \
  --output .runtime/web-production \
  --idempotency-key demo-001
```

The input can be a page object or `{ "page": { ... } }`. Links are limited to
relative paths, fragments, and `mailto:` references. `javascript:`, `data:`,
HTTP(S), protocol-relative links, raw HTML fields, and external CSS references
are rejected.

## HTTP interface

- `GET /health`
- `POST /v1/web/pages` with `{ "idempotency_key": "...", "page": { ... } }`

Invalid requests return 422 and idempotency conflicts return 409. Successful
responses contain only artifact filenames, media types, hashes, and sizes.

## Benchmark

```text
python -m web_production.scripts.benchmark \
  --fixture tests/fixtures/pages_20.json \
  --output-dir .runtime/web-v1a-benchmark
```

The synthetic benchmark checks 20 deterministic pages, replay hits, QA, and
zero model calls. Cost remains `unknown` until an approved local rate card is
available.

## AI brief adapter (V1B)

`web_production.provider.ProviderPageAdapter` implements the shared
[`brief-adapter.v1`](../../packages/brief-adapter-kit/README.md) pattern
(spec REQ-AI-01..09, REQ-WEB-01, AC-WEB-01a). A model *proposes* a
`PageRequest` from a free-text brief. It may use only the registered sections
of the `landing-v1` template (`hero`, `features`, `pricing`, `faq`, `contact`,
`footer`) and plain copy text:

- The engine owns all HTML, CSS, JavaScript, URLs, hrefs, slugs, colours, and
  images. A proposal is **rejected** if it contains markup, script, style,
  template, or URL-like text, an unknown section, or an engine-owned key.
- The business or product name, every feature title, and every pricing plan
  name and price must appear verbatim in the brief. Copy may be inferred, but any number, price, currency,
  e-mail address, phone number, or claim (for example "best", "guaranteed",
  "free", "ดีที่สุด", "รับประกัน") must be stated in the brief. Anything else
  becomes `needs_input`.
- A complete proposal still goes through `web_ir.validate_request`.
- A brief that itself asks for engine-owned content (markup, script, a URL or
  link, or a file path such as `../../etc/passwd`) is **rejected** with
  `brief_engine_value_forbidden` before any model call, so the refusal never
  depends on the model dropping the request.
- The model's `missing` list is advisory: only the critical fields `title` and
  `features` block completion there. Optional sections it calls missing are
  omitted; every critical value is still grounded by the engine.

The adapter fails closed without a `web-provider-config.v1` file
(`provider_not_configured`), which needs a rate card, caps, and owner
approval; the default `prompt_version` is `web-brief.v2` (copy the complete
name, field names only in `provided`/`inferred`/`missing`). It abstains on instruction-like briefs with zero calls and makes no
more than two calls per job. It never writes the brief into issues, the
ledger, or reports.

```text
python -m web_production.cli brief-shadow --provider-config .runtime/web-provider.json \
  --cases tests/fixtures/web_brief30_cases.json --split tests/fixtures/web_brief30_split.json \
  --output .runtime/web-shadow-report.json
python -m web_production.cli brief --input brief.json --output .runtime/web-production \
  --idempotency-key demo-ai-1 --provider-config .runtime/web-provider.json
```

`brief-shadow` evaluates EVAL-WEB-brief30 (30 synthetic cases, dev/held-out
split). It never builds or uses the results, and it writes a metadata-only
report. The gate is decided on held-out cases and follows spec §4.2:

- critical-field exact match ≥ 95%;
- `needs_input` recall 100%;
- adversarial silent passes 0, model calls ≤ 2, canary leaks 0;
- for AC-WEB-01a, raw HTML from the model 0 and new accessibility violations
  0, checked with the existing QA on an in-memory render;
- engine-owned values from the model 0;
- structured cases make zero model calls.

A clear case may carry `expected_title_alternatives`, read only by the shadow
gate, when its brief has more than one equally valid verbatim reading of the
name (for example `web-clear-th-02`, where "สอง บริการ" also reads as "two
services"). The alternative must still be grounded in the brief.

`brief` refuses any config whose `mode` is not `live` (`shadow_mode_only`).
The owner sets `live` only after a passing report. The deterministic `build`
path never imports the kit. AI commands need `packages/brief-adapter-kit` on
`PYTHONPATH` and fail closed with `provider_not_configured` without it.

## Job history, trace & manual steps

Every new `build`/`brief` attempt (accepted, rejected, or failed; replays
excluded) records the job with
[`production-trace`](../../packages/production-trace/README.md):

| File | Where | What |
|---|---|---|
| `jobs/<job_id>/trace.json` | output dir | Request hash, hero-image hash, the steps in order (`validate_request`, `compile_page`, optional `verify_hero_image`, `render_html` with the section list, `render_css` with breakpoints and theme, `write_bundle`, `run_page_qa`, `write_manifest`) with durations, the validated-IR hash, AI proposal metadata for `brief` (adapter, mode, model calls, provided/inferred field names), artifact sha256 values, and the QA outcome |
| `jobs/<job_id>/ir.json` | output dir | The exact validated `page-ir.v1` the engine rendered |
| `jobs/<job_id>/MANUAL-STEPS.md` | output dir | How to build the same page by hand in Webflow Designer. It gives the Navigator layer tree (Body ▸ Header.hero ▸ Div.container ▸ …) and numbered click-level steps (Add panel / Style panel / Settings panel, the exact copy, hrefs, theme hex values, pre-computed `color-mix` colours, spacing, both breakpoints, keyboard-focus state, SEO/OpenGraph settings, publish/export). It also has an accessibility checklist that matches the engine QA and a plain HTML/CSS path in VS Code |
| `jobs/<job_id>/<artifacts>` | output dir | Hard links (or copies) of the delivered HTML/CSS/image, so `verify` re-hashes the same bytes |
| `history.jsonl` | output dir | One metadata line per attempt |

Traces and history never contain page copy or the brief. Long values are
hashed, and failure notes are codes only. The manual contains the copy a
person must type, so it has the same sensitivity as the page. The manifest's
`metadata.trace_job_id` links an accepted page to its trace. Tracing never
masks a failure: an engine exception is recorded as `failed` and then
re-raised.

```text
python -m production_trace history --root .runtime/web-production
python -m production_trace show    --root .runtime/web-production <job_id>
python -m production_trace manual  --root .runtime/web-production <job_id>
python -m production_trace verify  --root .runtime/web-production <job_id>
```

The CLI needs `packages/production-trace` and `packages/web-production-ir` on
`PYTHONPATH`, as production-hub and production-pipelines set them.
`brief_adapter_kit` is imported only by the AI subcommands.

Recipe `landing-template-v3` changes the input hash. A replay of a key built
under v2 returns `idempotency_key_reused_with_different_input`, so use a new
key to rebuild.
