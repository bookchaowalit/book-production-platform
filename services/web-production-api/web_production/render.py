"""Deterministic HTML and CSS renderer for the fixed Web V1A template.

Section order is fixed: hero, features, optional pricing, optional FAQ, call
to action, footer. The head carries SEO and OpenGraph/Twitter meta derived
from the page title and description. Responsive overrides follow the named
``BREAKPOINTS`` (they match Webflow's Tablet and Mobile landscape
breakpoints, so the manual guide reproduces them exactly); QA checks that
every breakpoint is present.
"""

from __future__ import annotations

from html import escape

from web_ir import LinkSpec, PageIR

# (name, max-width px) — the CSS emits one ``@media (max-width: Npx)`` block per entry.
BREAKPOINTS: tuple[tuple[str, int], ...] = (("tablet", 991), ("mobile", 767))
OG_LOCALES = {"th": "th_TH", "en": "en_US"}
ON_PRIMARY = "#FFFFFF"
FOCUS_RING = "#FBBF24"
FONT_STACK = "system-ui, sans-serif"
CONTAINER_MAX_PX = 1120


def section_labels(language: str) -> dict[str, str]:
    """Fixed section headings the template prints (not part of the request)."""

    english = language == "en"
    return {
        "features": "Features" if english else "จุดเด่น",
        "pricing": "Pricing" if english else "ราคา",
        "faq": "Frequently asked questions" if english else "คำถามที่พบบ่อย",
        "ready": "Ready to get started?" if english else "พร้อมเริ่มต้นหรือยัง",
    }


def _text(value: str) -> str:
    return escape(value, quote=True)


def _link(link: LinkSpec) -> str:
    return f'<a class="button" href="{_text(link.href)}">{_text(link.label)}</a>'


def _pricing(ir: PageIR, label: str) -> str:
    if not ir.pricing:
        return ""
    cards = []
    for plan in ir.pricing:
        description = f"<p>{_text(plan.description)}</p>" if plan.description else ""
        items = "".join(f"<li>{_text(item)}</li>" for item in plan.features)
        listing = f"<ul>{items}</ul>" if items else ""
        cards.append(f'<article class="pricing-card"><h3>{_text(plan.name)}</h3><p class="price">{_text(plan.price)}</p>{description}{listing}</article>')
    return (
        '\n    <section class="pricing container" id="pricing" aria-labelledby="pricing-title">'
        f'\n      <h2 id="pricing-title">{label}</h2>'
        f'\n      <div class="pricing-grid">{"".join(cards)}</div>'
        "\n    </section>"
    )


def _faq(ir: PageIR, label: str) -> str:
    if not ir.faq:
        return ""
    items = "".join(
        f'<details class="faq-item"><summary>{_text(item.question)}</summary><p>{_text(item.answer)}</p></details>'
        for item in ir.faq
    )
    return (
        '\n    <section class="faq container" id="faq" aria-labelledby="faq-title">'
        f'\n      <h2 id="faq-title">{label}</h2>'
        f"\n      {items}"
        "\n    </section>"
    )


def render_html(ir: PageIR, css_filename: str = "styles.css", hero_image_filename: str | None = None) -> str:
    """Render an escaped, semantic static document from PageIR.

    ``hero_image_filename`` is the bundle-local name of the verified hero
    image; it is required when the IR has a hero image and ignored otherwise.
    """

    features = "".join(
        f'<article class="feature-card"><h3>{_text(feature.title)}</h3><p>{_text(feature.body)}</p></article>'
        for feature in ir.features
    )
    labels = section_labels(ir.language)
    secondary = f'<p class="secondary-cta">{_link(ir.secondary_cta)}</p>' if ir.secondary_cta else ""
    hero_image = ""
    if ir.hero_image is not None:
        if not hero_image_filename:
            raise ValueError("hero_image_filename_required")
        hero_image = (
            "\n      "
            f'<img class="hero-image" src="{_text(hero_image_filename)}" alt="{_text(ir.hero_image.alt)}">'
        )
    return f'''<!doctype html>
<html lang="{ir.language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_text(ir.title)}</title>
  <meta name="description" content="{_text(ir.description)}">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{_text(ir.title)}">
  <meta property="og:description" content="{_text(ir.description)}">
  <meta property="og:locale" content="{OG_LOCALES[ir.language]}">
  <meta name="twitter:card" content="summary">
  <link rel="stylesheet" href="{_text(css_filename)}">
</head>
<body>
  <header class="hero">
    <div class="container">
      <p class="eyebrow">{_text(ir.title)}</p>
      <h1>{_text(ir.hero_heading)}</h1>
      <p class="hero-body">{_text(ir.hero_body)}</p>
      <p>{_link(ir.hero_cta)}</p>{hero_image}
    </div>
  </header>
  <main id="content">
    <section class="features container" aria-labelledby="features-title">
      <h2 id="features-title">{labels["features"]}</h2>
      <div class="feature-grid">{features}</div>
    </section>{_pricing(ir, labels["pricing"])}{_faq(ir, labels["faq"])}
    <section class="cta container" id="signup" aria-labelledby="cta-title">
      <h2 id="cta-title">{labels["ready"]}</h2>
      <p>{_link(ir.hero_cta)}</p>
      {secondary}
    </section>
  </main>
  <footer class="footer"><div class="container">{_text(ir.footer_text)}</div></footer>
</body>
</html>
'''


