"""Bounded, metadata-only discovery from existing Mapillary imagery."""
import json
import math
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import number, require_permissions
from .store import check_study

ENDPOINT = "https://graph.mapillary.com/images"
FIELDS = "id,geometry,computed_geometry,captured_at,compass_angle,computed_compass_angle,is_pano,sequence"


class ApiError(Exception):
    pass


class AuthenticationError(ApiError):
    pass


class RequestLimit(ApiError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Mapillary:
    def __init__(self, token, limit, on_request=lambda: None):
        if not token or any(char.isspace() for char in token):
            raise ValueError("Set MAPILLARY_ACCESS_TOKEN to the client-owned credential")
        self.token = token
        self.limit = limit
        self.requests = 0
        self.on_request = on_request
        self.opener = build_opener(NoRedirects())

    def page(self, bbox, after=None):
        params = {"bbox": ",".join(map(str, bbox)), "fields": FIELDS, "limit": 100}
        if after is not None:
            params["after"] = after
        for attempt in range(3):
            if self.requests >= self.limit:
                raise RequestLimit("Configured request limit reached")
            self.on_request()
            self.requests += 1
            request = Request(ENDPOINT + "?" + urlencode(params), headers={
                "Authorization": "OAuth " + self.token, "Accept": "application/json",
            })
            try:
                with self.opener.open(request, timeout=20) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ApiError("Metadata response exceeds the size limit")
                try:
                    payload = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise ApiError("Invalid JSON metadata response") from None
                if not isinstance(payload, dict) or "error" in payload:
                    raise ApiError("Provider returned an invalid/error response")
                return payload
            except HTTPError as error:
                status = error.code
                error.close()
                if status in (401, 403):
                    raise AuthenticationError("Mapillary authentication/access rejected") from None
                if (status == 429 or 500 <= status <= 599) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(f"Mapillary HTTP {status}; no provider response or credential logged") from None
            except (URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError("Mapillary network request failed after bounded retries") from None


def search_bbox(config, prop):
    latitude, longitude = prop["latitude"], prop["longitude"]
    # shortcut: local spherical approximation for <=200 m searches; use geodesics for wider studies.
    dy = config["search_radius_m"] / 111_000
    dx = dy / math.cos(math.radians(latitude))
    west, south, east, north = config["bbox"]
    return [max(west, longitude - dx), max(south, latitude - dy),
            min(east, longitude + dx), min(north, latitude + dy)]


def normalize_image(item):
    if not isinstance(item, dict):
        raise ValueError("Image metadata must be an object")
    image_id = item.get("id")
    if type(image_id) is int:
        image_id = str(image_id)
    if not isinstance(image_id, str) or not image_id.isascii() or not image_id.isdigit():
        raise ValueError("Image ID must contain digits")
    geometry = item.get("computed_geometry") or item.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise ValueError("Image has no point geometry")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise ValueError("Image coordinates are missing")
    lon = number(coordinates[0], -180, 180, "camera longitude")
    lat = number(coordinates[1], -90, 90, "camera latitude")
    timestamp = item.get("captured_at")
    captured = None
    if timestamp is not None:
        number(timestamp, 0, 253402300799000, "capture timestamp")
        captured = datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat()
    bearing = item.get("computed_compass_angle")
    if bearing is None:
        bearing = item.get("compass_angle")
    # Mapillary uses -1 when the compass bearing is unknown.
    if bearing == -1:
        bearing = None
    if bearing is not None:
        number(bearing, 0, 360, "camera bearing")
    pano = item.get("is_pano")
    if pano is not None and type(pano) is not bool:
        raise ValueError("is_pano must be a boolean or missing")
    sequence = item.get("sequence")
    if isinstance(sequence, dict):
        sequence = sequence.get("id")
    if sequence is not None and not isinstance(sequence, str):
        raise ValueError("Invalid sequence identifier")
    return {"image_id": image_id, "camera_longitude": lon, "camera_latitude": lat,
            "captured_at": captured, "bearing": bearing, "is_pano": pano,
            "sequence": sequence, "reference": "https://www.mapillary.com/app/?pKey=" + image_id}


def next_cursor(payload):
    paging = payload.get("paging")
    if paging is None:
        return None
    if not isinstance(paging, dict):
        raise ValueError("Invalid pagination metadata")
    next_url = paging.get("next")
    if not next_url:
        return None
    if not isinstance(next_url, str):
        raise ValueError("Invalid next-page URL")
    url = urlsplit(next_url)
    if url.scheme != "https" or url.netloc != "graph.mapillary.com" or url.path.rstrip("/") != "/images":
        raise ValueError("Refusing unexpected pagination destination")
    values = parse_qs(url.query).get("after", [])
    if len(values) != 1 or not values[0] or len(values[0]) > 4096:
        raise ValueError("Next-page cursor missing or invalid")
    # Never follow the supplied URL or preserve its possible access_token.
    return values[0]


def discover(db, config, token, *, retry_incomplete=False):
    require_permissions(config, "metadata_access", "address_use")
    if not config["client_owned_account"]:
        raise ValueError("Client-owned imagery account has not been confirmed")
    check_study(db, config)
    client = Mapillary(token, config["max_requests"])
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
            cursor, seen = None, set()
            status, error = "complete", ""
            bbox = search_bbox(config, prop)
            try:
                for _ in range(config["max_pages_per_property"]):
                    payload = client.page(bbox, cursor)
                    rows = payload.get("data")
                    if not isinstance(rows, list) or len(rows) > 100:
                        raise ValueError("Invalid or oversized metadata page")
                    images = [normalize_image(item) for item in rows]
                    for image in images:
                        if not (bbox[0] <= image["camera_longitude"] <= bbox[2] and
                                bbox[1] <= image["camera_latitude"] <= bbox[3]):
                            continue
                        with db:
                            db.execute("INSERT OR IGNORE INTO images VALUES (?,?)",
                                       (image["image_id"], json.dumps(image, sort_keys=True)))
                            db.execute("INSERT OR IGNORE INTO candidates VALUES (?,?)",
                                       (prop["property_id"], image["image_id"]))
                    cursor = next_cursor(payload)
                    if cursor is None:
                        break
                    if cursor in seen:
                        raise ValueError("Provider repeated a pagination cursor")
                    seen.add(cursor)
                else:
                    status, error = "limited", "Page limit reached; search incomplete"
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
