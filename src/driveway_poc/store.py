"""Frozen candidate samples and transactional audit storage."""
import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .config import inside, number, require_permissions

CANDIDATE_FIELDS = [
    "property_id", "street", "latitude", "longitude",
    "property_address", "postcode", "property_uprn", "address_source",
]


def connect(path, *, create=False):
    path = Path(path)
    if not create and not path.is_file():
        raise ValueError("No study database. Import the agreed candidates first.")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1, 2, 3):
        db.close()
        raise ValueError("Unsupported database schema version")
    if version == 0 and db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
        db.close()
        raise ValueError("Refusing to modify an unrelated SQLite database")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS study (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS properties (
            property_id TEXT PRIMARY KEY, street TEXT NOT NULL,
            latitude REAL NOT NULL, longitude REAL NOT NULL,
            property_address TEXT NOT NULL, postcode TEXT NOT NULL,
            property_uprn TEXT NOT NULL, address_source TEXT NOT NULL,
            search_status TEXT NOT NULL DEFAULT 'pending',
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS images (
            image_id TEXT PRIMARY KEY, metadata TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidates (
            property_id TEXT NOT NULL REFERENCES properties(property_id),
            image_id TEXT NOT NULL REFERENCES images(image_id),
            PRIMARY KEY(property_id, image_id)
        );
        CREATE TABLE IF NOT EXISTS reviews (
            property_id TEXT NOT NULL, image_id TEXT NOT NULL,
            visibility TEXT NOT NULL, property_match TEXT NOT NULL,
            address_verified TEXT NOT NULL, reviewer TEXT NOT NULL,
            reviewed_on TEXT NOT NULL, evidence TEXT NOT NULL,
            PRIMARY KEY(property_id, image_id),
            FOREIGN KEY(property_id, image_id) REFERENCES candidates(property_id, image_id)
        );
        CREATE TABLE IF NOT EXISTS review_events (
            event_id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
            finished_at TEXT, status TEXT NOT NULL,
            requests INTEGER NOT NULL DEFAULT 0, config TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS classifications (
            image_id TEXT PRIMARY KEY REFERENCES images(image_id),
            image_sha256 TEXT NOT NULL, result TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS material_reviews (
            property_id TEXT NOT NULL, image_id TEXT NOT NULL,
            image_sha256 TEXT NOT NULL, driveway_class TEXT NOT NULL,
            reviewer TEXT NOT NULL, reviewed_on TEXT NOT NULL, evidence TEXT NOT NULL,
            PRIMARY KEY(property_id,image_id),
            FOREIGN KEY(property_id,image_id) REFERENCES candidates(property_id,image_id)
        );
        PRAGMA user_version = 3;
    """)
    if "detection_id" not in {row[1] for row in db.execute("PRAGMA table_info(material_reviews)")}:
        db.execute("ALTER TABLE material_reviews ADD COLUMN detection_id TEXT NOT NULL DEFAULT ''")
        db.commit()
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS material_review_detection ON material_reviews(image_id,detection_id) WHERE detection_id<>''")
    return db


def sample_settings(config):
    return json.dumps({key: config[key] for key in (
        "study", "provider", "bbox", "search_radius_m", "max_properties"
    )}, sort_keys=True)


def check_study(db, config):
    row = db.execute("SELECT value FROM study WHERE key='settings'").fetchone()
    if row is None or row["value"] != sample_settings(config):
        raise ValueError("Study settings changed; use a new database for a different sample or search area")


def import_candidates(db, path, config):
    require_permissions(config, "address_use")
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != CANDIDATE_FIELDS:
            raise ValueError("Candidate headers must match examples/candidates.csv")
        rows = list(reader)
    if not 1 <= len(rows) <= config["max_properties"]:
        raise ValueError(f"Expected 1-{config['max_properties']} candidate properties")
    ids, uprns, addresses = set(), set(), set()
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Candidate CSV contains missing or extra cells")
        row.update({key: value.strip() for key, value in row.items()})
        for field in ("property_id", "street", "address_source"):
            if not row[field]:
                raise ValueError(f"Candidate requires {field}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", row["property_id"]):
            raise ValueError("property_id must be 1-64 safe letters, digits, dots, underscores or hyphens")
        if row["property_id"] in ids:
            raise ValueError("Duplicate property_id")
        ids.add(row["property_id"])
        uprn = row["property_uprn"]
        if uprn and (not uprn.isascii() or not uprn.isdigit() or len(uprn) > 12):
            raise ValueError("UPRN must be a string of up to 12 digits")
        address = " ".join((row["property_address"] + " " + row["postcode"]).casefold().split())
        if uprn and uprn in uprns or row["property_address"] and address in addresses:
            raise ValueError("Duplicate property UPRN or address")
        if uprn:
            uprns.add(uprn)
        if row["property_address"]:
            addresses.add(address)
        row["latitude"] = number(float(row["latitude"]), -85, 85, "latitude")
        row["longitude"] = number(float(row["longitude"]), -180, 180, "longitude")
        if not inside(config, row["longitude"], row["latitude"]):
            raise ValueError("A candidate is outside the agreed study bbox")
    canonical = json.dumps(sorted(rows, key=lambda item: item["property_id"]), sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    previous = db.execute("SELECT value FROM study WHERE key='sample_hash'").fetchone()
    if previous:
        check_study(db, config)
        if previous["value"] != digest:
            raise ValueError("Candidate sample is frozen; use a new database to change it")
        return len(rows)
    with db:
        db.executemany(
            "INSERT INTO properties (" + ",".join(CANDIDATE_FIELDS) + ") VALUES (?,?,?,?,?,?,?,?)",
            [[row[field] for field in CANDIDATE_FIELDS] for row in rows],
        )
        db.executemany("INSERT INTO study VALUES (?,?)", [
            ("settings", sample_settings(config)), ("sample_hash", digest),
        ])
    return len(rows)
