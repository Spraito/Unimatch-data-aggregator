"""SQLite persistence and transactional imports of the scraper's JSON exports."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import click
from flask import current_app, g
from flask.cli import with_appcontext

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_FIELDS = (
    "tuition_estimates", "admission_requirements", "entrance_exam_coverage",
    "application_deadlines", "application_status",
)
SCHOOLS = {
    "University of the Philippines Diliman": ("up-diliman", "UP Diliman", "Quezon City", "Public", "maroon"),
    "Polytechnic University of the Philippines": ("pup-sta-mesa", "PUP", "Manila", "Public", "rust"),
    "De La Salle University": ("dlsu-manila", "DLSU", "Manila", "Private", "green"),
    "University of Santo Tomas": ("ust-manila", "UST", "Manila", "Private", "gold"),
}
CANONICAL_CAMPUSES = {
    "up-diliman": "Diliman, Quezon City, Metro Manila",
    "pup-sta-mesa": "Sta. Mesa, Manila, Metro Manila",
    "dlsu-manila": "Taft Avenue, Malate, Manila, Metro Manila",
    "ust-manila": "España Boulevard, Sampaloc, Manila, Metro Manila",
}
TOPICS = {
    "Computing": ("computer", "information technology"),
    "Engineering": ("engineering", "architecture", "environmental planning", "interior design"),
    "Education": ("education", "library"),
    "Business": ("business", "accountancy", "accounting", "entrepreneur", "office", "tourism", "hospitality", "transportation"),
    "Science & health": ("science", "mathematics", "statistics", "biology", "chemistry", "physics", "nutrition", "psychology"),
}


def safe_url(value):
    """Imported links must be HTTPS URLs, never script URLs or embedded credentials."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        return value if parts.scheme == "https" and parts.hostname and not parts.username and not parts.password else None
    except ValueError:
        return None


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def topic_for(name):
    for topic, keywords in TOPICS.items():
        if any(keyword in name.casefold() for keyword in keywords):
            return topic
    return "Arts & society"


def _identity(name, campus):
    known = SCHOOLS.get(name)
    if known and " ".join(campus.casefold().split()) == CANONICAL_CAMPUSES[known[0]].casefold():
        return known
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:70] or "university"
    suffix = hashlib.sha256((name.casefold() + ":" + " ".join(campus.casefold().split())).encode()).hexdigest()[:8]
    initials = "".join(word[0].upper() for word in re.findall(r"[A-Za-z]+", name)
                       if word.casefold() not in {"the", "of", "and"})[:5] or "UNI"
    # A different campus must not overwrite a seeded campus or inherit its city.
    return (f"{slug}-{suffix}", known[1] if known else initials, "Not specified",
            known[3] if known else "Not specified", known[4] if known else "green")


def _validate_records(records):
    if not isinstance(records, list) or not records or len(records) > 100:
        raise ValueError("Expected 1–100 university records in a JSON array")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Each university record must be an object")
        for key in ("university_name", "campus_location"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f"Missing or invalid {key}")
        for key in (*EVIDENCE_FIELDS, "source_notes", "sources", "errors", "missing_fields", "programs"):
            if not isinstance(record.get(key, []), list):
                raise ValueError(f"{key} must be an array")
        if len(record.get("programs", [])) > 5000:
            raise ValueError("At most 5,000 programs per institution are supported")
        for program in record.get("programs", []):
            if not isinstance(program, dict) or not isinstance(program.get("name"), str) or not program["name"].strip():
                raise ValueError("Each program needs a name")
        if any(not isinstance(item, dict) for key in EVIDENCE_FIELDS for item in record.get(key, [])):
            raise ValueError("Evidence entries must be objects")
        if any(not isinstance(item, str) for item in record.get("source_notes", [])):
            raise ValueError("Source notes must be text")
        if any(not isinstance(item, dict) for item in record.get("sources", [])):
            raise ValueError("Sources must be objects")


