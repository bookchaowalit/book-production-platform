# Graphics V1A golden review set

The benchmark fixture is synthetic and contains 30 outputs across the three
fixed templates and the square, portrait, and story recipes. After running the
benchmark, inspect the generated PNG files in the output directory and compare
the SVG and PNG pairs for the following visual invariants:

- text remains inside the canvas and Thai glyphs are present;
- the accent bar, background, and CTA keep the brand colors;
- portrait and story outputs preserve the declared dimensions;
- the product template remains readable when no optional image is supplied.

The SVG is the editable source of truth. New visual cases should be added to
the fixture or documented here before a recipe changes.
