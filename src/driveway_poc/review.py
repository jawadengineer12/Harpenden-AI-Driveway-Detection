"""Manual feasibility review and internal QA exports, never lead exports."""
import csv
import html
import hashlib
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
    if type(value) in (int, float):
        return text
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


def export_gallery(db, config, images_directory, output):
    """Show each image once, with explicit unverified nearby address candidates."""
    from .gemini import validate_driveways
    require_permissions(config, "metadata_access", "address_use", "image_use", "image_storage")
    check_study(db, config)
    images_directory = Path(images_directory).resolve()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sections, mismatched = [], []
    for row in db.execute("SELECT i.image_id,i.metadata,k.result FROM images i LEFT JOIN classifications k USING(image_id) ORDER BY i.image_id"):
        image = json.loads(row["metadata"])
        filename = Path(image["image_file"]).name if image.get("image_file") else (
            row["image_id"] + ".jpg" if row["image_id"].isascii() and row["image_id"].isdigit() else None)
        if not filename:
            continue
        path = images_directory / filename
        if not path.is_file():
            continue
        if image.get("image_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            mismatched.append(row["image_id"])
            continue
        result = json.loads(row["result"]) if row["result"] else None
        detections = validate_driveways(result["driveways"]) if result and "driveways" in result else []
        boxes, suggestions = [], []
        for detection in detections:
            y1,x1,y2,x2 = detection["box_2d"]
            boxes.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" fill="none" stroke="#ffcc00" stroke-width="3"/><text x="{x1}" y="{max(20,y1-5)}" fill="#ffcc00" font-size="22" stroke="#111" stroke-width="0.5">{detection["detection_id"]}</text>')
            suggestions.append('<li>' + html.escape(detection["detection_id"] + ': ' + detection["classification"] + ' — ' + detection["observations"] + ' Limitations: ' + detection["limitations"]) + '</li>')
        addresses = [html.escape(p["property_address"] + ' (' + p["property_id"] + ')') for p in db.execute('SELECT p.property_id,p.property_address FROM candidates c JOIN properties p USING(property_id) WHERE c.image_id=? ORDER BY p.property_address',(row["image_id"],))]
        source = html.escape(path.as_uri(), quote=True)
        overlay = '<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="Tentative driveway boxes">' + ''.join(boxes) + '</svg>' if boxes else ''
        sections.append('<section><h2>Image ' + html.escape(row["image_id"]) + '</h2><p>Captured ' + html.escape(str(image.get("captured_at"))) + '</p><div class="view"><a href="' + source + '"><img loading="lazy" src="' + source + '" alt="Street image; property ownership unverified"></a>' + overlay + '</div><p>Nearby address candidates — none assigned to a box:</p><ul><li>' + '</li><li>'.join(addresses) + '</li></ul><details><summary>Model suggestions (optional)</summary><ul>' + (''.join(suggestions) or '<li>No per-driveway detections available</li>') + '</ul></details></section>')
    content = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Harpenden driveway evidence review</title><style>body{max-width:1100px;margin:24px auto;padding:0 16px;font:16px/1.5 system-ui;color:#18212b;background:#f5f6f8}section{background:white;padding:20px;margin:24px 0;border:1px solid #ddd}.view{position:relative}.view img{width:100%;height:auto;display:block}.view svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}h2{font-size:20px}summary{cursor:pointer}</style><h1>Driveway evidence review</h1><p>Each image appears once. Addresses listed below it are nearby candidates, not identified houses. Boxes and IDs describe separate model detections; verify each box, house number, boundary and driveway before selecting it for a property. Road/shared access is not evidence of an individual private driveway. Review unclear when texture or ownership cannot be established.</p>' + ''.join(sections) + '</html>'
    with output.open("x", encoding="utf-8") as handle:
        handle.write(content)
    return {"properties": db.execute('SELECT COUNT(*) FROM properties').fetchone()[0], "downloaded_candidate_images": len(sections), "image_hash_mismatches": mismatched}
