"""
Structural Member Counter — Streamlit app.

Upload a structural plan PDF, get back a CSV/Excel count of every
structural member tag (e.g. GB-1, C-4, SB-02) found on the drawing,
enriched with Length/Size/Description when a schedule/legend table can be
auto-detected on the sheet. Many CAD exports (AutoCAD SHX fonts) have no
real extractable text at all — the app auto-detects this and falls back
to OCR on a rendered image of each page.

Run:
    pip install -r requirements.txt
    streamlit run app.py
"""

import io

import pandas as pd
import streamlit as st

from extractor import run_extraction

st.set_page_config(page_title="Structural Member Counter", layout="wide")

DEFAULT_PREFIXES = pd.DataFrame(
    [
        {"Prefix": "GB", "Meaning": "Grade Beam", "Enabled": True},
        {"Prefix": "TB", "Meaning": "Tie Beam", "Enabled": True},
        {"Prefix": "RB", "Meaning": "Roof Beam", "Enabled": True},
        {"Prefix": "FB", "Meaning": "Floor Beam", "Enabled": True},
        {"Prefix": "SB", "Meaning": "Slab/Sub Beam", "Enabled": True},
        {"Prefix": "G", "Meaning": "Girder", "Enabled": True},
        {"Prefix": "C", "Meaning": "Column", "Enabled": True},
        {"Prefix": "F", "Meaning": "Footing", "Enabled": True},
        {"Prefix": "CF", "Meaning": "Combined Footing", "Enabled": True},
        {"Prefix": "W", "Meaning": "Wall Footing", "Enabled": True},
        {"Prefix": "SW", "Meaning": "Shear Wall", "Enabled": True},
        {"Prefix": "L", "Meaning": "Lintel Beam", "Enabled": True},
    ]
)

st.title("Structural Member Counter")
st.caption(
    "Upload a structural plan PDF and get an automatic count of every structural "
    "member tag on the drawing. Works on real-text PDFs and on vector/SHX-font "
    "CAD exports (auto-falls-back to OCR)."
)

with st.sidebar:
    st.header("1. Member tag prefixes")
    st.caption(
        "Every firm labels members differently — edit this list to match the "
        "drawing's convention. Add rows for prefixes not listed; uncheck to ignore."
    )
    prefix_df = st.data_editor(
        DEFAULT_PREFIXES,
        num_rows="dynamic",
        use_container_width=True,
        key="prefix_editor",
    )
    active_prefixes = [
        str(r["Prefix"]).strip() for _, r in prefix_df.iterrows()
        if r.get("Enabled") and str(r.get("Prefix", "")).strip()
    ]
    st.caption(f"Active: {', '.join(active_prefixes) if active_prefixes else '(none)'}")

    st.header("2. Extraction mode")
    mode_label = st.radio(
        "How should the PDF be read?",
        ["Auto-detect (recommended)", "Text only (fast)", "Force OCR (slow, for SHX/vector drawings)"],
        index=0,
    )
    mode = {"Auto-detect (recommended)": "auto", "Text only (fast)": "text",
            "Force OCR (slow, for SHX/vector drawings)": "ocr"}[mode_label]
    if mode != "text":
        st.caption(
            "OCR renders each page as an image and reads it visually — much slower "
            "(~10-20s/page) and less exact than real text. Always review results before trusting them."
        )

uploaded = st.file_uploader("3. Upload structural plan PDF", type=["pdf"])

if uploaded is not None:
    if st.button("Process drawing", type="primary"):
        pdf_bytes = uploaded.read()
        progress = st.progress(0.0, text="Starting...")

        def on_progress(page, total):
            progress.progress(page / total, text=f"OCR: page {page}/{total}...")

        with st.spinner("Extracting and matching member tags..."):
            result = run_extraction(pdf_bytes, active_prefixes, mode=mode, progress_cb=on_progress)
        progress.empty()
        st.session_state["result"] = result
        st.session_state["pdf_name"] = uploaded.name

result = st.session_state.get("result")

if result is not None:
    st.caption(f"Extraction mode used: **{result.mode.upper()}**")
    for w in result.warnings:
        st.warning(w)

    st.subheader("4. Review & correct")
    st.caption(
        "Drawings are messy — a grid label or note can occasionally match a tag pattern, and "
        "OCR misreads similar-looking characters (0/O, 1/I, 5/S). Delete/edit rows below before "
        "exporting; this table is what gets downloaded. Low **Avg Confidence** rows deserve a second look."
    )

    from extractor import summarize

    summary = summarize(result.occurrences, result.schedule)
    edited_summary = st.data_editor(summary, num_rows="dynamic", use_container_width=True, key="summary_editor")

    col1, col2, col3 = st.columns(3)
    col1.metric("Unique tags", len(edited_summary))
    col2.metric("Total occurrences", int(edited_summary["Count"].sum()) if not edited_summary.empty else 0)
    col3.metric("Pages processed", result.pages_processed)

    if not result.schedule.empty:
        with st.expander(f"Auto-detected schedule table ({len(result.schedule)} rows)"):
            st.dataframe(result.schedule, use_container_width=True)

    if not result.occurrences.empty:
        with st.expander("Raw tag occurrences (page + position)"):
            st.dataframe(result.occurrences, use_container_width=True)

    st.subheader("5. Export")
    base_name = st.session_state.get("pdf_name", "structural_members").rsplit(".", 1)[0]

    csv_bytes = edited_summary.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download CSV",
        data=csv_bytes,
        file_name=f"{base_name}_member_count.csv",
        mime="text/csv",
    )

    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        edited_summary.to_excel(writer, index=False, sheet_name="Member Count")
        if not result.schedule.empty:
            result.schedule.to_excel(writer, index=False, sheet_name="Detected Schedule")
    st.download_button(
        "Download Excel",
        data=excel_buffer.getvalue(),
        file_name=f"{base_name}_member_count.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
else:
    st.info("Upload a PDF and click **Process drawing** to get started.")
