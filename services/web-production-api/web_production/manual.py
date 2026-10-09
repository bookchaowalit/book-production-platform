"""Manual-reproduction guide for Web V1A: build the same landing page by hand in Webflow.

The guide is derived from the validated ``PageIR`` and mirrors
:func:`web_production.render.render_html` / :func:`render_css` exactly: the
same element tree (Body ▸ Header.hero ▸ Div.container ▸ …), the same copy and
hrefs, the same theme colours, font stack, spacing, and the same two
breakpoints (Tablet ≤991px, Mobile landscape ≤767px — chosen to match
Webflow's built-in breakpoints). ``color-mix()`` values are pre-computed so a
person can type them into a colour picker. An alternative path for plain
HTML/CSS in VS Code is listed at the end; it reproduces the engine's files
byte for byte when the layer table is followed.
"""

from __future__ import annotations

from dataclasses import dataclass

from production_trace import ManualGuide
from web_ir import LinkSpec, PageIR

from .render import (
    BREAKPOINTS,
    CONTAINER_MAX_PX,
    FOCUS_RING,
    FONT_STACK,
    OG_LOCALES,
    ON_PRIMARY,
    pricing_columns,
    section_labels,
)

PROGRAM = "Webflow"
DESIGNER = "Webflow Designer"


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def mix_hex(first: str, weight: float, second: str) -> str:
    """``color-mix(in srgb, first weight, second)`` as a hex value (what a colour picker needs)."""

    channels = [round(a * weight + b * (1 - weight)) for a, b in zip(_rgb(first), _rgb(second), strict=True)]
    return "#" + "".join(f"{value:02X}" for value in channels)


def alpha_rgba(color: str, alpha: float) -> str:
    """``color-mix(in srgb, color N%, transparent)`` is the same colour at alpha N%."""

    red, green, blue = _rgb(color)
    return f"rgba({red}, {green}, {blue}, {alpha:g})"


@dataclass
class _Element:
    path: str
    element: str
    tag: str
    text: str = ""
    note: str = ""


