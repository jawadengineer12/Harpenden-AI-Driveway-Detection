"""Google Street View Static API provider.

Street View's published policy generally prohibits pre-fetching, indexing,
storing or caching panorama/image content (only the panorama ID is exempt
from that). This module saves fetched images to disk (see
IMAGES_DIR / fetch_and_classify) on explicit owner instruction, as a
deliberate accepted risk for this non-commercial PoC stage -- not because
that policy question has been resolved. Revisit before any client-facing
or commercial scale-up; see docs/SETUP.md and data/streetview_config.json's
permission evidence for the record of that decision.
"""
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .config import number, require_permissions
from .imagery import ApiError, AuthenticationError, NoRedirects, RequestLimit
from .store import check_study

METADATA_ENDPOINT = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMAGE_ENDPOINT = "https://maps.googleapis.com/maps/api/streetview"
PROVIDER = "Google Street View"
IMAGES_DIR = "outputs/streetview_images"


def safe_image_filename(label, heading):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", label or "").strip("-").lower()[:80]
    return f"{slug or 'property'}_h{heading}.jpg"


def bearing_to(from_lat, from_lon, to_lat, to_lon):
    """Initial compass bearing (degrees, 0-359) from one point toward another."""
    phi1, phi2 = math.radians(from_lat), math.radians(to_lat)
    delta = math.radians(to_lon - from_lon)
    x = math.sin(delta) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta)
    return round(math.degrees(math.atan2(x, y))) % 360


def scan_targets(meta):
    """Headings worth trying for one property: facing it directly, the
    opposite side (the pano may sit across the road from the house), and
    the two perpendiculars (corner plots, side-on driveways)."""
    bearing = bearing_to(meta["camera_latitude"], meta["camera_longitude"],
                         meta["property_latitude"], meta["property_longitude"])
    return [bearing, (bearing + 180) % 360, (bearing + 90) % 360, (bearing + 270) % 360]


class StreetView:
    def __init__(self, key, limit, on_request=lambda: None):
        if not key or any(char.isspace() for char in key):
            raise ValueError("Set STREET_VIEW_API to the client-owned credential")
        self.key = key
        self.limit = limit
        self.requests = 0
        self.on_request = on_request
        self.opener = build_opener(NoRedirects())

    def _get(self, url, params, *, max_bytes):
        for attempt in range(3):
            if self.requests >= self.limit:
                raise RequestLimit("Configured request limit reached")
            self.on_request()
            self.requests += 1
            request = Request(url + "?" + urlencode(params))
            try:
                with self.opener.open(request, timeout=20) as response:
                    raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise ApiError("Street View response exceeds the size limit")
                return raw
            except HTTPError as error:
                status = error.code
                error.close()
                if status in (401, 403):
                    raise AuthenticationError("Street View authentication/access rejected") from None
                if (status == 429 or 500 <= status <= 599) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(f"Street View HTTP {status}; no provider response or credential logged") from None
            except (URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError("Street View network request failed after bounded retries") from None

    def metadata(self, latitude, longitude):
        return self.metadata_at(f"{latitude},{longitude}")

    def metadata_at(self, location):
        raw = self._get(METADATA_ENDPOINT, {"location": location, "key": self.key}, max_bytes=10_000)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ApiError("Invalid JSON metadata response") from None
        if not isinstance(payload, dict) or "status" not in payload:
            raise ApiError("Provider returned an invalid metadata response")
        if payload["status"] in ("REQUEST_DENIED", "INVALID_REQUEST"):
            raise AuthenticationError("Street View metadata request rejected: " + payload["status"])
        return payload

    def image_bytes(self, latitude, longitude, heading, *, fov=90, pitch=-5, size="640x640"):
        raw = self._get(IMAGE_ENDPOINT, {
            "location": f"{latitude},{longitude}", "heading": heading,
            "fov": fov, "pitch": pitch, "size": size, "key": self.key,
        }, max_bytes=8_000_000)
        if not raw.startswith(b"\xff\xd8\xff"):
            raise ApiError("Street View image is not a supported JPEG or exceeds the size limit")
        return raw


def normalize_metadata(payload, latitude, longitude):
    if payload.get("status") != "OK":
        raise ValueError("No Street View panorama available at this location")
    pano_id = payload.get("pano_id")
    if not isinstance(pano_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,128}", pano_id):
        raise ValueError("Invalid or missing Street View panorama ID")
    location = payload.get("location")
    if not isinstance(location, dict):
        raise ValueError("Street View metadata is missing a panorama location")
    lat = number(location.get("lat"), -90, 90, "panorama latitude")
    lon = number(location.get("lng"), -180, 180, "panorama longitude")
    date = payload.get("date")
    captured, precision = None, None
    if date is not None:
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}", date):
            raise ValueError("Unexpected Street View capture-date format")
        captured = date + "-01T00:00:00+00:00"
        precision = "month"
    return {"pano_id": pano_id, "camera_longitude": lon, "camera_latitude": lat,
            "property_longitude": longitude, "property_latitude": latitude,
            "captured_at": captured, "capture_date_precision": precision,
            "reference": "https://www.google.com/maps/@?api=1&map_action=pano&pano=" + pano_id}


