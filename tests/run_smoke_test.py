import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractor import run_extraction, summarize

PDF_PATH = os.path.join(os.path.dirname(__file__), "sample_plan.pdf")
PREFIXES = ["GB", "TB", "RB", "FB", "SB", "G", "C", "F", "CF", "W", "SW", "L"]

with open(PDF_PATH, "rb") as f:
    pdf_bytes = f.read()

result = run_extraction(pdf_bytes, PREFIXES)

print("=== warnings ===")
for w in result.warnings:
    print("-", w)

print("\n=== raw occurrences ===")
print(result.occurrences.to_string(index=False))

print("\n=== detected schedule ===")
print(result.schedule.to_string(index=False) if not result.schedule.empty else "(empty)")

summary = summarize(result.occurrences, result.schedule)
print("\n=== summary (what gets exported) ===")
print(summary.to_string(index=False))

# --- assertions ---
expected_counts = {"GB-1": 3, "GB-2": 2, "C-4": 3, "SB-02": 1, "F-1": 1}
by_tag = dict(zip(summary["Tag"], summary["Count"]))
errors = []
for tag, exp in expected_counts.items():
    got = by_tag.get(tag)
    if got != exp:
        errors.append(f"{tag}: expected {exp}, got {got}")

if "GRID" in by_tag or "A" in by_tag or "SCALE" in str(summary["Tag"].tolist()):
    errors.append("noise text was incorrectly matched as a tag")

gb1_row = summary[summary["Tag"] == "GB-1"].iloc[0]
if gb1_row["Length"] != "5.0":
    errors.append(f"GB-1 Length expected '5.0', got {gb1_row['Length']!r}")
if gb1_row["Matched Schedule"] != "Yes":
    errors.append("GB-1 should show Matched Schedule = Yes")

print("\n=== result ===")
if errors:
    print("FAIL")
    for e in errors:
        print(" -", e)
    sys.exit(1)
else:
    print("PASS — counts, noise filtering, and schedule enrichment all correct")