def _elements(ir: PageIR, image_name: str | None) -> list[_Element]:
    """The page tree in render order (same as ``render_html``)."""

    labels = section_labels(ir.language)
    hero = "Body ▸ Header.hero ▸ Div.container"
    items = [
        _Element("Body ▸ Header.hero", "Div Block (Tag: Header)", "header.hero"),
        _Element(hero, "Div Block", "div.container"),
        _Element(f"{hero} ▸ Paragraph.eyebrow", "Paragraph", "p.eyebrow", ir.title),
        _Element(f"{hero} ▸ Heading 1", "Heading (H1)", "h1", ir.hero_heading),
        _Element(f"{hero} ▸ Paragraph.hero-body", "Paragraph", "p.hero-body", ir.hero_body),
        _Element(f"{hero} ▸ p ▸ Button", "Custom Element (p) ▸ Button", "p > a.button", ir.hero_cta.label, f"href={ir.hero_cta.href}"),
    ]
    if ir.hero_image is not None:
        items.append(_Element(f"{hero} ▸ Image.hero-image", "Image", "img.hero-image", image_name or ir.hero_image.filename, f"alt={ir.hero_image.alt}"))
    main = "Body ▸ Main#content"
    items.append(_Element(main, "Div Block (Tag: Main)", "main#content"))
    features = f"{main} ▸ Section.container.features"
    items += [
        _Element(features, "Section", "section.features.container", note="aria-labelledby=features-title"),
        _Element(f"{features} ▸ Heading 2#features-title", "Heading (H2)", "h2#features-title", labels["features"]),
        _Element(f"{features} ▸ Div.feature-grid", "Div Block", "div.feature-grid"),
    ]
    for index, feature in enumerate(ir.features, start=1):
        card = f"{features} ▸ Div.feature-grid ▸ Article.feature-card[{index}]"
        items += [
            _Element(card, "Div Block (Tag: Article)", "article.feature-card"),
            _Element(f"{card} ▸ Heading 3", "Heading (H3)", "h3", feature.title),
            _Element(f"{card} ▸ Paragraph", "Paragraph", "p", feature.body),
        ]
    if ir.pricing:
        pricing = f"{main} ▸ Section.container.pricing#pricing"
        items += [
            _Element(pricing, "Section", "section.pricing.container#pricing", note="aria-labelledby=pricing-title"),
            _Element(f"{pricing} ▸ Heading 2#pricing-title", "Heading (H2)", "h2#pricing-title", labels["pricing"]),
            _Element(f"{pricing} ▸ Div.pricing-grid", "Div Block", "div.pricing-grid"),
        ]
        for index, plan in enumerate(ir.pricing, start=1):
            card = f"{pricing} ▸ Div.pricing-grid ▸ Article.pricing-card[{index}]"
            items += [
                _Element(card, "Div Block (Tag: Article)", "article.pricing-card"),
                _Element(f"{card} ▸ Heading 3", "Heading (H3)", "h3", plan.name),
                _Element(f"{card} ▸ Paragraph.price", "Paragraph", "p.price", plan.price),
            ]
            if plan.description:
                items.append(_Element(f"{card} ▸ Paragraph", "Paragraph", "p", plan.description))
            if plan.features:
                items.append(_Element(f"{card} ▸ List", "List (Unordered)", "ul"))
                items += [_Element(f"{card} ▸ List ▸ List Item[{number}]", "List Item", "li", item) for number, item in enumerate(plan.features, start=1)]
    if ir.faq:
        faq = f"{main} ▸ Section.container.faq#faq"
        items += [
            _Element(faq, "Section", "section.faq.container#faq", note="aria-labelledby=faq-title"),
            _Element(f"{faq} ▸ Heading 2#faq-title", "Heading (H2)", "h2#faq-title", labels["faq"]),
        ]
        for index, entry in enumerate(ir.faq, start=1):
            item = f"{faq} ▸ details.faq-item[{index}]"
            items += [
                _Element(item, "Custom Element (details)", "details.faq-item"),
                _Element(f"{item} ▸ summary", "Custom Element (summary)", "summary", entry.question),
                _Element(f"{item} ▸ Paragraph", "Paragraph", "p", entry.answer),
            ]
    cta = f"{main} ▸ Section.container.cta#signup"
    items += [
        _Element(cta, "Section", "section.cta.container#signup", note="aria-labelledby=cta-title"),
        _Element(f"{cta} ▸ Heading 2#cta-title", "Heading (H2)", "h2#cta-title", labels["ready"]),
        _Element(f"{cta} ▸ p ▸ Button", "Custom Element (p) ▸ Button", "p > a.button", ir.hero_cta.label, f"href={ir.hero_cta.href}"),
    ]
    if ir.secondary_cta is not None:
        items.append(_Element(f"{cta} ▸ p.secondary-cta ▸ Button", "Custom Element (p) ▸ Button", "p.secondary-cta > a.button", ir.secondary_cta.label, f"href={ir.secondary_cta.href}"))
    items += [
        _Element("Body ▸ Footer.footer", "Div Block (Tag: Footer)", "footer.footer"),
        _Element("Body ▸ Footer.footer ▸ Div.container", "Div Block", "div.container", ir.footer_text),
    ]
    return items


def _button_steps(guide: ManualGuide, link: LinkSpec, *, section: str, wrapper_class: str = "", hero: bool = False) -> None:
    guide.step(
        "ลาก Custom Element มาวาง แล้วตั้ง Tag เป็น p" + (f" และใส่ class `{wrapper_class}`" if wrapper_class else ""),
        tool="Add panel (A) ▸ Advanced ▸ Custom Element; Settings panel (D) ▸ Tag",
        value="p",
        section=section,
    )
    guide.step("ลาก Button เข้าไปใน p นั้น แล้วพิมพ์ข้อความปุ่ม", tool="Add panel (A) ▸ Basic ▸ Button", target="ใน Custom Element p", value=link.label, section=section)
    guide.step("ตั้งลิงก์ของปุ่ม (URL แบบ relative/fragment/mailto เท่านั้น)", tool="Settings panel (D) ▸ Link Settings ▸ URL", value=link.href, section=section)
    guide.step(
        "ใส่ class `button`" + (" + combo class `is-hero` (แทน selector `.hero .button`)" if hero else ""),
        tool="Style panel (S) ▸ Selector",
        value="button" + (" is-hero" if hero else ""),
        section=section,
    )


