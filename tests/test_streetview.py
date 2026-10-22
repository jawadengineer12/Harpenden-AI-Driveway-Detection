"""Offline tests for the Street View provider: no real network calls."""
import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from driveway_poc.imagery import ApiError, AuthenticationError, RequestLimit
from driveway_poc.results import export_results
from driveway_poc.store import CANDIDATE_FIELDS, connect, import_candidates
from driveway_poc.streetview import (
    StreetView, bearing_to, discover, fetch_and_classify, normalize_metadata,
    safe_image_filename, scan_and_classify, scan_targets,
)

ROOT = Path(__file__).resolve().parents[1]


def response(data):
    result = Mock()
    result.read.return_value = data
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def metadata_payload(pano_id="CAoSLEFGMVFpcFBfakePano", date="2024-09"):
    return {"status": "OK", "pano_id": pano_id, "date": date,
            "location": {"lat": 51.82, "lng": -0.35}, "copyright": "synthetic"}


def classify_result(label="likely_resin_bound"):
    return {"classification": label, "visibility": "usable", "driveways": [],
            "scene_notes": "synthetic", "image_sha256": "a" * 64, "model": "synthetic-model",
            "requests": 1, "usage": {}, "human_review_required": True, "address_verified": False}


class NormalizeTests(unittest.TestCase):
    def test_rejects_missing_panorama(self):
        with self.assertRaises(ValueError):
            normalize_metadata({"status": "ZERO_RESULTS"}, 51.82, -0.35)

    def test_rejects_bad_pano_id_and_bad_date(self):
        with self.assertRaises(ValueError):
            normalize_metadata({**metadata_payload(), "pano_id": "bad id!"}, 51.82, -0.35)
        with self.assertRaises(ValueError):
            normalize_metadata({**metadata_payload(), "date": "2024"}, 51.82, -0.35)

    def test_happy_path_has_month_precision_and_reference(self):
        meta = normalize_metadata(metadata_payload(), 51.82, -0.35)
        self.assertEqual(meta["capture_date_precision"], "month")
        self.assertTrue(meta["captured_at"].startswith("2024-09-01"))
        self.assertIn(meta["pano_id"], meta["reference"])


class BearingTests(unittest.TestCase):
    def test_due_north_and_east(self):
        self.assertEqual(bearing_to(51.0, -0.35, 51.1, -0.35), 0)
        self.assertAlmostEqual(bearing_to(51.0, -0.35, 51.0, -0.34), 90, delta=1)

    def test_scan_targets_covers_both_sides_and_perpendiculars(self):
        meta = {"camera_latitude": 51.0, "camera_longitude": -0.35,
               "property_latitude": 51.1, "property_longitude": -0.35}
        targets = scan_targets(meta)
        self.assertEqual(targets, [0, 180, 90, 270])


class SafeFilenameTests(unittest.TestCase):
    def test_sanitizes_address_and_appends_heading(self):
        self.assertEqual(safe_image_filename("33 The Uplands, AL5 2PA", 180), "33-the-uplands-al5-2pa_h180.jpg")

    def test_blank_or_hostile_label_falls_back(self):
        self.assertEqual(safe_image_filename("", 0), "property_h0.jpg")
        self.assertEqual(safe_image_filename("../../etc/passwd", 90), "etc-passwd_h90.jpg")