def discover(db, config, key, *, retry_incomplete=False):
    require_permissions(config, "metadata_access", "address_use")
    if not config["client_owned_account"]:
        raise ValueError("Client-owned imagery account has not been confirmed")
    check_study(db, config)
    client = StreetView(key, config["max_requests"])
    started = datetime.now(timezone.utc).isoformat()
    with db:
        run_id = db.execute(
            "INSERT INTO runs (started_at,status,config) VALUES (?,'running',?)",
            (started, json.dumps(config, sort_keys=True)),
        ).lastrowid

    def count_request():
        with db:
            db.execute("UPDATE runs SET requests=requests+1 WHERE run_id=?", (run_id,))

    client.on_request = count_request
    run_status = "complete"
    try:
        for prop in db.execute("SELECT * FROM properties ORDER BY property_id").fetchall():
            if prop["search_status"] != "pending" and not (
                retry_incomplete and prop["search_status"] in {"error", "limited"}
            ):
                continue
            status, error = "complete", ""
            try:
                payload = client.metadata(prop["latitude"], prop["longitude"])
                if payload.get("status") != "OK":
                    status, error = "complete", "no_panorama: " + str(payload.get("status"))
                else:
                    meta = normalize_metadata(payload, prop["latitude"], prop["longitude"])
                    image_id = meta["pano_id"]
                    with db:
                        db.execute("INSERT OR IGNORE INTO images VALUES (?,?)",
                                   (image_id, json.dumps({**meta, "provider": PROVIDER}, sort_keys=True)))
                        db.execute("INSERT OR IGNORE INTO candidates VALUES (?,?)",
                                   (prop["property_id"], image_id))
            except (ApiError, ValueError, OverflowError) as exc:
                status = "limited" if isinstance(exc, RequestLimit) else "error"
                error = str(exc) if isinstance(exc, ApiError) else "Invalid provider metadata"
                with db:
                    db.execute("UPDATE properties SET search_status=?,error=? WHERE property_id=?",
                               (status, error, prop["property_id"]))
                if isinstance(exc, (AuthenticationError, RequestLimit)):
                    run_status = "blocked"
                    raise
            with db:
                db.execute("UPDATE properties SET search_status=?,error=? WHERE property_id=?",
                           (status, error, prop["property_id"]))
            if status != "complete":
                run_status = "partial"
    except BaseException:
        if run_status != "blocked":
            run_status = "interrupted"
        raise
    finally:
        with db:
            db.execute("UPDATE runs SET status=?,finished_at=? WHERE run_id=?",
                       (run_status, datetime.now(timezone.utc).isoformat(), run_id))
    return run_id


