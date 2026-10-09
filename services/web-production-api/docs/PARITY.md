# Web V1A — feature parity with real site builders

Honest comparison of what `web-v1a` (deterministic landing-page engine,
recipe `landing-template-v3`) does against Webflow, Framer, and WordPress.
"Have" means the engine produces it deterministically today, with QA. "Partial"
means a limited version exists. "Missing" means it does not exist.

V1A is deliberately a **single static landing page from a fixed template**.
Many gaps below exist by design (no JavaScript, no external assets, no
deploy). They are listed so nobody mistakes the engine for a full site
builder.

## Capability matrix

| # | Capability | Webflow | Framer | WordPress | web-v1a | Priority |
|---|---|---|---|---|---|---|
| 1 | Semantic HTML landmarks (header/main/footer), one H1 | have | have | theme-dependent | **have** (QA-enforced) | — |
| 2 | Escaped copy, no raw HTML/JS | n/a | n/a | n/a | **have** | — |
| 3 | Hero, features grid, CTA, footer | have | have | have | **have** | — |
| 4 | Pricing section (plans, price text, feature list) | have | have | via blocks/plugins | **have** (new in v3, 1–3 plans) | — |
| 5 | FAQ section (accessible accordion) | have (Dropdown/custom) | have | via blocks | **have** (new in v3, `<details>`/`<summary>`, no JS) | — |
| 6 | SEO title + meta description | have | have | have (plugins) | **have** (QA-enforced) | — |
| 7 | OpenGraph / Twitter card meta | have | have | via plugin | **partial** (new in v3: og:type/title/description/locale, twitter:card; **no og:image** because it needs an absolute URL on a real domain) | P2 |
| 8 | Responsive breakpoints | have (991/767/478) | have (custom) | theme-dependent | **have** (new in v3: Tablet ≤991px, Mobile ≤767px — same as Webflow; QA checks each) | — |
| 9 | Keyboard focus visibility | have (states) | have | theme-dependent | **have** (`:focus-visible`, QA-enforced) | — |
| 10 | Theme colours | have (variables) | have | have | **have** (primary/background/foreground) | — |
| 11 | Custom fonts / type scale | have | have | have | **missing** (system font stack only, fixed scale) | P2 |
| 12 | Hero image (local, alt required) | have | have | have | **have** (hash-verified, bundle-local) | — |
| 13 | Image gallery / multiple images | have | have | have | **missing** | P2 |
| 14 | Testimonials section | have | have | via blocks | **missing** — needs verbatim-quote grounding so AI cannot invent testimonials | P1 |
| 15 | Contact/lead form | have (Forms) | have | via plugin | **missing** (only a `mailto:` CTA; a form needs a backend) | P1 |
| 16 | Multi-page site, navigation menu | have | have | have | **missing** (one page) | P2 |
| 17 | CMS collections / blog | have | have | have (core) | **missing** | P3 |
| 18 | Structured data (JSON-LD) | via embed | via embed | via plugin | **missing** (would need `<script type="application/ld+json">`, which QA currently bans) | P2 |
| 19 | Canonical URL, sitemap.xml, robots.txt | have | have | have | **missing** (needs a known domain) | P2 |
| 20 | Interactions / animations | have | have | theme/plugin | **missing** (by design: no JS) | P3 |
| 21 | Localization (multi-locale) | have | have | via plugin | **partial** (one `th` or `en` page per job) | P3 |
| 22 | Hosting / publish / custom domain | have | have | have | **missing** (by design: no deploy endpoint) | P3 |
| 23 | Accessibility audit | Audit panel | partial | plugin | **partial** (deterministic QA: doctype, landmarks, single H1, alt text, focus, safe links; no colour-contrast check) | P1 |
| 24 | Replay-safe builds, hashed artifacts | n/a | n/a | n/a | **have** (manifest + `production-trace verify`) | — |
| 25 | Per-job history, step trace, manual steps | version history only | version history only | revisions | **have** (`trace.json`, `history.jsonl`, `MANUAL-STEPS.md` for Webflow) | — |
| 26 | AI-proposed page from a brief | AI site builder | AI | plugins | **have** (V1B adapter, grounded, shadow-gated; registry includes pricing + FAQ) | — |

## Implemented in this round (recipe `landing-template-v3`)

1. **Pricing and FAQ sections** — new registered sections in `PageRequest`
   (`pricing`: 1–3 plans with name, price text, optional description and up to
   8 features; `faq`: up to 8 question/answer pairs). The renderer places them
   between features and the CTA; FAQ uses native `<details>`/`<summary>`
   (keyboard-accessible, no JavaScript). The V1B adapter registry
   (`SECTION_TYPES`) includes both. Plan names and prices must appear verbatim
   in the brief, and FAQ answers may not add numbers, prices, contacts, or
   claims the brief does not state.
2. **SEO + OpenGraph meta** — `og:type`, `og:title`, `og:description`,
   `og:locale`, `twitter:card` derived from the page title/description/language.
   QA now fails a page without a `<title>`, meta description, or any of these.
3. **Responsive breakpoint check** — named breakpoints
   (`render.BREAKPOINTS`: tablet 991px, mobile 767px — Webflow's own values,
   so the manual guide is exact). QA fails when a breakpoint block is missing.

Also fixed: a schema-invalid request used to return a pydantic docs URL
fragment as `error_code`. It now returns `invalid_request`.

## Roadmap (prioritised, not implemented)

- **P1 Testimonials** — section with a quote, a name, and a role. The adapter
  must require each quote verbatim in the brief, or the engine must take it
  from a structured source only.
- **P1 Colour-contrast QA** — WCAG AA contrast between foreground/background,
  white on primary, and button colours. Fully deterministic and cheap.
- **P1 Lead form** — needs a submission target. Out of scope while V1A has no
  backend. Until then, keep `mailto:`.
- **P2 og:image, canonical URL, sitemap/robots** — all need a configured site
  base URL in the request (`site_url`), validated as https on an allowlisted
  domain.
- **P2 JSON-LD (Organization/Product/FAQPage)** — needs a QA exception for
  `application/ld+json` only, with escaped data and no executable script.
- **P2 Custom fonts, gallery, multi-page navigation.**
- **P3 CMS/blog, animations, hosting** — not planned for a static,
  deterministic engine.
