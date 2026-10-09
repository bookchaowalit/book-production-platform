# Marketing V1A — feature parity with real programs

Compared with Meta Business Suite (Facebook / Instagram), Hootsuite (or
Buffer), and Canva. Status: **have** = deterministic today; **partial** =
narrower version; **missing** = not built; **excluded** = intentionally not
allowed in V1A (review-only: no publish, schedule, send, targeting, or spend).
Priority: P1 = next, P2 = later, P3 = out of scope locally.

Last reviewed: 2026-10-03 (recipe `campaign-package-v2`).

## Capability table

| Capability | Meta Business Suite / Hootsuite / Canva | Our status | Priority |
|---|---|---|---|
| One campaign, several channels (FB, IG, LinkedIn, email, LINE, web) | Create post (multi-placement); Hootsuite Composer | have (review-only package) | — |
| Per-channel copy and asset slots | Post details ▸ Text / Media | have | — |
| Copy length per channel | live counter; platform limits | partial (engine limits 300–1,200 chars, stricter than platforms; checked on the AI path) | P2 |
| Forbidden words, unsupported claims | Hootsuite compliance (enterprise) | partial (AI proposal path only; the structured build does not re-run them) | P1 |
| Hashtag and mention rules | IG refuses > 30 hashtags / > 20 mentions; Hootsuite hashtag suggestions | **have (v2)**: hard IG limits reject the build; duplicate / non-clickable / above-recommended flagged for review | — |
| UTM tracked links | Hootsuite UTM parameters; Google Campaign URL Builder | **have (v2)**: one link per copy / email / landing asset, fixed `utm_*` order, query and fragment kept | — |
| A/B variants | Meta Business Suite A/B test; Mailchimp A/B; Hootsuite (ads) | **have (v2)**: 2–4 labelled variants per channel + kind, listed as `experiments`; no live test or winner pick | — |
| Save as draft for approval | Save as draft; Hootsuite approvals | have (the package is the draft; manual ends every channel with "Save as draft") | — |
| Approval workflow (approve / reject / comments) | Hootsuite approvals; Meta roles | missing | P1 |
| Bulk CSV export for schedulers | Hootsuite Bulk Composer; Meta bulk upload | missing | P1 |
| Email subject / preheader / sender fields | Mailchimp campaign settings | missing (body copy only) | P1 |
| Planned date / content calendar (metadata only) | Planner; Hootsuite Planner | missing (would stay review-only) | P1 |
| Image frame sizes per channel | Canva presets; Meta crop | partial (manual names the frame per channel; engine does not resize) | P2 |
| Design templates, text layers, brand kit | Canva | missing (graphics service owns rendering) | P2 |
| Link preview / OG metadata check | Meta link preview | missing | P2 |
| More channels (X, TikTok, YouTube, Threads) | Hootsuite / Meta | missing | P2 |
| Link shortener | Ow.ly; Bitly | missing (external service) | P3 |
| Schedule / publish / send | Schedule; Publish | **excluded** | — |
| Audience targeting, boost, ad budget | Boost post; Ads Manager | **excluded** (audience rules are recorded only) | — |
| Analytics, inbox, social listening | Insights; Inbox; Streams | missing (needs platform APIs) | P3 |
| Source lineage and replay-safe packages | — | have (asset hashes, idempotent replay) | — |
| Job history, step trace, manual click-by-click guide | — | have (`jobs/<key>/trace.json`, `MANUAL-STEPS.md`, `history.jsonl`) | — |

## Implemented in this round (v2)

1. **Hashtag / mention rules** (`channel_tools.tag_checks`) — Instagram's hard
   limits (30 hashtags, 20 mentions) reject the build with
   `hashtag_limit_exceeded` / `mention_limit_exceeded`; duplicate hashtags,
   hashtags on email / LINE / web (not clickable), and more than the
   recommended count (FB 5, IG 15, LinkedIn 5) become review `checks` in the
   package. Copy is never rewritten.
2. **UTM link builder** (`CampaignRequest.utm`, `channel_tools.tracked_links`)
   — `landing_url` must be `https` without existing `utm_` parameters; each
   copy / email / landing asset gets `utm_source` / `utm_medium` for its
   channel, `utm_campaign` (slug of the campaign id or an explicit value),
   `utm_content` = asset id, optional `utm_term`. Links are a separate package
   field (`links`), so asset text and its hash stay unchanged.
3. **A/B variants** (`AssetSpec.variant`) — within one channel and kind,
   either no asset or every asset has a unique label `A`–`Z`, 2–4 per group;
   the package lists them under `experiments`, and the manual uses each
   program's A/B feature or separate drafts where none exists.

A v1 request (no `variant`, no `utm`) keeps its hash and package bytes, and v1
manifests still replay after the recipe upgrade.

## Roadmap (not done)

- **P1:** re-run length / forbidden-word / claim checks on the structured build;
  approval states; Hootsuite-compatible bulk CSV; email subject / preheader;
  planned-date metadata (still never scheduled).
- **P2:** platform-native limits next to engine limits; image resize / crop to
  the channel frame through the graphics service; link preview checks; more
  channels.
- **P3:** link shortener, analytics, inbox — need external APIs and stay out of
  the deterministic engine.

Known limits of the manual guide: menu labels follow the current web UIs
(Meta Business Suite, LinkedIn Page admin, LINE OA Manager, Mailchimp,
WordPress) and may move; Meta's A/B test and LINE's broadcast A/B appear only
for eligible accounts, so the guide falls back to one draft per variant.
