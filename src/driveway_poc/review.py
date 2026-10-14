"""Manual feasibility review and internal QA exports, never lead exports."""
import csv
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from .config import PERMISSIONS, require_permissions
from .store import check_study

REVIEW_FIELDS = [
    "property_id", "image_id", "property_address", "street", "imagery_reference",
    "captured_at", "is_pano", "visibility", "property_match", "address_verified",
    "reviewer", "reviewed_on", "evidence",
]
EDITABLE = ["visibility", "property_match", "address_verified", "reviewer", "reviewed_on", "evidence"]


def safe_cell(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def write_csv(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", newline="", encoding="utf-8-sig", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: safe_cell(row.get(key)) for key in fields})
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def export_review(db, path):
    rows = []
    for candidate in db.execute("""
        SELECT p.property_id, p.property_address, p.street, i.image_id, i.metadata,
               r.visibility, r.property_match, r.address_verified,
               r.reviewer, r.reviewed_on, r.evidence
        FROM candidates c JOIN properties p USING(property_id)
        JOIN images i USING(image_id)
        LEFT JOIN reviews r ON r.property_id=c.property_id AND r.image_id=c.image_id
        ORDER BY p.property_id, i.image_id
    """):
        row = dict(candidate)
        image = json.loads(row.pop("metadata"))
        row.update(imagery_reference=image["reference"], captured_at=image["captured_at"],
                   is_pano=image["is_pano"])
        for field in EDITABLE:
            if row[field] is None:
                row[field] = ""
        rows.append(row)
    write_csv(path, REVIEW_FIELDS, rows)
    return len(rows)


def import_review(db, path, config):
    require_permissions(config, "metadata_access", "address_use", "image_use")
    check_study(db, config)
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != REVIEW_FIELDS:
            raise ValueError("Review headers must match review-template output")
        rows = list(reader)
    accepted, seen = [], set()
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Review CSV contains missing or extra cells")
        key = row["property_id"], row["image_id"]
        if key in seen:
            raise ValueError("Duplicate property/image review")
        seen.add(key)
        if not db.execute("SELECT 1 FROM candidates WHERE property_id=? AND image_id=?", key).fetchone():
            raise ValueError("Review refers to an undiscovered property/image pair")
        original = db.execute("""
            SELECT p.property_address,p.street,i.metadata FROM candidates c
            JOIN properties p USING(property_id) JOIN images i USING(image_id)
            WHERE c.property_id=? AND c.image_id=?
        """, key).fetchone()
        image = json.loads(original["metadata"])
        expected = {"property_address": original["property_address"], "street": original["street"],
                    "imagery_reference": image["reference"], "captured_at": image["captured_at"],
                    "is_pano": image["is_pano"]}
        if any(row[field] != safe_cell(value) for field, value in expected.items()):
            raise ValueError("Read-only evidence columns changed; regenerate the review template")
        if not any(row[field].strip() for field in EDITABLE):
            continue
        for field in EDITABLE:
            row[field] = row[field].strip()
        if row["visibility"] not in {"usable", "obstructed", "unclear", "not_visible"}:
            raise ValueError("Invalid visibility decision")
        if row["property_match"] not in {"verified", "ambiguous", "wrong_property"}:
            raise ValueError("Invalid property-match decision")
        if row["address_verified"] not in {"yes", "no"}:
            raise ValueError("address_verified must be yes or no")
        for field in ("reviewer", "reviewed_on", "evidence"):
            if not row[field]:
                raise ValueError(f"Review requires {field}")
        if date.fromisoformat(row["reviewed_on"]) > date.today():
            raise ValueError("Review date cannot be in the future")
        prop = db.execute("SELECT property_address,postcode FROM properties WHERE property_id=?", key[:1]).fetchone()
        if row["address_verified"] == "yes" and not (prop["property_address"] and prop["postcode"]):
            raise ValueError("Cannot verify a missing postal address/postcode")
        accepted.append(row)
    with db:
        for row in accepted:
            values = [row["property_id"], row["image_id"]] + [row[field] for field in EDITABLE]
            previous = db.execute(
                "SELECT * FROM reviews WHERE property_id=? AND image_id=?", values[:2]
            ).fetchone()
            if previous and list(previous) == values:
                continue
            db.execute(
                "INSERT INTO review_events (recorded_at,payload) VALUES (?,?)",
                (datetime.now(timezone.utc).isoformat(), json.dumps(dict(zip(
                    ["property_id", "image_id"] + EDITABLE, values)), sort_keys=True)),
            )
            db.execute("INSERT OR REPLACE INTO reviews VALUES (?,?,?,?,?,?,?,?)", values)
    return len(accepted)


def report(db, config, output):
    check_study(db, config)
    require_permissions(config, "metadata_access", "address_use")
    rows, qualified_streets = [], set()
    qualifying = 0
    for prop in db.execute("SELECT * FROM properties ORDER BY property_id"):
        images = db.execute("""
            SELECT i.metadata,r.* FROM candidates c JOIN images i USING(image_id)
            LEFT JOIN reviews r ON r.property_id=c.property_id AND r.image_id=c.image_id
            WHERE c.property_id=?
        """, (prop["property_id"],)).fetchall()
        usable, reviewed, fresh = 0, 0, 0
        for item in images:
            image = json.loads(item["metadata"])
            if item["reviewer"] is not None:
                reviewed += 1
            captured = image["captured_at"]
            age = None if captured is None else (
                datetime.now(timezone.utc).date() - datetime.fromisoformat(captured).date()
            ).days
            is_fresh = age is not None and config["max_image_age_days"] is not None and (
                0 <= age <= config["max_image_age_days"]
            )
            fresh += int(is_fresh)
            if (is_fresh and item["visibility"] == "usable" and
                    item["property_match"] == "verified" and item["address_verified"] == "yes"):
                usable += 1
        if usable:
            qualifying += 1
            qualified_streets.add(prop["street"].casefold().strip())
        status = prop["search_status"]
        if usable:
            outcome = "reviewed_feasible_example"
        elif status == "pending":
            outcome = "not_searched"
        elif status in {"limited", "error"}:
            outcome = "search_incomplete"
        elif not images:
            outcome = "no_metadata_found_in_search_area"
        elif not reviewed:
            outcome = "needs_manual_review"
        elif not fresh:
            outcome = "freshness_unresolved_or_unsuitable"
        else:
            outcome = "no_verified_usable_match"
        rows.append({
            "property_id": prop["property_id"], "street": prop["street"],
            "property_address": prop["property_address"], "search_status": status,
            "candidate_images": len(images), "reviewed_images": reviewed,
            "fresh_images": fresh, "usable_verified_views": usable,
            "outcome": outcome, "error": prop["error"],
        })
    blockers = [f"permission:{scope}" for scope in PERMISSIONS
                if config["permissions"][scope]["status"] != "approved"]
    if not config["client_owned_account"]:
        blockers.append("client_owned_account")
    if config["max_image_age_days"] is None:
        blockers.append("freshness_policy")
    if qualifying < 5 or len(qualified_streets) < 2:
        blockers.append("need_5_reviewed_examples_across_2_streets")
    summary = {
        "purpose": "internal feasibility audit, not leads or classification accuracy",
        "properties_in_sample": len(rows),
        "properties_searched": sum(row["search_status"] != "pending" for row in rows),
        "searches_complete": sum(row["search_status"] == "complete" for row in rows),
        "properties_with_candidate_images": sum(row["candidate_images"] > 0 for row in rows),
        "reviewed_feasible_properties": qualifying,
        "reviewed_feasible_streets": len(qualified_streets),
        "metadata_requests": db.execute("SELECT COALESCE(SUM(requests),0) FROM runs").fetchone()[0],
        "gate": "ready_for_live_classification_smoke_test" if not blockers else "blocked",
        "blockers": blockers,
        "caveat": "Nearby camera metadata does not prove driveway visibility or ownership. "
                  "Permission records are human assertions, not independently verified legal clearance.",
    }
    output = Path(output)
    write_csv(output / "feasibility.csv", list(rows[0]), rows)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    try:
        os.replace(temporary, output / "summary.json")
    finally:
        temporary.unlink(missing_ok=True)
    return summary
