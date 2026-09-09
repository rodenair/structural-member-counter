"""
Core extraction logic for the Structural Member Counter.

Two extraction modes, auto-selected per document:

  TEXT mode: pull real PDF text tokens (PyMuPDF) and match them against a
  configurable list of member-tag prefixes. Fast and precise, but only
  works when the drawing's text is real embedded text.

  OCR mode: many CAD exports (esp. AutoCAD with SHX fonts) turn every
  label into vector line-art, not real text — get_text() comes back
  nearly empty even though the sheet is dense with tags. In that case we
  render each page to an image and run OCR (RapidOCR) instead, treating
  it like a scanned drawing. Slower, and less exact (a human review pass
  is expected), but it's what actually works on those files.

In both modes we also try to auto-detect a schedule/legend table (a table
whose header row looks like MARK/SIZE/LENGTH/...) so each tag can be
enriched with its length/size/description. Table *structure* comes from
pdfplumber's vector-line detection (works in both modes, since the table
borders are always real vector lines); table *cell text* comes from real
PDF text in TEXT mode or from OCR boxes overlapping each cell in OCR mode.

This is deliberately conservative: it surfaces everything it finds for a
human to review/correct in the app, rather than silently dropping or
guessing at ambiguous matches.
"""

import io
import re
from dataclasses import dataclass, field

import pymupdf as fitz  # PyMuPDF
import pandas as pd
import pdfplumber

# Keywords that suggest a pdfplumber-detected table is a member schedule,
# not some unrelated table (title block, notes, etc.)
SCHEDULE_HEADER_KEYWORDS = [
    "mark", "tag", "id", "size", "length", "l(m)", "l (m)", "dimension",
    "rebar", "reinforcement", "width", "depth", "grade", "description",
    "qty", "quantity", "type",
]

# If text-mode finds suspiciously few tag occurrences AND the document is
# this dense with vector paths on average, it's almost certainly a
# vector/SHX-font CAD export (real content drawn as line-art, not text)
# rather than a genuinely sparse/irrelevant PDF — fall back to OCR
# automatically in "auto" mode. A real text-based drawing this dense with
# geometry would yield far more than a handful of tag matches; a stray
# match or two is more likely a false positive from a real-text sheet/detail
# title (e.g. "F7 PLAN VIEW") than genuine tag callouts. (Char-count alone
# is unreliable: title-block boilerplate can look like "plenty of text"
# while the actual drawing content is still all vector art.)
AVG_VECTOR_PATHS_OCR_TRIGGER = 50
MIN_TEXT_OCCURRENCES_TO_TRUST = 5

OCR_ZOOM = 2.5  # render scale for OCR — higher = better small-text legibility, slower


def normalize_tag(raw: str) -> str:
    """Collapse formatting noise so 'GB-1', 'GB 1', 'gb1' all compare equal."""
    return re.sub(r"[\s\-_]", "", raw).upper()


def build_pattern(prefixes: list[str]) -> re.Pattern:
    """Strict whole-token pattern, for already-tokenized real PDF words (TEXT mode)."""
    escaped = sorted((re.escape(p) for p in prefixes if p.strip()), key=len, reverse=True)
    if not escaped:
        escaped = [re.escape("X_NEVER_MATCHES_X")]
    alternation = "|".join(escaped)
    return re.compile(rf"^(?:{alternation})[\s\-]?\d{{1,3}}[A-Za-z]?$", re.IGNORECASE)


def build_search_pattern(prefixes: list[str]) -> re.Pattern:
    """Boundary-guarded pattern for scanning inside merged OCR text, where
    nearby words can get glued into one box (e.g. 'F7 PLAN VIEW' -> 'F7PLANVIEW').
    Lookarounds stop it from grabbing a stray letter/digit off neighboring text."""
    escaped = sorted((re.escape(p) for p in prefixes if p.strip()), key=len, reverse=True)
    if not escaped:
        escaped = [re.escape("X_NEVER_MATCHES_X")]
    alternation = "|".join(escaped)
    return re.compile(
        rf"(?<![A-Za-z0-9])(?:{alternation})[\s\-]?\d{{1,3}}(?:[A-Za-z](?![A-Za-z0-9]))?(?![0-9])",
        re.IGNORECASE,
    )


