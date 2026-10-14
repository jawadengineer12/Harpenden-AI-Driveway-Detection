"""Retrieve one existing Mapillary image, preserving safe provenance."""
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from .config import require_permissions
from .imagery import ApiError, NoRedirects, normalize_image


def fetch_image(config, token, image_id, directory):
    require_permissions(config, "metadata_access", "image_use", "image_storage")
    if not config["client_owned_account"]:
        raise ValueError("Client-owned imagery account must be confirmed")
    if not token or any(c.isspace() for c in token):
        raise ValueError("Set MAPILLARY_ACCESS_TOKEN")
    if not isinstance(image_id, str) or not image_id.isascii() or not image_id.isdigit():
        raise ValueError("Mapillary image ID must contain digits")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    image_path, metadata_path = directory / (image_id + ".jpg"), directory / (image_id + ".json")
    if image_path.exists() or metadata_path.exists():
        raise ValueError("Image/provenance already exists; reuse it or choose another directory")
    opener = build_opener(NoRedirects())
    params = {"fields": "id,geometry,computed_geometry,captured_at,compass_angle,computed_compass_angle,is_pano,sequence,creator,thumb_2048_url"}
    request = Request("https://graph.mapillary.com/" + image_id + "?" + urlencode(params),
                      headers={"Authorization": "OAuth " + token})
    try:
        with opener.open(request, timeout=25) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ApiError("Mapillary metadata exceeds limit")
        try:
            item = json.loads(raw)
            metadata = normalize_image(item)
            if metadata["image_id"] != image_id:
                raise ValueError()
            url = item["thumb_2048_url"]
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if parsed.scheme != "https" or not host.endswith(".fbcdn.net") or parsed.port not in (None, 443) or parsed.username or parsed.password:
                raise ValueError()
            metadata["creator"] = item.get("creator")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ApiError("Invalid Mapillary image metadata or thumbnail host") from None
        # Do not forward the API token to the image CDN or store its signed URL.
        with opener.open(Request(url), timeout=30) as response:
            data = response.read(8_000_001)
        if len(data) > 8_000_000 or not data.startswith(b"\xff\xd8\xff"):
            raise ApiError("Thumbnail is not a supported JPEG or exceeds 8 MB")
    except HTTPError as error:
        status = error.code
        error.close()
        raise ApiError(f"Mapillary image fetch HTTP {status}; no automatic retry") from None
    except (URLError, OSError, TimeoutError):
        raise ApiError("Mapillary image fetch network failure; no automatic retry") from None
    metadata.update(provider="Mapillary", image_file=image_path.name,
                    attribution="Mapillary / contributor; preserve provider reference and creator",
                    requests=2, ownership_verified=False)
    with image_path.open("xb") as handle:
        handle.write(data)
    try:
        with metadata_path.open("x", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2)
            handle.write("\n")
    except BaseException:
        image_path.unlink()
        raise
    return {"image": str(image_path), "metadata": str(metadata_path), "requests": 2}
