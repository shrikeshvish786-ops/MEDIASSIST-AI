import difflib
import json
from pathlib import Path

import streamlit as st

DATABASE_FILE = Path(__file__).with_name("medicine.json")

st.set_page_config(
    page_title="MediAssist AI",
    page_icon="💊",
    layout="centered",
)


# ------------------------------------------------------------------ data
@st.cache_data
def load_medicines():
    """Load medicine.json; return (records, error_message)."""
    if not DATABASE_FILE.exists():
        return [], f"{DATABASE_FILE.name} not found."
    try:
        with DATABASE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, list):
            return [], "medicine.json must contain a JSON list."
        return data, None
    except json.JSONDecodeError:
        return [], "medicine.json contains invalid JSON."


def search_terms(record):
    """All names a record can be found under."""
    terms = [record.get("generic_name", "")]
    terms += record.get("aliases", [])
    terms += record.get("brand_names", [])
    return [t.lower() for t in terms if t]


def find_matches(query, records):
    """Exact match first, then partial match, then close spellings."""
    query = query.strip().lower()
    if not query:
        return [], []

    exact = [r for r in records if query in search_terms(r)]
    if exact:
        return exact, []

    partial = [
        r for r in records
        if any(query in term or term in query for term in search_terms(r))
    ]
    if partial:
        return partial, []

    lookup = {}
    for record in records:
        for term in search_terms(record):
            lookup[term] = record
    close = difflib.get_close_matches(query, lookup.keys(), n=3, cutoff=0.7)
    suggestions = sorted({lookup[c].get("generic_name", "") for c in close})
    return [], suggestions


def show_section(title, items):
    if not items:
        return
    st.subheader(title)
    if isinstance(items, str):
        items = [items]
    for entry in items:
        st.markdown(f"- {entry}")


def show_record(record):
    st.subheader(record.get("generic_name", "Unknown medicine"))

    if record.get("brand_names"):
        st.caption("Brand names: " + ", ".join(record["brand_names"][:8]))
    if record.get("category"):
        st.write("**Category:**", record["category"])

    status = record.get("review_status", "pending")
    if status != "approved":
        st.warning(
            "⚠️ **Unreviewed information.** This record has not been "
            "checked by a qualified reviewer yet."
        )

    show_section("Uses", record.get("uses"))
    show_section("Possible side effects", record.get("side_effects"))
    show_section("Contraindications", record.get("contraindications"))
    show_section("Warnings", record.get("warnings"))
    show_section("Precautions", record.get("precautions"))
    show_section("Drug interactions", record.get("drug_interactions"))

    if record.get("source_url"):
        st.markdown(
            f"**Reference:** [{record.get('source_name', 'Source')}]"
            f"({record['source_url']})"
        )
    st.caption(
        f"Jurisdiction: {record.get('jurisdiction') or 'Not specified'} | "
        f"Review status: {status} | "
        f"Retrieved: {record.get('retrieved_at') or 'n/a'}"
    )


# -------------------------------------------------------------------- UI
st.title("💊 MediAssist AI")
st.caption("Your medical information assistant")

st.warning(
    "Educational prototype only. "
    "Not a substitute for a doctor or pharmacist."
)

st.write(
    "Search by generic or brand name. This version does not provide "
    "diagnosis, dosing, or personalised treatment advice."
)

records, load_error = load_medicines()

if load_error:
    st.error(load_error)
    st.stop()

show_unreviewed = st.checkbox(
    "Include unreviewed records",
    value=True,
    help="Untick to show only records marked 'approved'.",
)

visible = [
    r for r in records
    if show_unreviewed or r.get("review_status") == "approved"
]

with st.form("search_form"):
    medicine = st.text_input(
        "Enter a medicine name",
        placeholder="e.g. Paracetamol, Acetaminophen, Advil",
    )
    submitted = st.form_submit_button("Search medicine")

if submitted:
    if not medicine.strip():
        st.info("Please enter a medicine name.")
    else:
        matches, suggestions = find_matches(medicine, visible)

        if matches:
            if len(matches) > 1:
                st.caption(f"{len(matches)} records found.")
            for record in matches[:5]:
                show_record(record)
                st.divider()

            st.warning(
                "Educational information only. This page does not "
                "determine whether a medicine is suitable for you."
            )
        else:
            st.error("Medicine not found. Consult a pharmacist.")
            if suggestions:
                st.info("Did you mean: " + ", ".join(suggestions) + "?")

st.divider()
st.caption(
    "Content reflects United States labeling. Names, strengths and "
    "approved uses may differ in other countries. "
    "If someone may have taken too much of a medicine, contact your local "
    "poison control centre or emergency services immediately."
)