@dataclass
class ExtractionResult:
    occurrences: pd.DataFrame  # one row per tag occurrence found in the drawing
    schedule: pd.DataFrame     # one row per schedule table entry found (may be empty)
    pages_processed: int = 0
    mode: str = "text"         # "text" or "ocr"
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# TEXT mode
# --------------------------------------------------------------------------

def _avg_vector_paths_per_page(doc) -> float:
    page_count = len(doc)
    if page_count == 0:
        return 0.0
    return sum(len(doc[i].get_drawings()) for i in range(page_count)) / page_count


def extract_occurrences_text(
    pdf_bytes: bytes, prefixes: list[str], exclude_bboxes: dict[int, list[tuple]] | None = None
) -> tuple[pd.DataFrame, int]:
    exclude_bboxes = exclude_bboxes or {}
    pattern = build_pattern(prefixes)
    rows = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_bboxes = exclude_bboxes.get(page_index + 1, [])
            words = page.get_text("words")
            for x0, y0, x1, y1, word, *_ in words:
                token = word.strip().strip(",.;:()[]")
                if not token or not pattern.match(token):
                    continue
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if any(bx0 <= cx <= bx1 and by0 <= cy <= by1 for bx0, by0, bx1, by1 in page_bboxes):
                    continue
                rows.append({
                    "tag_raw": token.upper().replace(" ", "-"), "tag_key": normalize_tag(token),
                    "page": page_index + 1, "x": round(x0, 1), "y": round(y0, 1), "confidence": 1.0,
                })
        page_count = len(doc)
    finally:
        doc.close()
    return pd.DataFrame(rows), page_count


# --------------------------------------------------------------------------
# OCR mode
# --------------------------------------------------------------------------