def fetch_and_classify(db, config, key, gemini, property_id, heading, *, images_dir=IMAGES_DIR):
    require_permissions(config, "metadata_access", "image_use", "image_storage", "external_ai")
    if not config["client_owned_account"]:
        raise ValueError("Client-owned imagery account has not been confirmed")
    check_study(db, config)
    if type(heading) is not int or not 0 <= heading <= 359:
        raise ValueError("Heading must be an integer from 0 to 359")
    prop = db.execute("SELECT * FROM properties WHERE property_id=?", (property_id,)).fetchone()
    if prop is None:
        raise ValueError("Unknown property ID")
    candidate = db.execute("""SELECT i.image_id,i.metadata FROM candidates c JOIN images i USING(image_id)
                              WHERE c.property_id=?""", (property_id,)).fetchone()
    if candidate is None:
        raise ValueError("Run discover-streetview for this property first")
    meta = json.loads(candidate["metadata"])
    if meta.get("provider") != PROVIDER:
        raise ValueError("Linked image is not a Street View candidate")
    client = StreetView(key, 3)  # headroom for _get's own bounded retry loop, not 3 separate images
    data = client.image_bytes(prop["latitude"], prop["longitude"], heading)
    result = gemini.classify_bytes(data)
    image_path = None
    if images_dir is not None:
        directory = Path(images_dir)
        directory.mkdir(parents=True, exist_ok=True)
        label = prop["property_address"] or prop["property_id"]
        image_path = directory / safe_image_filename(label, heading)
        if image_path.exists():
            raise ValueError(f"{image_path} already exists; remove it or choose another directory before re-saving")
        image_path.write_bytes(data)
    del data
    view_id = f"{meta['pano_id']}_h{heading}"
    view_meta = {**meta, "provider": PROVIDER, "bearing": heading, "is_pano": False,
                "image_id": view_id, "image_sha256": result["image_sha256"],
                "image_file": str(image_path) if image_path else None}
    try:
        with db:
            db.execute("INSERT OR IGNORE INTO images VALUES (?,?)", (view_id, json.dumps(view_meta, sort_keys=True)))
            db.execute("INSERT OR IGNORE INTO candidates VALUES (?,?)", (property_id, view_id))
            previous = db.execute("SELECT result FROM classifications WHERE image_id=?", (view_id,)).fetchone()
            encoded = json.dumps(result, sort_keys=True)
            if previous and previous["result"] != encoded:
                db.execute("DELETE FROM material_reviews WHERE image_id=?", (view_id,))
            db.execute("INSERT OR REPLACE INTO classifications VALUES (?,?,?)", (view_id, result["image_sha256"], encoded))
            db.execute("INSERT INTO review_events (recorded_at,payload) VALUES (?,?)", (
                datetime.now(timezone.utc).isoformat(), json.dumps({"event": "streetview_classification", "property_id": property_id,
                                                                  "image_id": view_id, "image_sha256": result["image_sha256"],
                                                                  "result": result}, sort_keys=True)))
    except BaseException:
        if image_path is not None:
            image_path.unlink(missing_ok=True)
        raise
    return view_id, result["classification"], str(image_path) if image_path else None


def scan_and_classify(db, config, key, gemini, property_id, *, images_dir=IMAGES_DIR):
    """Classify a property from several headings instead of one guess: the
    bearing toward the property, its opposite, and both perpendiculars.
    One bad angle (parked car, hedge, wrong house) no longer decides the
    whole result; a per-heading failure is recorded, not fatal to the scan."""
    rows = db.execute("""SELECT i.metadata FROM candidates c JOIN images i USING(image_id)
                        WHERE c.property_id=?""", (property_id,)).fetchall()
    metas = [json.loads(row["metadata"]) for row in rows]
    panos = [m for m in metas if m.get("provider") == PROVIDER and "bearing" not in m]
    if not panos:
        raise ValueError("Run discover-streetview for this property first")
    meta = panos[0]
    views = []
    for heading in scan_targets(meta):
        try:
            view_id, label, image_file = fetch_and_classify(
                db, config, key, gemini, property_id, heading, images_dir=images_dir)
            views.append({"heading": heading, "image_id": view_id, "classification": label,
                         "image_file": image_file, "error": None})
        except ApiError as exc:
            views.append({"heading": heading, "image_id": None, "classification": None,
                         "image_file": None, "error": str(exc)})
    return views
