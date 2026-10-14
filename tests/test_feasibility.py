"""Offline tests: all imagery and property records here are synthetic."""
import copy
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from driveway_poc.__main__ import main
from driveway_poc.config import load_config
from driveway_poc.imagery import (
    ApiError, AuthenticationError, Mapillary, RequestLimit, discover,
    next_cursor, normalize_image, search_bbox,
)
from driveway_poc.review import (
    EDITABLE, REVIEW_FIELDS, export_review, import_review, report, safe_cell, write_csv,
)
from driveway_poc.store import CANDIDATE_FIELDS, connect, import_candidates

ROOT = Path(__file__).resolve().parents[1]


def image(image_id="123"):
    return {
        "id": image_id, "geometry": {"type": "Point", "coordinates": [-0.35, 51.82]},
        "captured_at": int(datetime.now(timezone.utc).timestamp() * 1000),
        "compass_angle": 0, "is_pano": False, "sequence": "synthetic-sequence",
    }


def response(payload):
    handle = Mock()
    handle.read.return_value = json.dumps(payload).encode()
    handle.__enter__ = Mock(return_value=handle)
    handle.__exit__ = Mock(return_value=False)
    return handle


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.config = json.loads((ROOT / "config.example.json").read_text())
        self.config.update(bbox=[-0.36, 51.81, -0.34, 51.83],
                           max_image_age_days=365, client_owned_account=True)
        for permission in self.config["permissions"].values():
            permission.update(status="approved", evidence="SYNTHETIC test permission",
                              reviewed_by="Test", reviewed_on=datetime.now().date().isoformat())
        self.db = connect(self.path / "test.sqlite3", create=True)
        self.addCleanup(self.db.close)
        self.candidate = {
            "property_id": "test-1", "street": "Synthetic Street",
            "latitude": "51.82", "longitude": "-0.35",
            "property_address": "1 Synthetic Street", "postcode": "TEST",
            "property_uprn": "000123", "address_source": "Synthetic fixture",
        }
        self.candidates = self.path / "candidates.csv"
        self.write_candidates([self.candidate])

    def write_candidates(self, rows):
        with self.candidates.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def seed(self, rows=None):
        if rows is not None:
            self.write_candidates(rows)
        import_candidates(self.db, self.candidates, self.config)

    def discover_one(self):
        self.seed()
        with patch("driveway_poc.imagery.Mapillary.page", return_value={"data": [image()]}):
            discover(self.db, self.config, "SYNTHETIC_TOKEN")

    def review_rows(self):
        path = self.path / "review.csv"
        export_review(self.db, path)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def approve(self, rows):
        for row in rows:
            row.update(visibility="usable", property_match="verified", address_verified="yes",
                       reviewer="Independent synthetic reviewer",
                       reviewed_on=datetime.now().date().isoformat(),
                       evidence="Synthetic fixture: driveway region and address confirmed")
        return rows

    def save_reviews(self, rows):
        path = self.path / "reviews.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return import_review(self.db, path, self.config)

    def test_config_requires_explicit_permissions_and_valid_numbers(self):
        example = load_config(ROOT / "config.example.json")
        self.assertEqual(example["permissions"]["metadata_access"]["status"], "unknown")
        for field, value in [("search_radius_m", float("nan")), ("max_requests", True),
                             ("max_image_age_days", 0), ("client_owned_account", "true")]:
            candidate = copy.deepcopy(self.config)
            candidate[field] = value
            path = self.path / "invalid.json"
            path.write_text(json.dumps(candidate))
            with self.assertRaises(ValueError):
                load_config(path)
        self.config["permissions"]["metadata_access"]["evidence"] = ""
        path.write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ValueError, "evidence"):
            load_config(path)

    def test_frozen_sample_and_idempotent_import(self):
        self.seed()
        self.seed()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM properties").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT property_uprn FROM properties").fetchone()[0], "000123")
        self.candidate["latitude"] = "51.821"
        self.write_candidates([self.candidate])
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.seed()

    def test_invalid_candidate_batch_is_atomic(self):
        bad = dict(self.candidate, property_id="test-2", latitude="nan", property_address="2 Test")
        self.write_candidates([self.candidate, bad])
        with self.assertRaises(ValueError):
            self.seed()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM properties").fetchone()[0], 0)

    def test_outside_area_duplicate_property_and_sample_limit_rejected(self):
        for rows in [
            [dict(self.candidate, latitude="52")],
            [self.candidate, dict(self.candidate, property_id="test-2")],
            [dict(self.candidate, property_id=str(i)) for i in range(21)],
        ]:
            self.write_candidates(rows)
            with self.assertRaises(ValueError):
                self.seed()

    def test_permissions_account_and_token_block_before_network(self):
        self.seed()
        for scope in ("metadata_access", "address_use"):
            config = copy.deepcopy(self.config)
            config["permissions"][scope]["status"] = "unknown"
            with patch("driveway_poc.imagery.Mapillary.page") as request:
                with self.assertRaises(ValueError):
                    discover(self.db, config, "SYNTHETIC_TOKEN")
                request.assert_not_called()
        for config, token in [(dict(self.config, client_owned_account=False), "TOKEN"),
                              (self.config, None)]:
            with self.assertRaises(ValueError):
                discover(self.db, config, token)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    def test_discovery_resume_and_metadata_not_ownership(self):
        self.discover_one()
        with patch("driveway_poc.imagery.Mapillary.page") as request:
            discover(self.db, self.config, "SYNTHETIC_TOKEN")
            request.assert_not_called()
        summary = report(self.db, self.config, self.path / "out")
        self.assertEqual(summary["properties_with_candidate_images"], 1)
        self.assertEqual(summary["reviewed_feasible_properties"], 0)
        self.assertEqual(summary["gate"], "blocked")
        self.assertFalse((self.path / "out/leads.csv").exists())
        self.assertEqual(json.loads((self.path / "out/summary.json").read_text()), summary)

    def test_no_images_is_not_a_negative_classification(self):
        self.seed()
        with patch("driveway_poc.imagery.Mapillary.page", return_value={"data": []}):
            discover(self.db, self.config, "TOKEN")
        report(self.db, self.config, self.path / "out")
        text = (self.path / "out/feasibility.csv").read_text(encoding="utf-8-sig")
        self.assertIn("no_metadata_found_in_search_area", text)
        self.assertNotIn("likely_other", text)

    def test_pagination_dedup_and_partial_retry(self):
        self.seed()
        config = dict(self.config, max_pages_per_property=1)
        next_url = "https://graph.mapillary.com/images?after=cursor&access_token=SECRET"
        with patch("driveway_poc.imagery.Mapillary.page",
                   return_value={"data": [image(), image()], "paging": {"next": next_url}}):
            discover(self.db, config, "TOKEN")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM images").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT search_status FROM properties").fetchone()[0], "limited")
        with patch("driveway_poc.imagery.Mapillary.page", return_value={"data": [image()]}) as request:
            discover(self.db, config, "TOKEN")
            request.assert_not_called()
            discover(self.db, config, "TOKEN", retry_incomplete=True)
        self.assertEqual(self.db.execute("SELECT search_status FROM properties").fetchone()[0], "complete")

    def test_malformed_metadata_does_not_become_no_images(self):
        self.seed()
        with patch("driveway_poc.imagery.Mapillary.page", return_value={"data": [image(), {"id": "x"}]}):
            discover(self.db, self.config, "TOKEN")
        self.assertEqual(self.db.execute("SELECT search_status FROM properties").fetchone()[0], "error")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM images").fetchone()[0], 0)

    def test_auth_failure_stops_remaining_properties(self):
        rows = [self.candidate, dict(self.candidate, property_id="test-2",
                                    property_address="2 Test", property_uprn="000124")]
        self.seed(rows)
        with patch("driveway_poc.imagery.Mapillary.page",
                   side_effect=AuthenticationError("Authentication rejected")) as request:
            with self.assertRaises(AuthenticationError):
                discover(self.db, self.config, "TOKEN")
            self.assertEqual(request.call_count, 1)
        self.assertEqual([row[0] for row in self.db.execute(
            "SELECT search_status FROM properties ORDER BY property_id")], ["error", "pending"])
        self.assertEqual(self.db.execute("SELECT status FROM runs").fetchone()[0], "blocked")

    def test_property_and_camera_coordinates_remain_separate(self):
        self.discover_one()
        prop = self.db.execute("SELECT * FROM properties").fetchone()
        bbox = search_bbox(self.config, prop)
        self.assertLess(bbox[0], prop["longitude"])
        self.assertGreater(bbox[2], prop["longitude"])
        metadata = json.loads(self.db.execute("SELECT metadata FROM images").fetchone()[0])
        self.assertIn("camera_latitude", metadata)
        self.assertNotIn("property_address", metadata)

    def test_review_validation_is_atomic_and_revisions_audited(self):
        self.discover_one()
        rows = self.approve(self.review_rows())
        bad = dict(rows[0], image_id="999")
        with self.assertRaises(ValueError):
            self.save_reviews(rows + [bad])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)
        self.save_reviews(rows)
        self.save_reviews(rows)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM review_events").fetchone()[0], 1)
        rows[0]["property_match"] = "ambiguous"
        self.save_reviews(rows)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM review_events").fetchone()[0], 2)
        self.assertEqual(report(self.db, self.config, self.path / "out")["reviewed_feasible_properties"], 0)

    def test_review_cannot_invent_an_address(self):
        self.candidate["property_address"] = ""
        self.write_candidates([self.candidate])
        self.discover_one()
        rows = self.approve(self.review_rows())
        rows[0]["property_address"] = "Invented address in read-only cell"
        with self.assertRaisesRegex(ValueError, "Read-only"):
            self.save_reviews(rows)

    def test_unknown_and_future_capture_dates_do_not_qualify(self):
        self.discover_one()
        self.save_reviews(self.approve(self.review_rows()))
        for timestamp in [None, int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp() * 1000)]:
            with self.db:
                metadata = normalize_image(dict(image(), captured_at=timestamp))
                self.db.execute("UPDATE images SET metadata=?", (json.dumps(metadata),))
            self.assertEqual(report(self.db, self.config, self.path / "out")["reviewed_feasible_properties"], 0)

    def test_five_reviewed_properties_on_two_streets_and_all_rights_required(self):
        rows = [dict(self.candidate, property_id=f"test-{i}", property_address=f"{i} Test",
                     property_uprn=str(i), street="Synthetic Street A" if i < 3 else "Synthetic Street B")
                for i in range(1, 6)]
        self.seed(rows)
        with patch("driveway_poc.imagery.Mapillary.page",
                   side_effect=[{"data": [image(str(i))]} for i in range(1, 6)]):
            discover(self.db, self.config, "TOKEN")
        self.save_reviews(self.approve(self.review_rows()))
        summary = report(self.db, self.config, self.path / "out")
        self.assertEqual(summary["reviewed_feasible_properties"], 5)
        self.assertEqual(summary["gate"], "ready_for_live_classification_smoke_test")
        for scope in ("external_ai", "derived_export", "image_storage", "address_export"):
            config = copy.deepcopy(self.config)
            config["permissions"][scope]["status"] = "unknown"
            self.assertEqual(report(self.db, config, self.path / "out")["gate"], "blocked")

    def test_csv_formula_safety_unicode_and_atomic_replace(self):
        path = self.path / "safe.csv"
        write_csv(path, ["value"], [{"value": "=HYPERLINK(1)"}, {"value": "Élan House"}])
        self.assertIn("'=HYPERLINK", path.read_text(encoding="utf-8-sig"))
        self.assertIn("Élan", path.read_text(encoding="utf-8-sig"))
        original = path.read_bytes()
        with patch("driveway_poc.review.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                write_csv(path, ["value"], [{"value": "new"}])
        self.assertEqual(path.read_bytes(), original)
        for value in ["=1", " +1", "\tformula", "-1", "@SUM(1)", "\rtest"]:
            self.assertTrue(safe_cell(value).startswith("'"))

    def test_status_and_help_are_offline_and_do_not_leak_token(self):
        with patch.dict("os.environ", {"MAPILLARY_ACCESS_TOKEN": "SECRET"}):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--config", str(ROOT / "config.example.json"), "status"])
            self.assertEqual(code, 0)
            self.assertNotIn("SECRET", output.getvalue())
            self.assertTrue(json.loads(output.getvalue())["mapillary_token_present"])



    def test_request_ledger_counts_real_mocked_transport_attempts(self):
        self.seed()
        with patch("driveway_poc.imagery.build_opener") as opener:
            opener.return_value.open.side_effect = [
                HTTPError("https://graph.mapillary.com/images", 500, "", {}, None),
                response({"data": [image()]}),
            ]
            with patch("driveway_poc.imagery.time.sleep"):
                discover(self.db, self.config, "SYNTHETIC_TOKEN")
        self.assertEqual(self.db.execute("SELECT requests FROM runs").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT status FROM runs").fetchone()[0], "complete")

    def test_request_limit_leaves_unsearched_properties_pending(self):
        rows = [self.candidate, dict(self.candidate, property_id="test-2",
                                    property_address="2 Test", property_uprn="124")]
        self.seed(rows)
        with patch("driveway_poc.imagery.build_opener") as opener:
            opener.return_value.open.return_value = response({"data": []})
            with self.assertRaises(RequestLimit):
                discover(self.db, dict(self.config, max_requests=1), "TOKEN")
        self.assertEqual(self.db.execute("SELECT requests FROM runs").fetchone()[0], 1)
        self.assertEqual([row[0] for row in self.db.execute(
            "SELECT search_status FROM properties ORDER BY property_id")], ["complete", "limited"])

    def test_duplicate_review_and_invented_address_are_rejected(self):
        self.discover_one()
        rows = self.approve(self.review_rows())
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.save_reviews(rows + rows)
        rows[0]["address_verified"] = "true"
        with self.assertRaisesRegex(ValueError, "yes or no"):
            self.save_reviews(rows)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)

    def test_missing_postal_address_cannot_be_verified(self):
        self.candidate["postcode"] = ""
        self.write_candidates([self.candidate])
        self.discover_one()
        with self.assertRaisesRegex(ValueError, "missing postal"):
            self.save_reviews(self.approve(self.review_rows()))

    def test_ambiguous_or_stale_images_cannot_pass(self):
        self.discover_one()
        rows = self.approve(self.review_rows())
        rows[0]["property_match"] = "ambiguous"
        self.save_reviews(rows)
        self.assertEqual(report(self.db, self.config, self.path / "out")["reviewed_feasible_properties"], 0)
        rows[0]["property_match"] = "verified"
        self.save_reviews(rows)
        metadata = normalize_image(dict(image(), captured_at=int(
            (datetime.now(timezone.utc) - timedelta(days=366)).timestamp() * 1000)))
        with self.db:
            self.db.execute("UPDATE images SET metadata=?", (json.dumps(metadata),))
        self.assertEqual(report(self.db, self.config, self.path / "out")["reviewed_feasible_properties"], 0)

    def test_interruption_keeps_saved_metadata_and_records_run(self):
        self.seed()
        with patch("driveway_poc.imagery.Mapillary.page", side_effect=[
            {"data": [image()], "paging": {"next": "https://graph.mapillary.com/images?after=next"}},
            KeyboardInterrupt(),
        ]):
            with self.assertRaises(KeyboardInterrupt):
                discover(self.db, self.config, "TOKEN")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM images").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT status FROM runs").fetchone()[0], "interrupted")
        self.assertEqual(self.db.execute("SELECT search_status FROM properties").fetchone()[0], "pending")

    def test_repeated_cursor_stops_instead_of_looping(self):
        self.seed()
        page = {"data": [image()], "paging": {"next": "https://graph.mapillary.com/images?after=loop"}}
        with patch("driveway_poc.imagery.Mapillary.page", return_value=page) as request:
            discover(self.db, self.config, "TOKEN")
            self.assertEqual(request.call_count, 2)
        self.assertEqual(self.db.execute("SELECT search_status FROM properties").fetchone()[0], "error")

    def test_changed_search_scope_cannot_reuse_previous_results(self):
        self.discover_one()
        with self.assertRaisesRegex(ValueError, "settings changed"):
            discover(self.db, dict(self.config, search_radius_m=100), "TOKEN")

    def test_csv_output_is_the_complete_attempted_property_denominator(self):
        self.seed()
        summary = report(self.db, self.config, self.path / "out")
        self.assertEqual(summary["properties_in_sample"], 1)
        self.assertEqual(summary["properties_searched"], 0)
        text = (self.path / "out/feasibility.csv").read_text(encoding="utf-8-sig")
        self.assertIn("not_searched", text)



    def test_cli_end_to_end_and_review_template_preserves_edits(self):
        config_path = self.path / "config.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        prefix = ["--config", str(config_path), "--db", str(self.path / "test.sqlite3")]
        review_path = self.path / "operator-review.csv"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(prefix + ["import-candidates", str(self.candidates)]), 0)
            with patch.dict("os.environ", {"MAPILLARY_ACCESS_TOKEN": "SYNTHETIC_TOKEN"}):
                with patch("driveway_poc.imagery.build_opener") as opener:
                    opener.return_value.open.return_value = response({"data": [image()]})
                    self.assertEqual(main(prefix + ["discover"]), 0)
            self.assertEqual(main(prefix + ["review-template", "--output", str(review_path)]), 0)
            before = review_path.read_bytes()
            self.assertEqual(main(prefix + ["review-template", "--output", str(review_path)]), 2)
            self.assertEqual(review_path.read_bytes(), before)
            with review_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = self.approve(list(csv.DictReader(handle)))
            with review_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            self.assertEqual(main(prefix + ["import-review", str(review_path)]), 0)
            self.assertEqual(main(prefix + ["report", "--output", str(self.path / "report")]), 0)
        summary = json.loads((self.path / "report/summary.json").read_text())
        self.assertEqual(summary["metadata_requests"], 1)
        self.assertEqual(summary["reviewed_feasible_properties"], 1)
        self.assertEqual(summary["gate"], "blocked")
        self.assertFalse((self.path / "report/leads.csv").exists())