def pricing_columns(ir: PageIR) -> int:
    """Desktop pricing grid columns: one per plan (1-3)."""

    return max(1, len(ir.pricing))


def render_css(ir: PageIR) -> str:
    """Render local-only responsive CSS with a visible keyboard focus state."""

    theme = ir.theme
    tablet, mobile = (width for _, width in BREAKPOINTS)
    return f''' :root {{
  --primary: {theme.primary};
  --background: {theme.background};
  --foreground: {theme.foreground};
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--background); color: var(--foreground); font-family: {FONT_STACK}; line-height: 1.6; }}
.container {{ width: min({CONTAINER_MAX_PX}px, calc(100% - 2rem)); margin: 0 auto; }}
.hero {{ background: var(--primary); color: {ON_PRIMARY}; padding: 5rem 0; }}
.hero h1 {{ max-width: 16ch; font-size: clamp(2.25rem, 7vw, 5rem); line-height: 1.05; margin: .25rem 0 1rem; }}
.hero-body {{ max-width: 60ch; font-size: 1.125rem; }}
.hero-image {{ display: block; width: 100%; max-width: 480px; height: auto; margin-top: 2rem; border-radius: 12px; }}
.eyebrow {{ font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
.button {{ display: inline-block; border-radius: .5rem; background: var(--foreground); color: {ON_PRIMARY}; padding: .75rem 1.1rem; text-decoration: none; font-weight: 700; }}
.hero .button {{ background: {ON_PRIMARY}; color: var(--foreground); }}
.button:focus-visible {{ outline: .2rem solid {FOCUS_RING}; outline-offset: .2rem; }}
.features {{ padding: 4rem 0; }}
.feature-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }}
.feature-card {{ border: 1px solid color-mix(in srgb, var(--foreground) 18%, transparent); border-radius: .75rem; padding: 1.25rem; }}
.pricing {{ padding: 0 0 4rem; }}
.pricing-grid {{ display: grid; grid-template-columns: repeat({pricing_columns(ir)}, minmax(0, 1fr)); gap: 1rem; }}
.pricing-card {{ border: 1px solid color-mix(in srgb, var(--foreground) 18%, transparent); border-radius: .75rem; padding: 1.5rem; }}
.price {{ font-size: 2rem; font-weight: 700; margin: .25rem 0 .75rem; }}
.faq {{ padding: 0 0 4rem; }}
.faq-item {{ border-bottom: 1px solid color-mix(in srgb, var(--foreground) 18%, transparent); padding: 1rem 0; }}
.faq-item summary {{ cursor: pointer; font-weight: 700; }}
.faq-item summary:focus-visible {{ outline: .2rem solid {FOCUS_RING}; outline-offset: .2rem; }}
.cta {{ border-radius: .75rem; background: color-mix(in srgb, var(--primary) 12%, var(--background)); padding: 2rem; margin-bottom: 4rem; }}
.secondary-cta {{ margin-top: 1rem; }}
.footer {{ border-top: 1px solid color-mix(in srgb, var(--foreground) 18%, transparent); padding: 2rem 0; }}
@media (max-width: {tablet}px) {{
  .hero {{ padding: 4rem 0; }}
  .pricing-grid {{ grid-template-columns: 1fr; }}
}}
@media (max-width: {mobile}px) {{
  .hero {{ padding: 3rem 0; }}
  .feature-grid {{ grid-template-columns: 1fr; }}
}}
'''


__all__ = ["BREAKPOINTS", "OG_LOCALES", "pricing_columns", "render_css", "render_html", "section_labels"]
