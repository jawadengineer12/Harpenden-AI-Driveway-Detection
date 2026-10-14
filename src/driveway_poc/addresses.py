"""Single-request OS Places lookup; returned addresses are candidates, not ownership proof."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .config import number
from .imagery import ApiError, NoRedirects, RequestLimit


class OSPlaces:
    def __init__(self, key, limit=1):
        if not key or any(c.isspace() for c in key):
            raise ValueError("Set OS_PLACES_API_KEY to the client-owned project key")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("OS request limit must be an integer from 1 to 20")
        self.key, self.limit, self.requests = key, limit, 0
        self.opener = build_opener(NoRedirects())

    def radius(self, longitude, latitude, radius=50, maxresults=1):
        number(longitude, -180, 180, "longitude")
        number(latitude, -90, 90, "latitude")
        number(radius, 1, 200, "radius")
        if type(maxresults) is not int or not 1 <= maxresults <= 20:
            raise ValueError("maxresults must be an integer from 1 to 20")
        if self.requests >= self.limit:
            raise RequestLimit("OS Places request limit reached")
        # OS uses EPSG:4326 axis order (latitude, longitude), unlike Mapillary.
        params = {"point": f"{latitude},{longitude}", "radius": radius,
                  "srs": "EPSG:4326", "output_srs": "EPSG:4326",
                  "dataset": "DPA", "maxresults": maxresults}
        request = Request("https://api.os.uk/search/places/v1/radius?" + urlencode(params),
                          headers={"key": self.key, "Accept": "application/json"})
        self.requests += 1
        # No automatic retries: a failed/timeout request may still consume credits.
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(1_000_001)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            raise ApiError(f"OS Places HTTP {status}; no automatic retry") from None
        except (URLError, OSError, TimeoutError):
            raise ApiError("OS Places network failure; no automatic retry") from None
        if len(raw) > 1_000_000:
            raise ApiError("OS Places response exceeds size limit")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or "error" in payload:
                raise ValueError()
            if "results" not in payload and payload.get("header", {}).get("totalresults") != 0:
                raise ValueError()
            rows = payload.get("results", [])
            if not isinstance(rows, list) or len(rows) > maxresults:
                raise ValueError()
            candidates = []
            for row in rows:
                dpa = row["DPA"]
                uprn = str(dpa["UPRN"])
                if not uprn.isascii() or not uprn.isdigit() or len(uprn) > 12:
                    raise ValueError()
                address, postcode = dpa["ADDRESS"], dpa["POSTCODE"]
                if not all(isinstance(v, str) and v.strip() for v in (address, postcode)):
                    raise ValueError()
                candidates.append({
                    "property_uprn": uprn, "property_address": address, "postcode": postcode,
                    "longitude": number(dpa["LNG"], -180, 180, "longitude"),
                    "latitude": number(dpa["LAT"], -90, 90, "latitude"),
                    "street": dpa.get("THOROUGHFARE_NAME", ""),
                    "address_source": "OS Places DPA", "address_match_status": "unverified",
                })
            return candidates
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise ApiError("Invalid OS Places response; no automatic retry") from None