class TransportTests(unittest.TestCase):
    def test_auth_header_not_query_and_budget_includes_retries(self):
        client = Mapillary("SECRET", 2)
        client.opener = Mock()
        client.opener.open.side_effect = [
            HTTPError("https://graph.mapillary.com/images?access_token=SECRET", 429, "", {}, None),
            response({"data": []}),
        ]
        with patch("driveway_poc.imagery.time.sleep"):
            self.assertEqual(client.page([-0.36, 51.81, -0.34, 51.83]), {"data": []})
        self.assertEqual(client.requests, 2)
        request = client.opener.open.call_args.args[0]
        self.assertNotIn("SECRET", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "OAuth SECRET")
        with self.assertRaises(RequestLimit):
            client.page([-0.36, 51.81, -0.34, 51.83])

    def test_auth_errors_not_retried_or_echoed(self):
        client = Mapillary("SECRET", 5)
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError("https://example.com/SECRET", 401, "SECRET", {}, None)
        with self.assertRaises(AuthenticationError) as caught:
            client.page([0, 0, 0.001, 0.001])
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(client.requests, 1)

    def test_network_failure_has_bounded_retries(self):
        client = Mapillary("SECRET", 10)
        client.opener = Mock()
        client.opener.open.side_effect = URLError("SECRET")
        with patch("driveway_poc.imagery.time.sleep"):
            with self.assertRaises(ApiError) as caught:
                client.page([0, 0, 0.001, 0.001])
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(client.requests, 3)

    def test_pagination_cannot_redirect_credentials(self):
        for url in ["http://graph.mapillary.com/images?after=a",
                    "https://evil.example/images?after=a",
                    "https://graph.mapillary.com@evil.example/images?after=a",
                    "https://graph.mapillary.com/token?after=a",
                    "https://graph.mapillary.com/images?access_token=SECRET"]:
            with self.assertRaises(ValueError):
                next_cursor({"paging": {"next": url}})
        self.assertEqual(next_cursor({"paging": {"next":
            "https://graph.mapillary.com/images?after=abc&access_token=SECRET"}}), "abc")

    def test_metadata_handles_zero_bearing_and_missing_date(self):
        result = normalize_image(dict(image(), captured_at=None, computed_compass_angle=0))
        self.assertIsNone(result["captured_at"])
        self.assertEqual(result["bearing"], 0)
        self.assertEqual(result["image_id"], "123")
        with self.assertRaises(ValueError):
            normalize_image(dict(image(), id=True))


if __name__ == "__main__":
    unittest.main()
