"""Import drug label records from openFDA into medicine.json.

Imported records use the same schema as the seed data and are always
saved with review_status = "pending".
"""

import argparse
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://api.fda.gov/drug/label.json"
DAILYMED_URL = "https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={}"
DATABASE_FILE = Path(__file__).with_name("medicine.json")

MAX_BULLETS = 6        # bullets kept per section
MAX_BULLET_CHARS = 300  # characters kept per bullet
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,\-]{0,80}$")

# Fields rewritten when a newer label version arrives.
# aliases, brand_names (if already curated) and reviewer info are kept.
CONTENT_FIELDS = [
    "version", "generic_name", "brand_names", "category", "uses",
    "side_effects", "contraindications", "precautions", "warnings",
    "drug_interactions", "source_name", "source_url", "retrieved_at",
]


# ---------------------------------------------------------------- database
def load_database():
    """Load existing records without deleting them."""
    if not DATABASE_FILE.exists():
        return []

    try:
        with DATABASE_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{DATABASE_FILE.name} contains invalid JSON. "
            "Fix it before importing records."
        ) from exc

    if not isinstance(data, list):
        raise ValueError(
            f"{DATABASE_FILE.name} must contain a JSON list."
        )
    return data


def write_database_atomically(database):
    """Write to a temp file, then replace, so a crash can't corrupt data."""
    if DATABASE_FILE.exists():
        shutil.copy2(DATABASE_FILE, DATABASE_FILE.with_suffix(".json.bak"))

    fd, tmp_path = tempfile.mkstemp(
        dir=DATABASE_FILE.parent, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(database, tmp, indent=2, ensure_ascii=False)
        os.replace(tmp_path, DATABASE_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ----------------------------------------------------------- text helpers
def all_values(container, field):
    """Return every non-empty string stored under an FDA field."""
    values = container.get(field, [])
    if isinstance(values, str):
        values = [values]
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def first_text(container, *fields):
    """First non-empty text among the given fields."""
    for field in fields:
        values = all_values(container, field)
        if values:
            return values[0]
    return ""


def to_bullets(text):
    """Turn a long label section into a few short bullet strings."""
    if not text:
        return []

    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)

    bullets = []
    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 3:
            continue
        if len(sentence) > MAX_BULLET_CHARS:
            sentence = sentence[:MAX_BULLET_CHARS].rsplit(" ", 1)[0] + "..."
        bullets.append(sentence)
        if len(bullets) >= MAX_BULLETS:
            break
    return bullets


def unique(seq):
    seen, out = set(), []
    for item in seq:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


# ------------------------------------------------------------------ fetch
def fetch_medicines(generic_name, limit=10):
    """Fetch medicine label records from openFDA."""
    if not NAME_PATTERN.match(generic_name):
        print("Use letters, numbers, spaces, commas or hyphens only.")
        return []

    params = {
        "search": f'openfda.generic_name:"{generic_name}"',
        "limit": limit,
    }

    try:
        response = requests.get(API_URL, params=params, timeout=30)

        if response.status_code == 404:
            print("No matching records found.")
            return []

        response.raise_for_status()
        payload = response.json()

    except requests.RequestException as exc:
        print(f"Network/API error: {exc}")
        return []
    except ValueError as exc:
        print(f"Could not read API response: {exc}")
        return []

    now = datetime.now(timezone.utc).isoformat()
    imported = []

    for item in payload.get("results", []):
        product = item.get("openfda", {})
        source_id = item.get("id", "")
        set_id = first_text(product, "spl_set_id")

        generic = first_text(product, "generic_name") or generic_name
        brands = unique(all_values(product, "brand_name"))

        source_url = (
            DAILYMED_URL.format(set_id)
            if set_id
            else f"https://api.fda.gov/drug/label.json?search=id:{source_id}"
        )

        imported.append({
            "id": source_id,
            "source_record_id": source_id,
            "version": str(item.get("version", "")),
            "generic_name": generic.title(),
            "aliases": unique([generic_name.lower(), generic.lower()]),
            "brand_names": brands,
            "category": first_text(item, "purpose"),
            "uses": to_bullets(
                first_text(item, "indications_and_usage", "purpose")
            ),
            "side_effects": to_bullets(first_text(item, "adverse_reactions")),
            "contraindications": to_bullets(
                first_text(item, "contraindications")
            ),
            "precautions": to_bullets(
                first_text(item, "precautions", "general_precautions")
            ),
            "warnings": to_bullets(
                first_text(item, "warnings", "boxed_warning")
            ),
            "drug_interactions": to_bullets(
                first_text(item, "drug_interactions")
            ),
            "source_name": "openFDA Drug Labeling API / DailyMed",
            "source_url": source_url,
            "jurisdiction": "United States",
            "retrieved_at": now,
            "review_status": "pending",
            "reviewed_by": None,
        })

    return imported


# ------------------------------------------------------------------- save
def version_number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def save_records(new_records):
    """Add new records, update records with newer label versions."""
    database = load_database()
    index = {
        rec.get("source_record_id"): rec
        for rec in database
        if rec.get("source_record_id")
    }

    added = updated = 0

    for record in new_records:
        source_id = record.get("source_record_id")
        if not source_id:
            continue

        existing = index.get(source_id)

        if existing is None:
            database.append(record)
            index[source_id] = record
            added += 1

        elif version_number(record["version"]) > version_number(
            existing.get("version")
        ):
            for field in CONTENT_FIELDS:
                existing[field] = record[field]
            # New label text has not been checked by a person yet.
            existing["review_status"] = "pending"
            existing["reviewed_by"] = None
            updated += 1

    write_database_atomically(database)

    print(f"New records added:    {added}")
    print(f"Records updated:      {updated}")
    print(f"Total in database:    {len(database)}")
    print(f"Saved to:             {DATABASE_FILE}")


# ------------------------------------------------------------------- main
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", help="generic name, e.g. ibuprofen")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    name = (
        args.name
        or input("Enter a generic medicine name (e.g. ibuprofen): ")
    ).strip()

    if not name:
        print("Please enter a medicine name.")
    else:
        records = fetch_medicines(name, limit=max(1, min(args.limit, 100)))

        if records:
            save_records(records)
            print(
                "Imported records are PENDING review. "
                "Do not use them for medical decisions."
            )
        else:
            print("No records were imported.")