"""Synthetic end-to-end export checks; never use live services."""
import copy
import csv
import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch, Mock
from contextlib import redirect_stdout, redirect_stderr
import io

import test_feasibility as fixtures
from driveway_poc.__main__ import main
from driveway_poc.results import link_image, import_classification, review_material, export_results
from driveway_poc.store import connect
from driveway_poc.review import export_gallery


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorkflowTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.discover_one()
        self.db, self.config, self.path = self.f.db, self.f.config, self.f.path
        self.image_path = self.path / "123.jpg"
        self.image_path.write_bytes(b"\xff\xd8\xffsynthetic image")
        self.digest = hashlib.sha256(self.image_path.read_bytes()).hexdigest()
        metadata = json.loads(self.db.execute("SELECT metadata FROM images").fetchone()[0])
        metadata.update(provider="Mapillary", image_sha256=self.digest)
        self.metadata_path = self.path / "123.json"
        self.metadata_path.write_text(json.dumps(metadata))
        link_image(self.db, self.config, "test-1", self.metadata_path)
        self.result = {"classification": "likely_resin_bound", "visibility": "usable", "model": "synthetic-model",
                       "observations": "Synthetic smooth aggregate driveway", "limitations": "Visual opinion",
                       "image_sha256": self.digest, "prompt_sha256": "a"*64,
                       "driveways": [{"detection_id": "d1", "box_2d": [100,100,500,500],
                                      "classification": "likely_resin_bound", "visibility": "usable",
                                      "observations": "Synthetic driveway region", "limitations": "Visual opinion"}]}
        self.import_result()
        self.counter = 0

    def import_result(self):
        self.result["driveways"][0].update(classification=self.result["classification"], visibility=self.result["visibility"])
        path = self.path / "result.json"
        path.write_text(json.dumps(self.result))
        return import_classification(self.db, self.config, "123", path, self.image_path)

    def approve(self, label="likely_resin_bound"):
        self.f.save_reviews(self.f.approve(self.f.review_rows()))
        review_material(self.db, self.config, "test-1", "123", label,
                        "Synthetic human reviewer", date.today().isoformat(), "Synthetic driveway region confirmed", detection_id="d1")

    def export(self):
        self.counter += 1
        output = self.path / ("export-"+str(self.counter))
        summary = export_results(self.db, self.config, output)
        with (output / "leads.csv").open(encoding="utf-8-sig", newline="") as f:
            leads = list(csv.DictReader(f))
        with (output / "qa.csv").open(encoding="utf-8-sig", newline="") as f:
            qa = list(csv.DictReader(f))
        return summary, leads, qa

    def test_end_to_end_reviewed_positive_and_property_coordinates(self):
        self.approve()
        summary, leads, qa = self.export()
        self.assertEqual(summary["exported_leads"], 1)
        self.assertEqual(summary["attempted_properties"], len(qa))
        self.assertEqual(leads[0]["property_uprn"], "000123")
        self.assertEqual(float(leads[0]["longitude"]), -0.35)
        self.assertEqual(leads[0]["model_score"], "")
        self.assertEqual(qa[0]["exported"], "yes")

    def test_unreviewed_positive_exports_zero(self):
        summary, leads, qa = self.export()
        self.assertEqual(leads, [])
        self.assertEqual(summary["excluded_properties"], 1)
        self.assertIn("property_match_not_verified", qa[0]["exclusion_reasons"])
        self.assertIn("reviewed_positive_missing", qa[0]["exclusion_reasons"])

    def test_ambiguous_address_obstruction_and_unverified_address_rejected(self):
        self.approve()
        for field, value in [("property_match", "ambiguous"), ("address_verified", "no"), ("visibility", "obstructed")]:
            with self.subTest(field=field):
                self.db.execute("UPDATE reviews SET " + field + "=?", (value,))
                self.assertEqual(self.export()[1], [])
                self.f.save_reviews(self.f.approve(self.f.review_rows()))

    def test_stale_unknown_future_and_panorama_rejected(self):
        self.approve()
        original = json.loads(self.db.execute("SELECT metadata FROM images").fetchone()[0])
        for changes in [{"captured_at": None}, {"captured_at": "2000-01-01T00:00:00+00:00"},
                        {"captured_at": "2099-01-01T00:00:00+00:00"}, {"is_pano": True}]:
            with self.subTest(changes=changes):
                updated = dict(original, **changes)
                self.db.execute("UPDATE images SET metadata=?", (json.dumps(updated),))
                self.assertEqual(self.export()[1], [])
        self.db.execute("UPDATE images SET metadata=?", (json.dumps(original),))

    def test_every_missing_permission_blocks_export(self):
        self.approve()
        for scope in self.config["permissions"]:
            config = copy.deepcopy(self.config)
            config["permissions"][scope]["status"] = "unknown"
            with self.assertRaises(ValueError):
                export_results(self.db, config, self.path / scope)
        config = copy.deepcopy(self.config)
        config["max_image_age_days"] = None
        with self.assertRaises(ValueError): export_results(self.db, config, self.path / "age")

    def test_image_mismatch_and_prompt_missing_rejected(self):
        self.result["image_sha256"] = "b"*64
        with self.assertRaises(ValueError): self.import_result()
        self.result["image_sha256"] = self.digest
        self.result.pop("prompt_sha256")
        with self.assertRaises(ValueError): self.import_result()
        self.image_path.write_bytes(b"different image")
        with self.assertRaises(ValueError): self.import_result()

    def test_selected_driveway_not_whole_scene_controls_export(self):
        self.result["driveways"].append({"detection_id":"d2", "box_2d":[500,500,800,900],
                                       "classification":"other", "visibility":"usable",
                                       "observations":"Separate block driveway", "limitations":"Visual only"})
        self.import_result()
        self.f.save_reviews(self.f.approve(self.f.review_rows()))
        review_material(self.db,self.config,"test-1","123","likely_resin_bound","Reviewer",date.today().isoformat(),"Selected d2",detection_id="d2")
        self.assertEqual(self.export()[1], [])
        review_material(self.db,self.config,"test-1","123","likely_resin_bound","Reviewer",date.today().isoformat(),"Selected d1",detection_id="d1")
        self.assertEqual(len(self.export()[1]), 1)

    def test_missing_detection_and_duplicate_property_assignment_rejected(self):
        with self.assertRaises(ValueError):
            review_material(self.db,self.config,"test-1","123","likely_resin_bound","Reviewer",date.today().isoformat(),"No region")
        self.approve()
        self.db.execute("INSERT INTO properties SELECT 'test-2',street,latitude,longitude,'2 Test',postcode,'000124',address_source,search_status,error FROM properties WHERE property_id='test-1'")
        self.db.execute("INSERT INTO candidates VALUES ('test-2','123')")
        with self.assertRaisesRegex(ValueError, 'already assigned'):
            review_material(self.db,self.config,"test-2","123","likely_resin_bound","Reviewer",date.today().isoformat(),"Same region",detection_id="d1")
        gallery=self.path/'unique_gallery.html'
        stats=export_gallery(self.db,self.config,self.path,gallery)
        self.assertEqual(stats['downloaded_candidate_images'],1)
        self.assertEqual(gallery.read_text().count('<img '),1)
        self.assertIn('<rect ',gallery.read_text())

    def test_legacy_frame_result_cannot_approve_a_property(self):
        self.result.pop('driveways')
        path=self.path/'legacy.json';path.write_text(json.dumps(self.result))
        import_classification(self.db,self.config,'123',path,self.image_path)
        with self.assertRaises(ValueError):
            review_material(self.db,self.config,'test-1','123','likely_resin_bound','Reviewer',date.today().isoformat(),'No detected region',detection_id='d1')
        self.assertEqual(self.export()[1],[])

    def test_malformed_result_types_are_validation_errors(self):
        original = copy.deepcopy(self.result)
        for field in ("classification", "visibility", "prompt_sha256"):
            self.result = dict(original, **{field: []})
            with self.assertRaises(ValueError): self.import_result()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM classifications").fetchone()[0], 1)

    def test_changed_classification_invalidates_material_review(self):
        self.approve()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM material_reviews").fetchone()[0], 1)
        self.import_result()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM material_reviews").fetchone()[0], 1)
        self.result["classification"] = "other"
        self.import_result()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM material_reviews").fetchone()[0], 0)
        self.assertEqual(self.export()[1], [])

    def test_limited_visibility_and_reviewed_negative_rejected(self):
        self.result["visibility"] = "limited"
        self.assertEqual(self.import_result(), "unclear")
        self.approve()
        self.assertEqual(self.export()[1], [])
        self.result["visibility"] = "usable"
        self.import_result()
        self.approve(label="other")
        self.assertEqual(self.export()[1], [])

    def test_no_overwrite_and_formula_safe_notes(self):
        self.approve()
        self.db.execute("UPDATE material_reviews SET evidence=?", ("=HYPERLINK(unsafe)",))
        _, leads, _ = self.export()
        self.assertFalse(leads[0]["notes"].startswith("="))
        self.assertIn("=HYPERLINK", leads[0]["notes"])
        output = self.path / "existing"
        output.mkdir()
        with self.assertRaises(FileExistsError): export_results(self.db, self.config, output)

    def test_conflicting_provenance_preserves_existing(self):
        metadata = json.loads(self.metadata_path.read_text())
        metadata["camera_latitude"] = 52
        self.metadata_path.write_text(json.dumps(metadata))
        with self.assertRaises(ValueError): link_image(self.db, self.config, "test-1", self.metadata_path)
        self.assertEqual(json.loads(self.db.execute("SELECT metadata FROM images").fetchone()[0])["camera_latitude"], 51.82)

    def test_gallery_escapes_addresses_and_preserves_existing_file(self):
        self.db.execute("UPDATE properties SET property_address=?", ('<script>unsafe</script>',))
        path = self.path / "gallery.html"
        result = export_gallery(self.db, self.config, self.path, path)
        self.assertEqual(result["downloaded_candidate_images"], 1)
        text = path.read_text()
        self.assertNotIn('<script>', text)
        self.assertIn('&lt;script&gt;', text)
        with self.assertRaises(FileExistsError): export_gallery(self.db, self.config, self.path, path)

    def test_conflicting_views_block_property_and_duplicate_views_do_not_duplicate_leads(self):
        metadata = json.loads(self.metadata_path.read_text())
        metadata.update(image_id="124", reference="https://www.mapillary.com/app/?pKey=124")
        extra = self.path / "124.json"
        extra.write_text(json.dumps(metadata))
        link_image(self.db, self.config, "test-1", extra)
        result_path = self.path / "result.json"
        import_classification(self.db, self.config, "124", result_path, self.image_path)
        self.approve()
        review_material(self.db, self.config, "test-1", "124", "likely_resin_bound", "Test reviewer", date.today().isoformat(), "Same driveway second view", detection_id="d1")
        self.assertEqual(len(self.export()[1]), 1)
        review_material(self.db, self.config, "test-1", "124", "other", "Test reviewer", date.today().isoformat(), "Conflicting material interpretation", detection_id="d1")
        _, leads, qa = self.export()
        self.assertEqual(leads, [])
        self.assertIn('conflicting_material_reviews', qa[0]['exclusion_reasons'])

    def test_gallery_does_not_show_different_image_bytes(self):
        self.image_path.write_bytes(b"wrong cached image")
        stats=export_gallery(self.db,self.config,self.path,self.path/'mismatched.html')
        self.assertEqual(stats['downloaded_candidate_images'],0)
        self.assertEqual(stats['image_hash_mismatches'],['123'])

    def test_schema_one_upgrades_without_losing_properties(self):
        self.db.execute("PRAGMA user_version=1")
        self.db.commit()
        reopened = connect(self.path / "test.sqlite3")
        try:
            self.assertEqual(reopened.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(reopened.execute("SELECT COUNT(*) FROM properties").fetchone()[0], 1)
        finally: reopened.close()


class LookupTests(unittest.TestCase):
    def test_denied_permission_or_existing_output_prevents_paid_lookup(self):
        with tempfile.TemporaryDirectory() as directory, patch('driveway_poc.__main__.OSPlaces') as client:
            path = Path(directory) / "addresses.json"
            config = {"client_owned_account": True, "permissions": {"address_use": {"status": "unknown"}}}
            args = ['lookup-addresses', '--longitude=-0.35', '--latitude=51.82', '--output', str(path)]
            with patch('driveway_poc.__main__.load_config', return_value=config), redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)
                config['permissions']['address_use']['status'] = 'approved'
                path.write_text('preserve')
                self.assertEqual(main(args), 2)
            client.assert_not_called()
            self.assertEqual(path.read_text(), 'preserve')
