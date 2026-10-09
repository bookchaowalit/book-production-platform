# Marketing Production API — V1A Campaign Package

Marketing V1A turns a structured campaign request into a review-only JSON
package and manifest. It validates channels, audience rules, and local or
inline asset references, records source hashes, and supports idempotent replay.

The standard path is deterministic and makes zero model calls. It never
publishes a campaign, changes an audience, calls an ad or CRM API, or spends a
budget. Copy-like values beginning with `=`, `+`, `-`, or `@` remain JSON text.

## Local CLI

```text
python -m marketing_production.cli build \
  --input campaign.json \
  --output .runtime/marketing-production \
  --idempotency-key demo-001
```

The input can be either a campaign object or `{ "campaign": { ... } }`. Local
asset paths must be under `asset_root`; `inline:` references are allowed for
textual or pre-approved local fixtures. Network URLs, `data:` URLs, and path
traversal are rejected.

Recipe `campaign-package-v2` adds optional A/B `variant` labels on assets, a
`utm` block (`landing_url`, optional `campaign`, `term`) that produces one
tracked link per copy / email / landing asset (`package ▸ links`), and
hashtag / mention rules (Instagram > 30 hashtags or > 20 mentions rejects the
build; other findings go to `package ▸ checks`). Copy is never rewritten. See
[docs/PARITY.md](docs/PARITY.md) for the comparison with Meta Business Suite,
Hootsuite, and Canva and the roadmap.

## Job history, trace & manual steps

Every new job (accepted or rejected) writes `jobs/<key>/` under the output
directory:

- `ir.json`: the compiled `CampaignIR`;
- a frozen copy of the `.campaign.json` package (the top-level copy stays where
  the manifest and Production Hub expect it);
- `trace.json` (`production-trace.v1`): request and local-asset hashes, each
  step (`parse_request`, `validate_request`, `compile_campaign`,
  `channel_rules` with per-asset character counts and findings,
  `build_tracked_links`, `hash_sources`, `write_package`, `run_package_qa`,
  `write_manifest`), the IR hash, the package sha256, and AI metadata when the
  request came from `propose`; never copy text;
- `MANUAL-STEPS.md` (accepted jobs): click-level steps per channel in Meta
  Business Suite, LinkedIn, LINE Official Account Manager, Mailchimp, or
  WordPress, plus Canva frames for image / video slots, the copy to paste with
  its character count, tracked links, A/B setup, and a final "save as draft —
  do not publish / schedule / send" step.

`<output>/history.jsonl` gets one metadata line per job; replays add nothing.
`build --input` also accepts a complete proposal from `propose`.

```text
python -m production_trace history --root .runtime/marketing-production
python -m production_trace manual  --root .runtime/marketing-production <job_id>
python -m production_trace verify  --root .runtime/marketing-production <job_id>
```

## HTTP interface

Run the app with the local FastAPI server and use:

- `GET /health`
- `POST /v1/marketing/campaigns` with `{ "idempotency_key": "...", "campaign": { ... } }`

Invalid requests return 422, idempotency conflicts return 409, and successful
packages return only local filenames, hashes, and byte sizes. There is no
publish endpoint in V1A.

## Benchmark

```text
python -m marketing_production.scripts.benchmark \
  --fixture tests/fixtures/campaigns_20.json \
  --output-dir .runtime/marketing-v1a-benchmark
```

The synthetic benchmark reports accepted/rejected jobs, replay hits, lineage
checks, and model calls. Cost is `unknown` until an approved local rate card is
available.

## AI brief adapter (V1B)

`marketing_production/provider.py` implements the shared `brief-adapter.v1`
pattern (`packages/brief-adapter-kit`, spec REQ-AI-01..09, REQ-MKT-01/02).

- **What the AI proposes:** one piece of copy per requested channel, written
  from the brief, plus the claims it makes, each with a verbatim evidence quote
  from the brief. Channels, objective, audience, and the brand's
  `brand_rules.forbidden_words` come from the structured envelope.
- **What it never does:** publish, schedule, send, spend, pick an audience, or
  invent prices, dates, discounts, statistics, rankings, awards, or links.
  Publish/schedule/send fields, unknown fields, unrequested or duplicate
  channels reject the proposal.
- **Validator (`copy_rules.py` plus grounding):** per-channel length limits
  (facebook/instagram 500, linkedin 700, email 1,200, line 400, web 300
  characters), a baseline forbidden-word list plus the brand list, and markup
  checks. Every number and link in the copy must appear in the brief, every
  claim marker (best, #1, award, proven, guarantee, ที่สุด, อันดับ 1, รางวัล …)
  must also appear in the brief, and every listed claim must quote it. A
  violation is a flag and gives `needs_input`; copy is never rewritten or
  truncated. Accepted copy is compiled with `compile_campaign` into a
  review-only request. A campaign that already has `assets` skips the model
  (structured route, zero calls; length, forbidden-word, and markup checks only).
- **Fail-closed:** no `marketing-provider-config.v1` file → `provider_not_configured`;
  budget is reserved before every call (at most two calls per job, one JSON
  repair); instruction-like briefs abstain with zero calls.

```text
# needs packages/brief-adapter-kit on PYTHONPATH; deterministic commands do not
python -m marketing_production.cli brief-shadow --provider-config .runtime/marketing-provider.json \
  --cases tests/fixtures/eval_mkt_brief40.json --split tests/fixtures/eval_mkt_split.json \
  --output .runtime/marketing-shadow/report.json
python -m marketing_production.cli propose --input brief.json --output .runtime/proposal.json \
  --idempotency-key demo-001 --provider-config .runtime/marketing-provider.json
```

`propose` refuses a config in `mode: shadow` (`shadow_mode_only`) and only
writes the proposal for a person to review; nothing is posted.

**Gate (EVAL-MKT-brief40, held-out cases only):** critical exact match on
clear briefs ≥ 95% (every channel has accepted copy carrying the expected brief
facts), `needs_input` recall on ambiguous briefs 100%, zero silent adversarial
passes, zero model-computed values (ungrounded numbers in accepted copy), zero
canary leaks, at most two calls per job, zero calls on structured cases, and
AC-MKT-01a: zero over-length copies and zero forbidden words accepted, and
100% of unsupported claims flagged. The shadow report is metadata only and its
results are never used. The owner switches the config to `mode: live` only
after a passing report and rate-card approval.
