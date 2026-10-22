"""Resolve a free-text address to coordinates for the Street View provider.

Street View's own metadata endpoint can geocode a free-text address directly
(a normal, documented use of that API, not a separate scrape). When that
fails -- a wrong/ambiguous house number, or the address isn't in Google's
geocoder -- fall back to the postcode's centroid from postcodes.io (free,
open UK postcode data, no key required) and retry Street View metadata at
that point. The postcode centroid is an approximation, not the exact house;
callers must still manually confirm the camera is pointed at the right
property before relying on a classification.
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .imagery import ApiError, NoRedirects
from .streetview import StreetView, normalize_metadata

POSTCODES_IO = "https://api.postcodes.io/postcodes/"


def postcode_centroid(postcode):
    code = re.sub(r"\s+", "", postcode or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{5,8}", code):
        raise ValueError("Invalid postcode format")
    opener = build_opener(NoRedirects())
    request = Request(POSTCODES_IO + code, headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(100_001)
    except HTTPError as error:
        status = error.code
        error.close()
        raise ApiError(f"Postcode lookup HTTP {status}") from None
    except (URLError, OSError, TimeoutError):
        raise ApiError("Postcode lookup network failure") from None
    if len(raw) > 100_000:
        raise ApiError("Postcode lookup response exceeds size limit")
    try:
        payload = json.loads(raw)
        if payload.get("status") != 200:
            raise ValueError()
        result = payload["result"]
        return float(result["latitude"]), float(result["longitude"])
    except (ValueError, TypeError, KeyError):
        raise ApiError("Postcode not found or invalid response") from None


def resolve_address(key, address, postcode):
    """Return (latitude, longitude, pano_metadata, method). Raises ApiError/ValueError if nothing resolves."""
    client = StreetView(key, 3)
    text = address if postcode is None else f"{address}, {postcode}, UK"
    payload = client.metadata_at(text)
    if payload.get("status") == "OK":
        location = payload["location"]
        meta = normalize_metadata(payload, location["lat"], location["lng"])
        return location["lat"], location["lng"], meta, "address_text"
    if not postcode:
        raise ApiError(f"No Street View panorama for address text ({payload.get('status')}); no postcode to fall back on")
    lat, lon = postcode_centroid(postcode)
    payload = client.metadata(lat, lon)
    if payload.get("status") != "OK":
        raise ApiError(f"No Street View panorama via address text or postcode fallback ({payload.get('status')})")
    meta = normalize_metadata(payload, lat, lon)
    return lat, lon, meta, "postcode_fallback"
