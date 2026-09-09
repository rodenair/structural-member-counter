import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractor import run_extraction, summarize

PDF_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "sample", "SAMPLE WORK 1.pdf"
)
PREFIXES = ["GB", "TB", "RB", "FB", "SB", "G", "C", "F", "CF", "W", "SW", "L", "FTB", "PC"]

with open(PDF_PATH, "rb") as f:
    pdf_bytes = f.read()

t0 = time.time()


def progress(page, total):
    print(f"  OCR page {page}/{total}...", flush=True)


result = run_extraction(pdf_bytes, PREFIXES, mode="auto", progress_cb=progress)
print(f"\ntotal time: {time.time() - t0:.1f}s, mode used: {result.mode}")

print("\n=== warnings ===")
for w in result.warnings:
    print("-", w)

summary = summarize(result.occurrences, result.schedule)
print("\n=== summary (what gets exported) ===")
print(summary.to_string(index=False))

print(f"\nunique tags: {len(summary)}, total occurrences: {int(summary['Count'].sum()) if not summary.empty else 0}")
print(f"schedule rows detected: {len(result.schedule)}")