class StreetViewClientTests(unittest.TestCase):
    def client(self, limit=2):
        client = StreetView("SECRET", limit)
        client.opener = Mock()
        return client

    def test_metadata_does_not_leak_key_and_rejects_denied(self):
        client = self.client()
        client.opener.open.return_value = response(json.dumps(metadata_payload()).encode())
        payload = client.metadata(51.82, -0.35)
        self.assertEqual(payload["status"], "OK")
        request = client.opener.open.call_args.args[0]
        self.assertIn("key=SECRET", request.full_url)
        client.opener.open.return_value = response(json.dumps({"status": "REQUEST_DENIED"}).encode())
        with self.assertRaises(AuthenticationError):
            client.metadata(51.82, -0.35)

    def test_metadata_at_accepts_free_text_location(self):
        client = self.client()
        client.opener.open.return_value = response(json.dumps(metadata_payload()).encode())
        client.metadata_at("33 The Uplands, AL5 2PA, UK")
        request = client.opener.open.call_args.args[0]
        self.assertIn("Uplands", request.full_url)

    def test_image_bytes_requires_jpeg_signature(self):
        client = self.client()
        client.opener.open.return_value = response(b"not a jpeg")
        with self.assertRaises(ApiError):
            client.image_bytes(51.82, -0.35, 180)

    def test_request_limit_enforced(self):
        client = self.client(limit=1)
        client.opener.open.return_value = response(json.dumps(metadata_payload()).encode())
        client.metadata(51.82, -0.35)
        with self.assertRaises(RequestLimit):
            client.metadata(51.82, -0.35)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.images = self.path / "images"
        self.config = json.loads((ROOT / "config.example.json").read_text())
        self.config.update(provider="streetview", bbox=[-0.36, 51.81, -0.34, 51.83],
                           max_image_age_days=365, client_owned_account=True)
        for permission in self.config["permissions"].values():
            permission.update(status="approved", evidence="SYNTHETIC test permission",
                              reviewed_by="Test", reviewed_on=datetime.now().date().isoformat())
        self.db = connect(self.path / "test.sqlite3", create=True)
        self.addCleanup(self.db.close)
        candidate = {"property_id": "test-1", "street": "Synthetic Street",
                    "latitude": "51.82", "longitude": "-0.35",
                    "property_address": "1 Synthetic Street", "postcode": "TEST",
                    "property_uprn": "000123", "address_source": "Synthetic fixture"}
        candidates = self.path / "candidates.csv"
        with candidates.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS)
            writer.writeheader()
            writer.writerow(candidate)
        import_candidates(self.db, candidates, self.config)

    def discover_one(self):
        opener = Mock()
        opener.open.return_value = response(json.dumps(metadata_payload()).encode())
        with patch("driveway_poc.streetview.build_opener", return_value=opener):
            discover(self.db, self.config, "KEY")
        return opener

    def test_discover_records_metadata_only_never_fetches_pixels(self):
        opener = self.discover_one()
        self.assertEqual(opener.open.call_count, 1)
        row = self.db.execute("SELECT * FROM images").fetchone()
        self.assertEqual(json.loads(row["metadata"])["provider"], "Google Street View")
        prop = self.db.execute("SELECT search_status FROM properties").fetchone()
        self.assertEqual(prop["search_status"], "complete")

    def test_fetch_and_classify_saves_image_named_by_address(self):
        self.discover_one()
        opener = Mock()
        opener.open.return_value = response(b"\xff\xd8\xffsynthetic-streetview-bytes")
        gemini = Mock()
        gemini.classify_bytes.return_value = classify_result()
        with patch("driveway_poc.streetview.build_opener", return_value=opener):
            view_id, label, image_file = fetch_and_classify(
                self.db, self.config, "KEY", gemini, "test-1", 180, images_dir=self.images)
        self.assertEqual(label, "likely_resin_bound")
        self.assertTrue(view_id.endswith("_h180"))
        self.assertEqual(Path(image_file).name, "1-synthetic-street_h180.jpg")
        self.assertEqual(Path(image_file).read_bytes(), b"\xff\xd8\xffsynthetic-streetview-bytes")
        image_row = self.db.execute("SELECT metadata FROM images WHERE image_id=?", (view_id,)).fetchone()
        meta = json.loads(image_row["metadata"])
        self.assertEqual(meta["is_pano"], False)
        self.assertEqual(meta["image_file"], image_file)

    def test_fetch_and_classify_refuses_to_overwrite_existing_image(self):
        self.discover_one()
        self.images.mkdir(parents=True)
        (self.images / "1-synthetic-street_h180.jpg").write_bytes(b"already here")
        opener = Mock()
        opener.open.return_value = response(b"\xff\xd8\xffsynthetic")
        gemini = Mock()
        gemini.classify_bytes.return_value = classify_result()
        with patch("driveway_poc.streetview.build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                fetch_and_classify(self.db, self.config, "KEY", gemini, "test-1", 180, images_dir=self.images)

    def test_fetch_and_classify_can_skip_saving(self):
        self.discover_one()
        opener = Mock()
        opener.open.return_value = response(b"\xff\xd8\xffsynthetic")
        gemini = Mock()
        gemini.classify_bytes.return_value = classify_result()
        with patch("driveway_poc.streetview.build_opener", return_value=opener):
            view_id, label, image_file = fetch_and_classify(
                self.db, self.config, "KEY", gemini, "test-1", 180, images_dir=None)
        self.assertIsNone(image_file)
        self.assertFalse(self.images.exists())

    def test_scan_and_classify_tries_four_headings_and_saves_each(self):
        self.discover_one()
        opener = Mock()
        opener.open.return_value = response(b"\xff\xd8\xffsynthetic")
        gemini = Mock()
        gemini.classify_bytes.side_effect = [
            classify_result("other"), classify_result("likely_resin_bound"),
            classify_result("unclear"), classify_result("no_visible_driveway"),
        ]
        with patch("driveway_poc.streetview.build_opener", return_value=opener):
            views = scan_and_classify(self.db, self.config, "KEY", gemini, "test-1", images_dir=self.images)
        self.assertEqual([v["heading"] for v in views], [0, 180, 90, 270])
        self.assertEqual([v["classification"] for v in views], ["other", "likely_resin_bound", "unclear", "no_visible_driveway"])
        self.assertEqual(len(list(self.images.glob("*.jpg"))), 4)

    def test_scan_and_classify_records_a_per_heading_failure_without_aborting(self):
        self.discover_one()
        opener = Mock()
        opener.open.side_effect = [
            response(b"\xff\xd8\xffsynthetic"),
            ApiError("synthetic Street View failure"),
            response(b"\xff\xd8\xffsynthetic"),
            response(b"\xff\xd8\xffsynthetic"),
        ]
        gemini = Mock()
        gemini.classify_bytes.return_value = classify_result()
        with patch("driveway_poc.streetview.build_opener", return_value=opener):
            views = scan_and_classify(self.db, self.config, "KEY", gemini, "test-1", images_dir=self.images)
        self.assertIsNone(views[1]["classification"])
        self.assertIsNotNone(views[1]["error"])
        self.assertEqual(sum(v["classification"] is not None for v in views), 3)

    def test_fetch_and_classify_requires_discovery_first(self):
        gemini = Mock()
        with self.assertRaises(ValueError):
            fetch_and_classify(self.db, self.config, "KEY", gemini, "test-1", 180, images_dir=self.images)

    def test_fetch_and_classify_blocked_without_external_ai_permission(self):
        self.discover_one()
        self.config["permissions"]["external_ai"]["status"] = "unknown"
        with self.assertRaises(ValueError):
            fetch_and_classify(self.db, self.config, "KEY", Mock(), "test-1", 180, images_dir=self.images)

    def test_fetch_and_classify_blocked_without_image_storage_permission(self):
        self.discover_one()
        self.config["permissions"]["image_storage"]["status"] = "denied"
        with self.assertRaises(ValueError):
            fetch_and_classify(self.db, self.config, "KEY", Mock(), "test-1", 180, images_dir=self.images)

    def test_export_results_requires_image_storage_for_streetview_now(self):
        self.config["permissions"]["image_storage"]["status"] = "denied"
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(ValueError):
                export_results(self.db, self.config, Path(out) / "export")

    def test_export_results_runs_with_all_permissions_approved(self):
        with tempfile.TemporaryDirectory() as out:
            summary = export_results(self.db, self.config, Path(out) / "export")
        self.assertEqual(summary["exported_leads"], 0)
        self.assertEqual(summary["attempted_properties"], 1)


if __name__ == "__main__":
    unittest.main()
