# Structural Member Counter

Upload a structural plan PDF and get an automatic count of every structural
member tag on the drawing (e.g. `GB-1`, `C-4`, `SB-02`), enriched with
Length/Size/Description when a schedule/legend table can be auto-detected on
the sheet.

Handles two kinds of PDF:
- **Real-text PDFs** — matched directly against extracted text (fast).
- **Vector/SHX-font CAD exports** — very common: AutoCAD's default SHX fonts
  export as vector line-art, not real text, so `get_text()` comes back
  almost empty even on a dense drawing. The app auto-detects this (checked
  against real sample drawings) and falls back to OCR on a rendered image of
  each page. Slower, and needs a human review pass, but it's what actually
  works on files like these.

## Setup

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**Windows quirk (confirmed on this machine):** Streamlit spawns its actual
worker process resolved through the registry's App Paths key, not through
the invoking interpreter or an activated venv's PATH — so on a machine
with more than one Python install, the worker can end up running under a
*different* Python than the one you installed requirements into, even
inside an activated venv. If `streamlit run` seems to hang forever on OCR
(check via Task Manager whether a python.exe child process is actually
burning CPU — if so it's working, just check back), the fix is to also
install requirements into whichever Python is registered as the system
default:

```
python -m pip install -r requirements.txt
```

(that's the bare `python`, not `.venv\Scripts\python.exe`). On a machine
with only one Python install, this is a no-op — you can skip it.

## Run

```
.venv\Scripts\streamlit run app.py
```

Opens at http://localhost:8501. Upload a PDF, adjust the member-tag prefix
list in the sidebar to match the project's labeling convention, click
**Process drawing**, review/correct the table (OCR mode especially — check
low **Avg Confidence** rows), then download CSV or Excel.

## How it works

- **Text mode** (`extractor.py: extract_occurrences_text`): scans every
  page's real text tokens (PyMuPDF) for patterns like `<prefix><digits>`.
- **OCR mode** (`extract_occurrences_ocr`): renders each page to an image
  and runs RapidOCR, then searches the recognized text for the same
  patterns (with boundary guards, since OCR can glue nearby words into one
  box).
- **Auto mode** (default): tries text mode first; if it finds implausibly
  few matches on a page dense with vector paths, it's almost certainly an
  SHX-font drawing, so it re-runs in OCR mode instead.
- **Schedule detection** (`extract_schedule`): looks for a bordered table
  on any page whose header row contains keywords like
  MARK/SIZE/LENGTH/DESCRIPTION. Table *structure* comes from pdfplumber's
  vector-line detection (works even without real text); table *cell text*
  comes from real PDF text or OCR boxes overlapping each cell, depending on
  mode. Tables that span nearly the whole page are rejected — dense
  grid/dimension lines on a structural drawing can otherwise get misread as
  one giant table and wrongly blank out the entire plan from tag counting.
- **Review table**: editable in the browser before export — drawings are
  messy (grid labels, notes) and OCR misreads similar characters (0/O,
  1/I, 5/S), so a human pass before trusting the export is expected, not
  optional.

## Known limitations (MVP)

- One PDF at a time. Batch upload of a full drawing set is a natural next
  step.
- OCR mode is slow (~10-20s/page) and not perfectly accurate — that's a
  property of reading dense technical drawings visually, not a bug to
  "fix away." Review is part of the workflow.
- Tag prefix list is manual per session (not saved per project yet).
- Schedule auto-detection needs real ruled table borders; a schedule drawn
  without lines won't be picked up (tag counts still work, just without
  Length/Size/Description enrichment).

## Tests

```
python tests\make_sample_pdf.py       # regenerate the synthetic text-mode test drawing
python tests\run_smoke_test.py        # text-mode extraction pipeline test (synthetic PDF)
python tests\run_real_sample_test.py "sample\SAMPLE WORK 1.pdf"   # full auto-mode test against a real drawing
```
