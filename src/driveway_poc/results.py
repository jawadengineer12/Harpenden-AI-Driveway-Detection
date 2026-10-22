"""Link evidence, retain image-bound classifications, and export reviewed leads plus QA."""
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from .config import PERMISSIONS, number, require_permissions
from .review import write_csv
from .gemini import validate_driveways
from .store import check_study

CLASSES = {"likely_resin_bound", "other", "unclear", "no_visible_driveway"}
LEAD_FIELDS = ["property_uprn", "property_address", "postcode", "latitude", "longitude",
               "driveway_class", "model_score", "human_review", "address_match_status",
               "imagery_provider", "imagery_date", "imagery_reference", "notes"]


def link_image(db, config, property_id, metadata_path):
    require_permissions(config, "metadata_access", "address_use", "image_use")
    check_study(db, config)
    prop = db.execute("SELECT 1 FROM properties WHERE property_id=?", (property_id,)).fetchone()
    if prop is None:
        raise ValueError("Unknown property ID")
    if Path(metadata_path).stat().st_size > 1_000_000:
        raise ValueError("Image metadata exceeds size limit")
    image = json.loads(Path(metadata_path).read_text(encoding="utf-8-sig"))
    if not isinstance(image, dict):
        raise ValueError("Image metadata must be an object")
    if not isinstance(image.get("image_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", image["image_sha256"]):
        raise ValueError("Downloaded-image provenance must include its SHA-256")
    image_id = image.get("image_id")
    if not isinstance(image_id, str) or not image_id.isascii() or not image_id.isdigit():
        raise ValueError("Invalid image ID")
    if image.get("provider") != "Mapillary" or image.get("reference") != "https://www.mapillary.com/app/?pKey=" + image_id:
        raise ValueError("Expected Mapillary image provenance")
    number(image.get("camera_longitude"), -180, 180, "camera longitude")
    number(image.get("camera_latitude"), -90, 90, "camera latitude")
    if image.get("captured_at") is not None:
        if not isinstance(image["captured_at"], str):
            raise ValueError("Capture timestamp must be a string")
        if datetime.fromisoformat(image["captured_at"]).tzinfo is None:
            raise ValueError("Capture timestamp must include timezone")
    if type(image.get("is_pano")) is not bool:
        raise ValueError("Projection type is required")
    with db:
        previous = db.execute("SELECT metadata FROM images WHERE image_id=?", (image_id,)).fetchone()
        if previous:
            old = json.loads(previous["metadata"])
            for field in ("camera_longitude", "camera_latitude", "captured_at", "is_pano", "reference"):
                if old.get(field) != image.get(field):
                    raise ValueError("Conflicting image provenance; existing evidence preserved")
            if old.get("image_sha256") and old["image_sha256"] != image["image_sha256"]:
                raise ValueError("Conflicting image bytes; existing evidence preserved")
        db.execute("INSERT INTO images VALUES (?,?) ON CONFLICT(image_id) DO UPDATE SET metadata=excluded.metadata", (image_id, json.dumps(image, sort_keys=True)))
        db.execute("INSERT OR IGNORE INTO candidates VALUES (?,?)", (property_id, image_id))
    return image_id


def import_classification(db, config, image_id, result_path, image_path):
    require_permissions(config, "image_use", "image_storage", "external_ai")
    check_study(db, config)
    provenance = db.execute("SELECT metadata FROM images WHERE image_id=?", (image_id,)).fetchone()
    if provenance is None:
        raise ValueError("Link the image to a sample property first")
    if Path(result_path).stat().st_size > 1_000_000 or Path(image_path).stat().st_size > 8_000_000:
        raise ValueError("Classification/image exceeds size limit")
    result = json.loads(Path(result_path).read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("Classification must be an object")
    digest = hashlib.sha256(Path(image_path).read_bytes()).hexdigest()
    if json.loads(provenance["metadata"]).get("image_sha256") != digest:
        raise ValueError("Image bytes do not match linked Mapillary provenance")
    if result.get("image_sha256") != digest:
        raise ValueError("Classification does not match these image bytes")
    if not all(isinstance(result.get(k), str) for k in ("classification", "visibility", "prompt_sha256")):
        raise ValueError("Classification labels and prompt hash must be strings")
    if result.get("classification") not in CLASSES or result.get("visibility") not in {"usable", "limited", "not_visible"}:
        raise ValueError("Invalid classification label/visibility")
    if not isinstance(result.get("model"), str) or not result["model"].strip():
        raise ValueError("Classification model is missing")
    if not re.fullmatch(r"[a-f0-9]{64}", result.get("prompt_sha256", "")):
        raise ValueError("Classification prompt hash is required")
    for key in ("observations", "limitations"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise ValueError("Classification evidence is missing")
    if result["classification"] in {"likely_resin_bound", "other"} and result["visibility"] != "usable":
        result["classification"] = "unclear"
    if "driveways" in result:
        result["driveways"] = validate_driveways(result["driveways"])
    with db:
        previous = db.execute("SELECT result FROM classifications WHERE image_id=?", (image_id,)).fetchone()
        encoded = json.dumps(result, sort_keys=True)
        if previous and previous["result"] != encoded:
            # A changed interpretation invalidates all material approvals for this frame.
            db.execute("DELETE FROM material_reviews WHERE image_id=?", (image_id,))
        db.execute("INSERT OR REPLACE INTO classifications VALUES (?,?,?)", (image_id, digest, encoded))
        db.execute("INSERT INTO review_events (recorded_at,payload) VALUES (?,?)", (
            datetime.now(timezone.utc).isoformat(), json.dumps({"event": "classification_import", "image_id": image_id,
                                                              "image_sha256": digest, "result": result}, sort_keys=True)))
    return result["classification"]


def review_material(db, config, property_id, image_id, driveway_class, reviewer, reviewed_on, evidence, detection_id=None):
    require_permissions(config, "image_use", "address_use")
    check_study(db, config)
    if driveway_class not in CLASSES:
        raise ValueError("Invalid reviewed material class")
    if not all(isinstance(v, str) and v.strip() for v in (reviewer, reviewed_on, evidence)):
        raise ValueError("Material review needs reviewer, date and evidence identifying the driveway")
    if date.fromisoformat(reviewed_on) > date.today():
        raise ValueError("Review date cannot be in the future")
    row = db.execute("""SELECT k.image_sha256,k.result FROM classifications k JOIN candidates c USING(image_id)
                        WHERE c.property_id=? AND c.image_id=?""", (property_id, image_id)).fetchone()
    if row is None:
        raise ValueError("No classification for this property/image pair")
    detections = json.loads(row["result"]).get("driveways", [])
    if not detection_id or detection_id not in {d["detection_id"] for d in detections}:
        raise ValueError("Select an existing driveway detection ID; legacy frame results cannot approve a property")
    duplicate = db.execute("SELECT 1 FROM material_reviews WHERE image_id=? AND detection_id=? AND property_id<>?",
                           (image_id, detection_id, property_id)).fetchone()
    if duplicate:
        raise ValueError("This driveway detection is already assigned to another property; shared ownership remains ambiguous")
    values = (property_id, image_id, row["image_sha256"], driveway_class, reviewer.strip(), reviewed_on, evidence.strip(), detection_id)
    with db:
        db.execute("INSERT OR REPLACE INTO material_reviews VALUES (?,?,?,?,?,?,?,?)", values)
        db.execute("INSERT INTO review_events (recorded_at,payload) VALUES (?,?)", (
            datetime.now(timezone.utc).isoformat(), json.dumps({"event": "material_review", "values": values})))


def export_results(db, config, output):
    require_permissions(config, *PERMISSIONS)
    check_study(db, config)
    if not config["client_owned_account"]:
        raise ValueError("Client-owned accounts must be confirmed")
    if config["max_image_age_days"] is None:
        raise ValueError("Set the agreed image-age limit before exporting leads")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    leads, qa = [], []
    for prop in db.execute("SELECT * FROM properties ORDER BY property_id"):
        pairs = db.execute("""SELECT i.image_id,i.metadata,k.result,k.image_sha256,
            r.visibility,r.property_match,r.address_verified,r.reviewer,
            m.driveway_class,m.image_sha256 AS reviewed_hash,m.reviewer AS material_reviewer,
            m.evidence AS material_evidence,m.detection_id
            FROM candidates c JOIN images i USING(image_id)
            LEFT JOIN classifications k USING(image_id)
            LEFT JOIN reviews r ON r.property_id=c.property_id AND r.image_id=c.image_id
            LEFT JOIN material_reviews m ON m.property_id=c.property_id AND m.image_id=c.image_id
            WHERE c.property_id=? ORDER BY i.image_id""", (prop["property_id"],)).fetchall()
        eligible, reasons, model_labels = [], set(), set()
        for pair in pairs:
            image = json.loads(pair["metadata"])
            result = json.loads(pair["result"]) if pair["result"] else None
            detection = next((d for d in result.get("driveways", []) if d["detection_id"] == pair["detection_id"]), None) if result else None
            failures = []
            if detection is None:
                failures.append("individual_driveway_not_selected")
            if not result:
                failures.append("classification_missing")
            else:
                if detection:
                    model_labels.add(detection["classification"])
                if not detection or detection["classification"] != "likely_resin_bound": failures.append("model_not_positive")
                if not detection or detection["visibility"] != "usable": failures.append("model_visibility_limited")
            if pair["driveway_class"] != "likely_resin_bound" or not pair["material_reviewer"]:
                failures.append("reviewed_positive_missing")
            if pair["reviewed_hash"] != pair["image_sha256"] or not pair["reviewed_hash"]:
                failures.append("material_review_not_bound_to_image")
            if pair["visibility"] != "usable": failures.append("usable_view_not_verified")
            if pair["property_match"] != "verified": failures.append("property_match_not_verified")
            if pair["address_verified"] != "yes" or not pair["reviewer"]:
                failures.append("address_not_verified")
            captured = image.get("captured_at")
            try:
                capture = datetime.fromisoformat(captured)
                if capture.tzinfo is None: raise ValueError()
                age = (datetime.now(timezone.utc).date() - capture.date()).days
                if not 0 <= age <= config["max_image_age_days"]: failures.append("imagery_stale_or_future")
            except (ValueError, TypeError):
                failures.append("capture_date_unknown")
            if image.get("is_pano") is not False:
                failures.append("panorama_view_not_validated")
            if not prop["property_address"] or not prop["postcode"] or not prop["address_source"]:
                failures.append("postal_address_missing")
            if failures:
                reasons.update(failures)
            else:
                eligible.append((pair, image))
        reviewed_classes = {p["driveway_class"] for p in pairs if p["material_reviewer"]}
        if "likely_resin_bound" in reviewed_classes and reviewed_classes - {"likely_resin_bound", "unclear", "no_visible_driveway"}:
            eligible = []
            reasons.add("conflicting_material_reviews")
        if eligible:
            pair, image = eligible[0]
            leads.append({"property_uprn": prop["property_uprn"], "property_address": prop["property_address"],
                          "postcode": prop["postcode"], "latitude": prop["latitude"], "longitude": prop["longitude"],
                          "driveway_class": "likely_resin_bound", "model_score": "", "human_review": "reviewed_likely_resin_bound",
                          "address_match_status": "verified", "imagery_provider": image.get("provider", "Mapillary"),
                          "imagery_date": image["captured_at"], "imagery_reference": image["reference"],
                          "notes": "Driveway " + pair["detection_id"] + " box " + json.dumps(next(d["box_2d"] for d in json.loads(pair["result"])["driveways"] if d["detection_id"] == pair["detection_id"])) + "; " + pair["material_evidence"] + "; visual assessment, not physical material confirmation"})
        if not pairs: reasons.add("no_candidate_images")
        qa.append({"property_id": prop["property_id"], "property_uprn": prop["property_uprn"],
                   "property_address": prop["property_address"], "search_status": prop["search_status"],
                   "candidate_images": len(pairs), "context_classified_images": sum(bool(p["result"]) for p in pairs),
                   "selected_driveway_classes": ";".join(sorted(model_labels)), "exported": "yes" if eligible else "no",
                   "exclusion_reasons": "" if eligible else ";".join(sorted(reasons))})
    write_csv(output / "leads.csv", LEAD_FIELDS, leads)
    write_csv(output / "qa.csv", ["property_id", "property_uprn", "property_address", "search_status", "candidate_images",
                                 "context_classified_images", "selected_driveway_classes", "exported", "exclusion_reasons"], qa)
    summary = {"attempted_properties": len(qa), "exported_leads": len(leads), "excluded_properties": len(qa)-len(leads),
               "unique_classified_images": db.execute("SELECT COUNT(*) FROM classifications").fetchone()[0],
               "properties_with_selected_driveways": sum(bool(r["selected_driveway_classes"]) for r in qa),
               "classification_accuracy": "not_estimated; no independent material ground truth",
               "caveat": "Provisional nearby image links are not verified driveway ownership. Zero leads is a valid outcome."}
    (output / "summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    return summary
