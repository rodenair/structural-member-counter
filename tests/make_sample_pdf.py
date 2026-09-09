"""
Builds a synthetic structural plan PDF to smoke-test the extractor:
  - A "plan" page with member tags scattered around (some repeated,
    plus deliberate noise like a grid label that should NOT match).
  - A "schedule" page with a real bordered table (so pdfplumber's
    line-based table detection has something to find) listing each
    tag's length/size/description.

Run: python tests/make_sample_pdf.py
Produces: tests/sample_plan.pdf
"""

import os

import pymupdf as fitz

OUT_PATH = os.path.join(os.path.dirname(__file__), "sample_plan.pdf")


def build():
    doc = fitz.open()

    # --- Page 1: "plan" with scattered tags ---
    page = doc.new_page(width=842, height=595)  # A3 landscape-ish
    page.insert_text((72, 60), "GROUND FLOOR FRAMING PLAN", fontsize=14)

    tags_on_plan = [
        ("GB-1", 100, 120), ("GB-1", 300, 120), ("GB-1", 500, 400),
        ("GB-2", 100, 200), ("GB-2", 300, 260),
        ("C-4", 150, 150), ("C-4", 350, 150), ("C-4", 550, 150),
        ("SB-02", 200, 320),
        ("F-1", 120, 450),
        # noise that should NOT be picked up as a member tag:
        ("GRID A", 40, 90),
        ("SCALE 1:100", 700, 550),
        ("2", 60, 500),  # bare number, no letter prefix
    ]
    for text, x, y in tags_on_plan:
        page.insert_text((x, y), text, fontsize=10)

    # --- Page 2: schedule table (drawn with real vector lines so
    # pdfplumber's table-line detection can find it) ---
    page2 = doc.new_page(width=842, height=595)
    page2.insert_text((72, 50), "GRADE BEAM & COLUMN SCHEDULE", fontsize=14)

    headers = ["MARK", "SIZE (mm)", "LENGTH (m)", "DESCRIPTION"]
    rows = [
        ["GB-1", "300x500", "5.0", "Grade Beam Type 1"],
        ["GB-2", "300x600", "6.0", "Grade Beam Type 2"],
        ["C-4", "400x400", "3.0", "Column Type 4"],
        ["SB-02", "200x300", "2.5", "Sub Beam"],
        ["F-1", "1200x1200", "-", "Isolated Footing"],
    ]

    x0, y0 = 72, 90
    col_widths = [90, 110, 110, 200]
    row_h = 26
    n_cols = len(headers)
    n_rows = len(rows) + 1

    total_w = sum(col_widths)
    total_h = row_h * n_rows

    # vertical lines
    x = x0
    for w in [0] + col_widths:
        x += w
        page2.draw_line((x, y0), (x, y0 + total_h))
    # horizontal lines
    y = y0
    for _ in range(n_rows + 1):
        page2.draw_line((x0, y), (x0 + total_w, y))
        y += row_h

    def put_row(row_idx, cells):
        y = y0 + row_idx * row_h + row_h * 0.65
        x = x0
        for cell, w in zip(cells, col_widths):
            page2.insert_text((x + 5, y), str(cell), fontsize=9)
            x += w

    put_row(0, headers)
    for i, row in enumerate(rows, start=1):
        put_row(i, row)

    doc.save(OUT_PATH)
    doc.close()
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    build()