_ocr_engine = None


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def ocr_all_pages(pdf_bytes: bytes, zoom: float = OCR_ZOOM, progress_cb=None) -> tuple[dict[int, list[tuple]], int]:
    """Render every page and OCR it. Returns {1-indexed page: [(x0,y0,x1,y1,text,conf), ...]}
    with boxes in PDF point space (scaled back down from the rendered pixels)."""
    engine = _get_ocr_engine()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_ocr: dict[int, list[tuple]] = {}
    try:
        page_count = len(doc)
        for page_index in range(page_count):
            if progress_cb:
                progress_cb(page_index + 1, page_count)
            page = doc[page_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            img_bytes = pix.tobytes("png")
            result, _ = engine(img_bytes)
            boxes = []
            for box, text, conf in (result or []):
                xs = [p[0] / zoom for p in box]
                ys = [p[1] / zoom for p in box]
                boxes.append((min(xs), min(ys), max(xs), max(ys), text, float(conf)))
            page_ocr[page_index + 1] = boxes
    finally:
        doc.close()
    return page_ocr, page_count


def extract_occurrences_ocr(
    page_ocr: dict[int, list[tuple]], prefixes: list[str], exclude_bboxes: dict[int, list[tuple]] | None = None
) -> pd.DataFrame:
    exclude_bboxes = exclude_bboxes or {}
    pattern = build_search_pattern(prefixes)
    rows = []
    for page_num, boxes in page_ocr.items():
        page_bboxes = exclude_bboxes.get(page_num, [])
        for x0, y0, x1, y1, text, conf in boxes:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if any(bx0 <= cx <= bx1 and by0 <= cy <= by1 for bx0, by0, bx1, by1 in page_bboxes):
                continue
            cleaned = text.replace(" ", "")
            for m in pattern.finditer(cleaned):
                tag_raw = m.group(0).upper()
                rows.append({
                    "tag_raw": tag_raw, "tag_key": normalize_tag(tag_raw),
                    "page": page_num, "x": round(x0, 1), "y": round(y0, 1), "confidence": round(conf, 2),
                })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Schedule table detection (shared by both modes)
# --------------------------------------------------------------------------

def _cell_text_from_pdf(page, cell_bbox) -> str:
    if cell_bbox is None:
        return ""
    x0, top, x1, bottom = cell_bbox
    cropped = page.within_bbox((x0, top, x1, bottom))
    return (cropped.extract_text() or "").strip()


def _cell_text_from_ocr(boxes, cell_bbox) -> str:
    if cell_bbox is None or not boxes:
        return ""
    x0, top, x1, bottom = cell_bbox
    hits = []
    for bx0, by0, bx1, by1, text, conf in boxes:
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        if x0 <= cx <= x1 and top <= cy <= bottom:
            hits.append((bx0, text))
    hits.sort(key=lambda t: t[0])
    return " ".join(t for _, t in hits).strip()


def extract_schedule(
    pdf_bytes: bytes, page_ocr: dict[int, list[tuple]] | None = None
) -> tuple[pd.DataFrame, dict[int, list[tuple]]]:
    """`page_ocr`, if given, is used to reconstruct cell text for tables whose
    borders are real vector lines but whose text isn't real PDF text (OCR mode)."""
    schedule_rows = []
    table_bboxes: dict[int, list[tuple]] = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_index, page in enumerate(pdf.pages):
            page_num = page_index + 1
            ocr_boxes = (page_ocr or {}).get(page_num, [])
            try:
                tables = page.find_tables()
            except Exception:
                continue
            for t_idx, table in enumerate(tables):
                n_rows = len(table.rows)
                n_cols = len(table.rows[0].cells) if n_rows else 0
                if n_rows < 2 or n_cols < 2:
                    continue

                # A real schedule is a bounded box in one area of the sheet, not
                # the whole page — dense grid/dimension lines on a structural
                # drawing can otherwise get misread as one giant "table" spanning
                # the sheet border, which would wrongly blank out the entire plan.
                tx0, ttop, tx1, tbottom = table.bbox
                if (tx1 - tx0) > 0.75 * page.width and (tbottom - ttop) > 0.75 * page.height:
                    continue

                def row_text(row):
                    return [
                        (_cell_text_from_pdf(page, c) or _cell_text_from_ocr(ocr_boxes, c))
                        for c in row.cells
                    ]

                header = [h.strip().lower() for h in row_text(table.rows[0])]
                # Real header cells are short labels (e.g. "MARK", "LENGTH (M)").
                # A cell that swallowed a huge chunk of OCR text (from a bogus,
                # oversized table region) shouldn't count as a keyword match just
                # because a keyword-like substring appears somewhere in the noise.
                short_header = [h for h in header if len(h) <= 40]
                if not any(any(kw in h for kw in SCHEDULE_HEADER_KEYWORDS) for h in short_header):
                    continue  # doesn't look like a member schedule
                table_bboxes.setdefault(page_num, []).append(table.bbox)
                for row in table.rows[1:]:
                    cells = row_text(row)
                    if not cells or not cells[0]:
                        continue
                    tag_raw = cells[0].strip()
                    if not tag_raw:
                        continue
                    entry = {"tag_raw": tag_raw.upper(), "tag_key": normalize_tag(tag_raw),
                              "page": page_num, "table_index": t_idx}
                    for col_name, val in zip(header, cells):
                        entry[col_name or "col"] = val
                    schedule_rows.append(entry)
    return pd.DataFrame(schedule_rows), table_bboxes


# --------------------------------------------------------------------------
# Combine + summarize
# --------------------------------------------------------------------------

def summarize(occurrences: pd.DataFrame, schedule: pd.DataFrame) -> pd.DataFrame:
    cols = ["Tag", "Count", "Pages", "Avg Confidence", "Matched Schedule", "Length", "Size", "Description"]
    if occurrences.empty:
        return pd.DataFrame(columns=cols)

    # Group by tag_key (the normalized identity) so formatting variants of the
    # same physical tag — "FTB-1" from one OCR box, "FTB1" from another — merge
    # into one row instead of splitting the count. The most common raw spelling
    # is used as the display label.
    most_common_raw = (
        occurrences.groupby(["tag_key", "tag_raw"]).size().reset_index(name="n")
        .sort_values("n", ascending=False).drop_duplicates("tag_key").set_index("tag_key")["tag_raw"]
    )
    grouped = (
        occurrences.groupby("tag_key")
        .agg(
            Count=("page", "count"),
            Pages=("page", lambda s: ", ".join(sorted({str(p) for p in s}))),
            **{"Avg Confidence": ("confidence", "mean")},
        )
        .reset_index()
    )
    grouped["tag_raw"] = grouped["tag_key"].map(most_common_raw)
    grouped["Avg Confidence"] = grouped["Avg Confidence"].round(2)

    length_col = next((c for c in schedule.columns if "length" in c.lower() or "l(m" in c.lower()), None)
    size_col = next((c for c in schedule.columns if "size" in c.lower() or "dimension" in c.lower()), None)
    desc_col = next((c for c in schedule.columns if "descrip" in c.lower() or "type" in c.lower()), None)

    def lookup(tag_key, col):
        if schedule.empty or col is None:
            return ""
        match = schedule[schedule["tag_key"] == tag_key]
        if match.empty:
            return ""
        val = match.iloc[0].get(col, "")
        return "" if val is None else str(val)

    grouped["Matched Schedule"] = grouped["tag_key"].apply(
        lambda k: "Yes" if (not schedule.empty and k in set(schedule["tag_key"])) else "No"
    )
    grouped["Length"] = grouped["tag_key"].apply(lambda k: lookup(k, length_col))
    grouped["Size"] = grouped["tag_key"].apply(lambda k: lookup(k, size_col))
    grouped["Description"] = grouped["tag_key"].apply(lambda k: lookup(k, desc_col))

    grouped = grouped.rename(columns={"tag_raw": "Tag"}).drop(columns=["tag_key"])
    grouped = grouped[cols]
    return grouped.sort_values("Tag").reset_index(drop=True)


def run_extraction(pdf_bytes: bytes, prefixes: list[str], mode: str = "auto", progress_cb=None) -> ExtractionResult:
    """`mode`: "auto" (default), "text", or "ocr"."""
    warnings = []

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_count = len(doc)
    avg_vectors = _avg_vector_paths_per_page(doc)
    doc.close()

    schedule = occurrences = None
    used_mode = None

    if mode in ("auto", "text"):
        schedule, table_bboxes = extract_schedule(pdf_bytes)
        occurrences, _ = extract_occurrences_text(pdf_bytes, prefixes, exclude_bboxes=table_bboxes)
        used_mode = "text"

    need_ocr = mode == "ocr" or (
        mode == "auto"
        and occurrences is not None
        and len(occurrences) < MIN_TEXT_OCCURRENCES_TO_TRUST
        and avg_vectors > AVG_VECTOR_PATHS_OCR_TRIGGER
    )

    if need_ocr:
        if mode == "auto":
            warnings.append(
                f"Text extraction found no member tags despite ~{avg_vectors:.0f} vector paths/page "
                "on average — this looks like a vector/SHX-font CAD export where the real content "
                "is drawn as line-art, not text. Falling back to OCR (slower; review results below "
                "before trusting the counts)."
            )
        page_ocr, _ = ocr_all_pages(pdf_bytes, progress_cb=progress_cb)
        schedule, table_bboxes = extract_schedule(pdf_bytes, page_ocr=page_ocr)
        occurrences = extract_occurrences_ocr(page_ocr, prefixes, exclude_bboxes=table_bboxes)
        used_mode = "ocr"

    if occurrences.empty:
        warnings.append(
            "No member tags matched. Check that the prefix list matches this drawing's labeling "
            "convention" + (", or try forcing OCR mode." if used_mode == "text" else ".")
        )
    if schedule.empty:
        warnings.append(
            "No schedule/legend table was auto-detected — Length/Size/Description columns will be "
            "blank. You can still export the tag counts."
        )
    return ExtractionResult(
        occurrences=occurrences, schedule=schedule, pages_processed=page_count, mode=used_mode, warnings=warnings
    )