def import_records(records):
    """Validate the entire batch first; reimports replace a school's program set.

    Imports remain unverified evidence. This prototype has no review/approval
    workflow and never turns a source statement into confirmed admissions advice.
    """
    _validate_records(records)
    connection = get_db()
    with connection:
        for record in records:
            name, campus = record["university_name"].strip(), record["campus_location"].strip()
            school_id, short, city, sector, color = _identity(name, campus)
            evidence = {key: record.get(key, []) for key in (*EVIDENCE_FIELDS, "source_notes", "errors", "missing_fields")}
            sources = record.get("sources", [])
            official = next((safe_url(s.get("final_url") or s.get("requested_url")) for s in sources
                             if safe_url(s.get("final_url") or s.get("requested_url"))), None)
            existing = connection.execute("SELECT official_url FROM universities WHERE id = ?", (school_id,)).fetchone()
            official = official or (existing["official_url"] if existing else None)
            fetched = any(s.get("status") == "fetched" for s in sources)
            status = "partial" if fetched or any(record.get(k) for k in (*EVIDENCE_FIELDS, "programs")) else "pending"
            connection.execute("""
                INSERT INTO universities
                (id, name, short_name, city, campus_location, sector, color, official_url, sources_json,
                 evidence_json, collection_status, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, short_name=excluded.short_name, campus_location=excluded.campus_location,
                official_url=excluded.official_url, sources_json=excluded.sources_json,
                evidence_json=excluded.evidence_json, collection_status=excluded.collection_status,
                collected_at=excluded.collected_at
            """, (school_id, name, short, city, campus, sector, color, official,
                  json.dumps(sources, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False),
                  status, record.get("retrieved_at_utc")))
            connection.execute("DELETE FROM programs WHERE university_id = ?", (school_id,))
            for program in record.get("programs", []):
                program_name = " ".join(program["name"].split())
                program_id = hashlib.sha256((school_id + ":" + program_name.casefold()).encode()).hexdigest()[:20]
                alignment = program.get("strand_alignment")
                alignment = alignment if isinstance(alignment, str) else None
                connection.execute("""INSERT OR IGNORE INTO programs
                    (id, university_id, name, topic, strand_alignment, source_url)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (program_id, school_id, program_name, topic_for(program_name), alignment, safe_url(program.get("source_url"))))
    return len(records)


def init_db():
    connection = get_db()
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS universities (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, short_name TEXT NOT NULL,
            city TEXT NOT NULL, campus_location TEXT NOT NULL, sector TEXT NOT NULL,
            color TEXT NOT NULL, official_url TEXT, sources_json TEXT NOT NULL DEFAULT '[]',
            evidence_json TEXT NOT NULL DEFAULT '{}', collection_status TEXT NOT NULL DEFAULT 'pending',
            collected_at TEXT
        );
        CREATE TABLE IF NOT EXISTS programs (
            id TEXT PRIMARY KEY, university_id TEXT NOT NULL REFERENCES universities(id) ON DELETE CASCADE,
            name TEXT NOT NULL, topic TEXT NOT NULL, strand_alignment TEXT, source_url TEXT
        );
        CREATE INDEX IF NOT EXISTS programs_school ON programs(university_id);
    """)
    if connection.execute("SELECT COUNT(*) FROM universities").fetchone()[0]:
        return
    configured = json.loads((ROOT / "config/universities.json").read_text())
    for source in configured:
        school_id, short, city, sector, color = _identity(source["name"], source["campus_location"])
        connection.execute("""INSERT INTO universities
            (id, name, short_name, city, campus_location, sector, color, official_url, sources_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (school_id, source["name"], short, city, source["campus_location"], sector, color,
             source["pages"][0]["url"], json.dumps([{"requested_url": p["url"], "status": "configured"} for p in source["pages"]])))
    connection.commit()
    # Start immediately with the collected evidence. No external network requests.
    sample = ROOT / "data/pup/unimatch_ph_raw_data.json"
    if sample.exists():
        import_records(json.loads(sample.read_text()))


@click.command("import-data")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@with_appcontext
def import_data_command(path):
    """Import a reviewed scraper JSON export into the local SQLite database."""
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Import file exceeds 10 MiB")
        count = import_records(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Imported {count} university record(s). Evidence still requires verification.")
