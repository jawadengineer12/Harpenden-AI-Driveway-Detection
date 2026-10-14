"""Configuration and explicit, human-recorded permission evidence."""
import json
import math
from datetime import date
from pathlib import Path

PERMISSIONS = (
    "metadata_access", "address_use", "image_use", "image_storage",
    "external_ai", "derived_export", "address_export",
)


def number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.get("provider") != "mapillary":
        raise ValueError("This milestone supports the existing Mapillary provider only")
    if not isinstance(config.get("study"), str) or not config["study"].strip():
        raise ValueError("study is required")
    for name, low, high in (
        ("max_properties", 1, 20), ("max_requests", 1, 100),
        ("max_pages_per_property", 1, 10), ("search_radius_m", 10, 200),
    ):
        value = config.get(name)
        number(value, low, high, name)
        if not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
    age = config.get("max_image_age_days")
    if age is not None:
        number(age, 1, 36500, "max_image_age_days")
        if not isinstance(age, int):
            raise ValueError("max_image_age_days must be an integer")
    bounds = config.get("bbox")
    if bounds is not None:
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError("bbox must be [west, south, east, north]")
        west, south, east, north = bounds
        for value, name in ((west, "west"), (east, "east")):
            number(value, -180, 180, name)
        for value, name in ((south, "south"), (north, "north")):
            number(value, -85, 85, name)
        if not west < east or not south < north:
            raise ValueError("bbox bounds are reversed or empty")
        if east - west > 0.1 or north - south > 0.1:
            raise ValueError("Use a small agreed street area, at most 0.1 degrees per side")
    if type(config.get("client_owned_account")) is not bool:
        raise ValueError("client_owned_account must be true or false")
    permissions = config.get("permissions")
    if not isinstance(permissions, dict) or set(permissions) != set(PERMISSIONS):
        raise ValueError("permissions must contain the scopes in config.example.json")
    for scope, record in permissions.items():
        if not isinstance(record, dict) or record.get("status") not in {"unknown", "approved", "denied"}:
            raise ValueError(f"Invalid permission record: {scope}")
        if record["status"] == "approved":
            for field in ("evidence", "reviewed_by", "reviewed_on"):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    raise ValueError(f"{scope}: approved permission requires {field}")
            reviewed = date.fromisoformat(record["reviewed_on"])
            if reviewed > date.today():
                raise ValueError(f"{scope}: review date cannot be in the future")
    return config


def require_permissions(config, *scopes):
    blocked = [scope for scope in scopes if config["permissions"][scope]["status"] != "approved"]
    if blocked:
        raise ValueError("Permission not established: " + ", ".join(blocked))


def inside(config, longitude, latitude):
    if config["bbox"] is None:
        raise ValueError("Set the agreed study bbox before importing or discovering properties")
    west, south, east, north = config["bbox"]
    return west <= longitude <= east and south <= latitude <= north