def build_webflow_guide(ir: PageIR, *, html_name: str, css_name: str, image_name: str | None = None) -> ManualGuide:
    """Translate a validated ``PageIR`` into a Webflow Navigator tree and click-level steps."""

    theme = ir.theme
    border = alpha_rgba(theme.foreground, 0.18)
    cta_background = mix_hex(theme.primary, 0.12, theme.background)
    labels = section_labels(ir.language)
    tablet, mobile = (width for _, width in BREAKPOINTS)
    elements = _elements(ir, image_name)
    guide = ManualGuide(
        program=PROGRAM,
        program_version="Designer (2026)",
        alternatives=["Framer", "WordPress (Gutenberg)", "VS Code — HTML/CSS ล้วน (ได้ไฟล์ตรงกับเครื่องมือทุกไบต์)"],
        title=ir.title,
        summary=(
            f"ทำตามขั้นตอนนี้ใน {DESIGNER} จะได้หน้า landing ที่มีโครงสร้าง ข้อความ ลิงก์ สี ระยะห่าง และ breakpoint "
            f"เหมือน `{html_name}` + `{css_name}` ที่เครื่องมือสร้าง; Webflow จะเติม class `w-…` ของตัวเองลงไป "
            "จึงเทียบกันที่โครงสร้าง/ข้อความ/สไตล์ ไม่ใช่ทีละไบต์"
        ),
        estimated_minutes=float(
            25 + 3 * len(ir.features) + 5 * len(ir.pricing) + 2 * len(ir.faq) + (5 if ir.hero_image else 0) + (2 if ir.secondary_cta else 0)
        ),
        setup=[
            "เข้า Webflow Dashboard ▸ `New site` ▸ `Blank site` แล้วเปิด Designer",
            f"เตรียมไฟล์จากเครื่องมือไว้เทียบ: `{html_name}`, `{css_name}`" + (f", `{image_name}`" if image_name else ""),
            "หน่วยในขั้นตอน: 1rem = 16px (ค่า default ของเบราว์เซอร์และ Webflow)",
            "เปิด Navigator (Z) ค้างไว้ทางซ้าย เพื่อเทียบ layer กับตารางในข้อ 2",
        ],
    )

    for element in elements:
        guide.layer(element.path, element.element, content=element.text, position=" ".join(part for part in (element.tag, element.note) if part))

    s = "ตั้งค่าหน้าและ SEO"
    guide.step("เปิด Page settings ของหน้า Home", tool="Pages panel (P) ▸ Home ▸ ⚙ Settings", section=s)
    guide.step("ตั้ง slug ของหน้า (ถ้าไม่ใช่หน้า Home)", tool="Page settings ▸ Slug", value=ir.slug, section=s)
    guide.step("ใส่ Title Tag", tool="Page settings ▸ SEO Settings ▸ Title Tag", value=ir.title, section=s)
    guide.step("ใส่ Meta Description", tool="Page settings ▸ SEO Settings ▸ Meta Description", value=ir.description, section=s)
    guide.step("ใส่ Open Graph Title (ติ๊ก Same as SEO Title ได้ เพราะค่าเท่ากัน)", tool="Page settings ▸ Open Graph Settings ▸ Open Graph Title", value=ir.title, section=s)
    guide.step("ใส่ Open Graph Description", tool="Page settings ▸ Open Graph Settings ▸ Open Graph Description", value=ir.description, section=s)
    guide.step(
        "ตั้งภาษาของหน้า (attribute lang) และ og:locale",
        tool="Site settings ▸ Localization ▸ Primary locale",
        value=f"lang={ir.language}, og:locale={OG_LOCALES[ir.language]}",
        note="Webflow ใส่ og:type=website และ twitter:card ให้เองเมื่อ publish; ถ้าไม่ใส่ ให้เพิ่มใน Page settings ▸ Custom Code ▸ Inside <head> tag",
        section=s,
    )
    guide.step("ไม่ใส่ Open Graph Image (เครื่องมือไม่ใส่ og:image เพราะต้องเป็น URL เต็มของโดเมนจริง)", tool="Page settings ▸ Open Graph Settings ▸ Open Graph Image URL", value="(เว้นว่าง)", section=s)

    s = "ธีม สี และฟอนต์"
    for name, value in (("primary", theme.primary), ("background", theme.background), ("foreground", theme.foreground)):
        guide.step(f"สร้างตัวแปรสี `{name}`", tool="Variables panel ▸ + New ▸ Color", target=name, value=value, section=s)
    guide.step("เลือก Body แล้วตั้งพื้นหลัง/สีตัวอักษร", tool="Navigator ▸ Body; Style panel ▸ Backgrounds ▸ Color / Typography ▸ Color", value="background / foreground (ตัวแปร)", section=s)
    guide.step("ตั้งฟอนต์และระยะบรรทัดของ Body", tool="Style panel ▸ Typography ▸ Font / Height", value=f"{FONT_STACK}; line-height 1.6", note="ถ้า dropdown ไม่มี system-ui ให้พิมพ์ค่า font-family ใน custom property หรือใช้ฟอนต์ระบบที่ใกล้ที่สุด", section=s)
    guide.step("ตั้ง margin ของ Body เป็น 0", tool="Style panel ▸ Spacing ▸ Margin", value="0", section=s)

    s = "ส่วน Hero"
    guide.step("ลาก Div Block มาไว้บนสุดใน Body แล้วเปลี่ยน Tag เป็น Header", tool="Add panel (A) ▸ Layout ▸ Div Block; Settings panel (D) ▸ Tag", value="Header", section=s)
    guide.step("ใส่ class `hero` แล้วตั้งสไตล์", tool="Style panel ▸ Selector / Backgrounds / Typography / Spacing", value=f"class hero; background primary ({theme.primary}); color {ON_PRIMARY}; padding 5rem (80px) บน-ล่าง, 0 ซ้าย-ขวา", section=s)
    guide.step(
        "ลาก Div Block เข้าไปใน Header แล้วใส่ class `container`",
        tool="Add panel ▸ Div Block; Style panel ▸ Size / Spacing",
        value=f"class container; Width calc(100% - 2rem); Max W {CONTAINER_MAX_PX}px; Margin ซ้าย-ขวา Auto",
        note=f"เท่ากับ CSS `width: min({CONTAINER_MAX_PX}px, calc(100% - 2rem))`",
        section=s,
    )
    guide.step("ลาก Paragraph เข้า container พิมพ์ชื่อหน้า แล้วใส่ class `eyebrow`", tool="Add panel ▸ Typography ▸ Paragraph", value=ir.title, note="สไตล์ eyebrow: Weight 700, Letter spacing 0.08em, Capitalize = Uppercase (AA)", section=s)
    guide.step("ลาก Heading มาใต้ eyebrow ตั้งเป็น H1 แล้วพิมพ์หัวข้อ", tool="Add panel ▸ Typography ▸ Heading; Settings ▸ H1", value=ir.hero_heading, section=s)
    guide.step(
        "ตั้งสไตล์ All H1 Headings (มี H1 เดียวในหน้า จึงเท่ากับ `.hero h1`)",
        tool="Style panel ▸ Selector ▸ All H1 Headings",
        value="Max W 16ch; Font size clamp(2.25rem, 7vw, 5rem); Height 1.05; Margin 0.25rem 0 1rem",
        section=s,
    )
    guide.step("ลาก Paragraph มาใต้ H1 พิมพ์เนื้อหา แล้วใส่ class `hero-body`", tool="Add panel ▸ Typography ▸ Paragraph", value=ir.hero_body, note="สไตล์ hero-body: Max W 60ch; Font size 1.125rem (18px)", section=s)
    _button_steps(guide, ir.hero_cta, section=s, hero=True)
    guide.step(
        "ตั้งสไตล์ class `button` (ใช้ซ้ำทุกปุ่ม)",
        tool="Style panel ▸ .button",
        value="Display Inline-block; Radius 0.5rem; Background foreground; Color #FFFFFF; Padding 0.75rem 1.1rem; Weight 700; Decoration none",
        section=s,
    )
    guide.step("ตั้งสไตล์ combo `.button.is-hero` (ปุ่มบนพื้น primary)", tool="Style panel ▸ .button.is-hero", value=f"Background {ON_PRIMARY}; Color foreground ({theme.foreground})", section=s)
    guide.step(
        "ตั้งกรอบโฟกัสคีย์บอร์ดของปุ่ม",
        tool="Style panel ▸ .button ▸ States ▸ Focused (keyboard)",
        value=f"Outline 0.2rem solid {FOCUS_RING}; Outline offset 0.2rem",
        section=s,
    )
    if ir.hero_image is not None:
        guide.step(
            "ลาก Image มาใต้ปุ่ม แล้วอัปโหลดรูปจากเครื่องมือ",
            tool="Add panel ▸ Media ▸ Image; Assets panel (J) ▸ Upload",
            value=image_name or ir.hero_image.filename,
            section=s,
        )
        guide.step("ใส่ Alt text ของรูป (ห้ามเว้นว่าง)", tool="Settings panel (D) ▸ Image Settings ▸ Alt Text", value=ir.hero_image.alt, section=s)
        guide.step("ใส่ class `hero-image` แล้วตั้งสไตล์", tool="Style panel ▸ .hero-image", value="Display Block; Width 100%; Max W 480px; Height Auto; Margin top 2rem; Radius 12px", section=s)

    s = "ส่วน Main และจุดเด่น (Features)"
    guide.step("ลาก Div Block มาใต้ Header แล้วตั้ง Tag เป็น Main และ ID เป็น content", tool="Add panel ▸ Div Block; Settings panel ▸ Tag / ID", value="Tag Main; ID content", section=s)
    guide.step(
        "ลาก Section เข้า Main ใส่ class `container` แล้ว combo class `features`",
        tool="Add panel ▸ Layout ▸ Section; Style panel ▸ Selector",
        value="container features; padding 4rem (64px) บน-ล่าง",
        note="Webflow ทำ `.container.features` เป็น combo class — ได้ผลเท่ากับ CSS ของเครื่องมือ",
        section=s,
    )
    guide.step("ตั้ง attribute การเข้าถึงของ Section", tool="Settings panel ▸ Custom Attributes ▸ +", value="aria-labelledby = features-title", section=s)
    guide.step("ลาก Heading (H2) เข้า Section ตั้ง ID แล้วพิมพ์หัวข้อ", tool="Add panel ▸ Heading; Settings ▸ H2 / ID", value=f"ID features-title; ข้อความ: {labels['features']}", section=s)
    guide.step("ลาก Div Block มาใต้ H2 ใส่ class `feature-grid`", tool="Style panel ▸ Layout ▸ Display Grid", value="Grid 2 คอลัมน์ (1fr 1fr); Gap 1rem", section=s)
    for index, feature in enumerate(ir.features, start=1):
        guide.step(f"ลาก Div Block (Tag Article) เข้า feature-grid เป็นการ์ดที่ {index} ใส่ class `feature-card`", tool="Add panel ▸ Div Block; Settings ▸ Tag Article", value="feature-card", section=s)
        guide.step(f"ลาก Heading (H3) เข้าการ์ดที่ {index} แล้วพิมพ์ชื่อจุดเด่น", tool="Add panel ▸ Heading; Settings ▸ H3", value=feature.title, section=s)
        guide.step(f"ลาก Paragraph มาใต้ H3 ของการ์ดที่ {index} แล้วพิมพ์รายละเอียด", tool="Add panel ▸ Paragraph", value=feature.body, section=s)
    guide.step("ตั้งสไตล์ class `feature-card`", tool="Style panel ▸ .feature-card", value=f"Border 1px solid {border}; Radius 0.75rem; Padding 1.25rem", note="สีขอบ = color-mix(foreground 18%, transparent)", section=s)

    if ir.pricing:
        s = "ส่วนราคา (Pricing)"
        guide.step("ลาก Section มาใต้ Features ใส่ class `container` + combo `pricing` และ ID", tool="Add panel ▸ Section; Settings ▸ ID", value="container pricing; ID pricing; padding 0 บน, 4rem ล่าง", section=s)
        guide.step("ตั้ง attribute การเข้าถึง", tool="Settings panel ▸ Custom Attributes ▸ +", value="aria-labelledby = pricing-title", section=s)
        guide.step("ลาก Heading (H2) ตั้ง ID แล้วพิมพ์หัวข้อ", tool="Add panel ▸ Heading; Settings ▸ H2 / ID", value=f"ID pricing-title; ข้อความ: {labels['pricing']}", section=s)
        guide.step(
            "ลาก Div Block ใส่ class `pricing-grid`",
            tool="Style panel ▸ Layout ▸ Display Grid",
            value=f"Grid {pricing_columns(ir)} คอลัมน์ เท่ากัน; Gap 1rem",
            section=s,
        )
        for index, plan in enumerate(ir.pricing, start=1):
            guide.step(f"ลาก Div Block (Tag Article) เป็นแพ็กเกจที่ {index} ใส่ class `pricing-card`", tool="Add panel ▸ Div Block; Settings ▸ Tag Article", value="pricing-card", section=s)
            guide.step(f"ลาก Heading (H3) แล้วพิมพ์ชื่อแพ็กเกจที่ {index}", tool="Add panel ▸ Heading; Settings ▸ H3", value=plan.name, section=s)
            guide.step("ลาก Paragraph ใส่ class `price` แล้วพิมพ์ราคาตามที่เครื่องมือแสดง (อย่าคำนวณใหม่)", tool="Add panel ▸ Paragraph", value=plan.price, section=s)
            if plan.description:
                guide.step(f"ลาก Paragraph แล้วพิมพ์คำอธิบายแพ็กเกจที่ {index}", tool="Add panel ▸ Paragraph", value=plan.description, section=s)
            if plan.features:
                guide.step(f"ลาก List (Unordered) เข้าแพ็กเกจที่ {index}", tool="Add panel ▸ Typography ▸ List", value=f"{len(plan.features)} รายการ", section=s)
                for item in plan.features:
                    guide.step("พิมพ์ List Item", tool="Navigator ▸ List ▸ List Item (+)", value=item, section=s)
        guide.step("ตั้งสไตล์ `pricing-card` และ `price`", tool="Style panel ▸ .pricing-card / .price", value=f"การ์ด: Border 1px solid {border}; Radius 0.75rem; Padding 1.5rem. ราคา: Size 2rem; Weight 700; Margin 0.25rem 0 0.75rem", section=s)

    if ir.faq:
        s = "ส่วนคำถามที่พบบ่อย (FAQ)"
        guide.step("ลาก Section มาใต้ส่วนก่อนหน้า ใส่ class `container` + combo `faq` และ ID", tool="Add panel ▸ Section; Settings ▸ ID", value="container faq; ID faq; padding 0 บน, 4rem ล่าง", section=s)
        guide.step("ตั้ง attribute การเข้าถึง", tool="Settings panel ▸ Custom Attributes ▸ +", value="aria-labelledby = faq-title", section=s)
        guide.step("ลาก Heading (H2) ตั้ง ID แล้วพิมพ์หัวข้อ", tool="Add panel ▸ Heading; Settings ▸ H2 / ID", value=f"ID faq-title; ข้อความ: {labels['faq']}", section=s)
        for index, entry in enumerate(ir.faq, start=1):
            guide.step(f"ลาก Custom Element ตั้ง Tag `details` ใส่ class `faq-item` (คำถามที่ {index})", tool="Add panel ▸ Advanced ▸ Custom Element; Settings ▸ Tag", value="details", section=s)
            guide.step(f"ลาก Custom Element Tag `summary` เข้า details แล้วพิมพ์คำถามที่ {index}", tool="Add panel ▸ Advanced ▸ Custom Element; Settings ▸ Tag", value=entry.question, note="ใส่ class `faq-question` แทน selector `.faq-item summary`", section=s)
            guide.step(f"ลาก Paragraph มาใต้ summary แล้วพิมพ์คำตอบที่ {index}", tool="Add panel ▸ Paragraph", value=entry.answer, section=s)
        guide.step("ตั้งสไตล์ FAQ", tool="Style panel ▸ .faq-item / .faq-question", value=f"faq-item: Border bottom 1px solid {border}; Padding 1rem 0. faq-question: Cursor pointer; Weight 700; State Focused (keyboard): Outline 0.2rem solid {FOCUS_RING}, offset 0.2rem", section=s)

    s = "ส่วนชวนลงมือ (CTA) และ Footer"
    guide.step("ลาก Section มาท้าย Main ใส่ class `container` + combo `cta` และ ID", tool="Add panel ▸ Section; Settings ▸ ID", value="container cta; ID signup", section=s)
    guide.step("ตั้ง attribute การเข้าถึง", tool="Settings panel ▸ Custom Attributes ▸ +", value="aria-labelledby = cta-title", section=s)
    guide.step(
        "ตั้งสไตล์ `.container.cta`",
        tool="Style panel ▸ Backgrounds / Borders / Spacing",
        value=f"Background {cta_background}; Radius 0.75rem; Padding 2rem; Margin bottom 4rem",
        note="พื้นหลัง = color-mix(primary 12%, background) คำนวณไว้แล้ว",
        section=s,
    )
    guide.step("ลาก Heading (H2) ตั้ง ID แล้วพิมพ์หัวข้อ", tool="Add panel ▸ Heading; Settings ▸ H2 / ID", value=f"ID cta-title; ข้อความ: {labels['ready']}", section=s)
    _button_steps(guide, ir.hero_cta, section=s)
    if ir.secondary_cta is not None:
        _button_steps(guide, ir.secondary_cta, section=s, wrapper_class="secondary-cta")
        guide.step("ตั้งสไตล์ `secondary-cta`", tool="Style panel ▸ .secondary-cta", value="Margin top 1rem", section=s)
    guide.step("ลาก Div Block มาใต้ Main ตั้ง Tag Footer ใส่ class `footer`", tool="Add panel ▸ Div Block; Settings ▸ Tag Footer", value=f"Border top 1px solid {border}; Padding 2rem 0", section=s)
    guide.step("ลาก Div Block class `container` เข้า Footer แล้วพิมพ์ข้อความท้ายหน้า", tool="Add panel ▸ Div Block (+ Text)", value=ir.footer_text or "(เว้นว่าง)", section=s)

    s = "Responsive (breakpoint)"
    guide.step(f"สลับไป breakpoint Tablet (≤{tablet}px)", tool="Top bar ▸ Breakpoints ▸ Tablet", value=f"{tablet}px", section=s)
    guide.step("ตั้ง padding ของ `.hero` ที่ Tablet", tool="Style panel ▸ .hero ▸ Spacing", value="4rem บน-ล่าง", section=s)
    if ir.pricing:
        guide.step("ตั้ง `.pricing-grid` เป็น 1 คอลัมน์ที่ Tablet", tool="Style panel ▸ .pricing-grid ▸ Grid", value="1 คอลัมน์", section=s)
    guide.step(f"สลับไป breakpoint Mobile landscape (≤{mobile}px)", tool="Top bar ▸ Breakpoints ▸ Mobile landscape", value=f"{mobile}px", section=s)
    guide.step("ตั้ง padding ของ `.hero` ที่ Mobile", tool="Style panel ▸ .hero ▸ Spacing", value="3rem บน-ล่าง", section=s)
    guide.step("ตั้ง `.feature-grid` เป็น 1 คอลัมน์ที่ Mobile", tool="Style panel ▸ .feature-grid ▸ Grid", value="1 คอลัมน์", section=s)
    guide.step("ตรวจที่ Mobile portrait ว่าไม่มีอะไรล้นจอ", tool="Top bar ▸ Breakpoints ▸ Mobile portrait", value="478px", section=s)

    s = "ตรวจการเข้าถึงและเผยแพร่"
    guide.step("ตรวจหน้าเว็บด้วย Audit panel ของ Webflow แล้วแก้ทุกข้อ", tool="Audit panel (Shift+A)", value="0 issues", section=s)
    guide.step("ห้ามเพิ่ม Embed/script หรือ asset จาก URL ภายนอก", tool="(ไม่มี)", note="QA ของเครื่องมือปฏิเสธ <script>, href แบบ http(s)/javascript:/data:, url() และ @import", section=s)
    guide.step("เผยแพร่ หรือ Export ไฟล์มาเทียบ", tool="Publish ▸ Publish to selected domains  (หรือ  Export code ▸ Download ZIP)", value=html_name, section=s)

    s = "ทางเลือก: HTML/CSS ล้วนใน VS Code"
    guide.step("สร้างโฟลเดอร์ใหม่ แล้วสร้างไฟล์ HTML และ CSS ชื่อเดียวกับเครื่องมือ", tool="VS Code ▸ File ▸ New File…", value=f"{html_name}, {css_name}", section=s)
    guide.step("พิมพ์ <head> ตามลำดับ: charset, viewport, title, description, og:type, og:title, og:description, og:locale, twitter:card, link stylesheet", tool="Editor", value=f'<link rel="stylesheet" href="{css_name}">', section=s)
    guide.step("พิมพ์ <body> ตามตารางในข้อ 2 ทีละแถว (คอลัมน์ ตำแหน่ง = tag.class, เนื้อหา = ข้อความ)", tool="Editor", value=f"{len(elements)} element", section=s)
    guide.step("คัดลอกค่าทุกบรรทัดของ CSS จากขั้นตอนด้านบน (หรือเปิด CSS ของเครื่องมือเทียบ) แล้วบันทึก", tool="File ▸ Save", value=css_name, section=s)

    image_check = [f"มีรูป `{image_name or ir.hero_image.filename}` พร้อม alt ไม่ว่าง และไม่มีรูปจาก URL ภายนอก"] if ir.hero_image is not None else ["ไม่มีรูปภาพ (IR ไม่มี hero_image)"]
    guide.checks = [
        f"มี H1 เดียว ข้อความ: {ir.hero_heading}",
        f"มี H2 ครบ {2 + bool(ir.pricing) + bool(ir.faq)} หัวข้อตามลำดับ: " + ", ".join(
            [labels["features"], *([labels["pricing"]] if ir.pricing else []), *([labels["faq"]] if ir.faq else []), labels["ready"]]
        ),
        f"การ์ดจุดเด่น {len(ir.features)} ใบ" + (f", แพ็กเกจราคา {len(ir.pricing)} ใบ" if ir.pricing else "") + (f", คำถาม FAQ {len(ir.faq)} ข้อ" if ir.faq else ""),
        "ทุกปุ่มมี href ตรงกับเครื่องมือ: " + ", ".join(sorted({ir.hero_cta.href, *([ir.secondary_cta.href] if ir.secondary_cta else [])})),
        f"<title> และ og:title = {ir.title}; meta description และ og:description = {ir.description}",
        f"html lang = {ir.language}; og:locale = {OG_LOCALES[ir.language]}; มี twitter:card = summary",
        *image_check,
        f"ที่ ≤{tablet}px hero padding 4rem" + (" และ pricing เป็น 1 คอลัมน์" if ir.pricing else "") + f"; ที่ ≤{mobile}px features เป็น 1 คอลัมน์และ hero padding 3rem",
        "กด Tab ไล่ปุ่มแล้วเห็นกรอบโฟกัสสีเหลือง (#FBBF24) ทุกปุ่ม",
        "ไม่มี <script>, ไม่มีลิงก์ http(s)/javascript:/data:, ไม่มี url()/@import ใน CSS (ตรงกับ QA ของเครื่องมือ)",
        "เปิดไฟล์ HTML ของเครื่องมือในเบราว์เซอร์คู่กัน แล้วเทียบข้อความทุกส่วนตามตารางข้อ 2",
    ]
    guide.glossary = {
        "Navigator": "แผงต้นไม้ layer ของหน้า (กด Z)",
        "Combo class": "class ที่ซ้อนบน class หลัก เช่น `.container.features`",
        "Custom Element": "element ที่กำหนด tag เองได้ (p, details, summary)",
        "Focused (keyboard)": "สถานะ :focus-visible ใน Webflow",
    }
    return guide


__all__ = ["alpha_rgba", "build_webflow_guide", "mix_hex"]
