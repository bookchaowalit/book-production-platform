"""The design principles every AI step of the graphics engine works from (compact runtime brief).

The full doctrine, with reasons, examples, and the human critique checklist,
is the Solo Empire ``graphic-design`` skill (``skills/graphic-design/``). This
module is its compact runtime form: short enough for every model call, and the
same rules the deterministic checks (:mod:`.layouts`) and the design score
(:mod:`.aesthetics`) enforce, so the model aims at what will be accepted.
Change both together.
"""

from __future__ import annotations

PRINCIPLES: tuple[tuple[str, str], ...] = (
    ("message", ("One message and one action per graphic; the viewer gets it in about three seconds. Design the read "
                "order: hero, headline, proof (price/date/offer), action, small print.")),
    ("hierarchy", ("Exactly one dominant element. Headline 1.5-4x the next text and 2-6x the median text; at most three "
                  "type levels. Energy comes from scale contrast, not from more elements; accent one key word instead "
                  "of enlarging a whole line.")),
    ("typography", ("Body and labels never below 1.6% of the short side; 20-40 characters per line. One display face plus "
                   "one text face. Thai: no uppercase or wide tracking, line height at least 1.15, never break or crop "
                   "a Thai word (bleed only short Latin words or numbers).")),
    ("color", ("Text contrast 4.5:1 on its real backdrop (3:1 for large text and button labels). About 60% dominant, 30% "
              "secondary, 10% accent; 2-5 solid colors. Brand colors that fail contrast go on shapes, not text.")),
    ("composition", ("Snap to a grid with one or two text alignment axes. Margins at least 3.5% of the short side. 30-70% "
                    "of the canvas covered; space around the hero makes it the hero; no empty slab without a role. "
                    "Balanced, or asymmetric with a counterweight. Related items close, consistent gaps.")),
    ("photos", ("One focal subject, cropped to it (focus on face or product). Cut-outs shown whole; framed photos cover-fit. "
               "Never put a photo or opaque shape over text; use a fade or panel to set text on a photo.")),
    ("depth", ("Layers: background, decoration, photo, text, foreground accents. Only a giant decorative word may sit behind "
              "a subject. Textures stay faint; decoration lives at edges and corners, the reading area stays calm.")),
    ("formats", ("Every size gets its own arrangement. Story (1080x1920): keep all text except small print between 10.5% "
                "and 85.5% of the height, clear of the app's own UI.")),
    ("series", ("Across a client's posts keep palette, type, and photo treatment; vary the composition idea, crop, and hero. "
               "Never repeat a recent layout.")),
)


def principles_text() -> str:
    """The principles as one prompt paragraph."""

    return " ".join(f"{name.upper()}: {rule}" for name, rule in PRINCIPLES)


__all__ = ["PRINCIPLES", "principles_text"]
