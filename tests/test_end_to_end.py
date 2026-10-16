"""Run the public CLI workflow with synthetic HTTP responses and no network."""
import csv
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from driveway_poc.__main__ import main
from driveway_poc.store import CANDIDATE_FIELDS, connect
from test_feasibility import ROOT, image
from test_gemini import response


class FullWorkflowTests(unittest.TestCase):
    def test_cli_from_address_lookup_to_reviewed_export_and_resume(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            config = json.loads((ROOT / 'config.example.json').read_text())
            config.update(bbox=[-0.36, 51.81, -0.34, 51.83],
                          max_image_age_days=730, client_owned_account=True)
            for permission in config['permissions'].values():
                permission.update(status='approved', evidence='Synthetic test only',
                                  reviewed_by='Test', reviewed_on=date.today().isoformat())
            config_path, db_path = root / 'config.json', root / 'test.sqlite3'
            config_path.write_text(json.dumps(config))
            stack.enter_context(patch.dict(os.environ, {
                'MAPILLARY_ACCESS_TOKEN': 'SYNTHETIC', 'OS_PLACES_API_KEY': 'SYNTHETIC',
                'GEMINI_API_KEY': 'SYNTHETIC'}))
            stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('Unexpected network')))

            def cli(*args, expected=0):
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(output):
                    code = main(['--config', str(config_path), '--db', str(db_path), *map(str, args)])
                self.assertEqual(code, expected, output.getvalue())
                return output.getvalue()

            def rows(path):
                with path.open(encoding='utf-8-sig', newline='') as handle:
                    return list(csv.DictReader(handle))

            def write_rows(path, records, fields):
                with path.open('w', encoding='utf-8', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(records)

            address_payload = {'results': [{'DPA': {
                'UPRN': '00012' + str(i), 'ADDRESS': f'{i} Synthetic Street',
                'POSTCODE': 'TEST', 'LNG': -0.35, 'LAT': 51.82,
                'THOROUGHFARE_NAME': 'Synthetic Street'}} for i in range(1, 4)]}
            os_opener = Mock()
            os_opener.open.return_value = response(json.dumps(address_payload).encode())
            with patch('driveway_poc.addresses.build_opener', return_value=os_opener):
                cli('lookup-addresses', '--longitude=-0.35', '--latitude=51.82',
                    '--maxresults', '3', '--output', root / 'addresses.json')
            self.assertEqual(os_opener.open.call_count, 1)
            addresses = json.loads((root / 'addresses.json').read_text())
            self.assertFalse(addresses['ownership_verified'])
            candidates = [dict({key: value for key, value in row.items() if key in CANDIDATE_FIELDS},
                               property_id=f'test-{i}') for i, row in enumerate(addresses['candidates'], 1)]
            write_rows(root / 'candidates.csv', candidates, CANDIDATE_FIELDS)
            cli('status')
            cli('import-candidates', root / 'candidates.csv')
            cli('import-candidates', root / 'candidates.csv')
            metadata = image()
            discovery_opener = Mock()
            discovery_opener.open.return_value = response(json.dumps({'data': [metadata]}).encode())
            with patch('driveway_poc.imagery.build_opener', return_value=discovery_opener):
                cli('discover')
                self.assertEqual(discovery_opener.open.call_count, 3)
                cli('discover')
                self.assertEqual(discovery_opener.open.call_count, 3)

            metadata['thumb_2048_url'] = 'https://synthetic.fbcdn.net/image.jpg'
            download_opener = Mock()
            download_opener.open.side_effect = [response(json.dumps(metadata).encode()),
                                                response(b'\xff\xd8\xffsynthetic')]
            with patch('driveway_poc.download.build_opener', return_value=download_opener):
                cli('fetch-image', '123', '--output', root / 'images')
                cli('fetch-image', '123', '--output', root / 'images', expected=2)
            self.assertEqual(download_opener.open.call_count, 2)
            self.assertIsNone(download_opener.open.call_args_list[1].args[0].get_header('Authorization'))
            for candidate in candidates:
                cli('link-image', candidate['property_id'], root / 'images/123.json')

            detections = [dict(detection_id=f'd{i}', box_2d=[i*100, 100, i*100+90, 500],
                               classification=label, visibility=visibility,
                               observations='Synthetic surface', limitations='Visual evidence only')
                          for i, (label, visibility) in enumerate([
                              ('likely_resin_bound', 'usable'), ('other', 'usable'),
                              ('likely_resin_bound', 'limited')], 1)]
            model_payload = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
                {'text': json.dumps({'driveways': detections, 'scene_notes': 'Synthetic scene'})}]}}]}
            model_opener = Mock()
            model_opener.open.return_value = response(json.dumps(model_payload).encode())
            with patch('driveway_poc.gemini.build_opener', return_value=model_opener):
                cli('classify-image', root / 'images/123.jpg', '--output', root / 'result.json')
                cli('classify-image', root / 'images/123.jpg', '--output', root / 'result.json', expected=2)
            self.assertEqual(model_opener.open.call_count, 1)
            self.assertEqual(json.loads((root / 'result.json').read_text())['driveways'][2]['classification'], 'unclear')
            cli('import-classification', '123', root / 'result.json', root / 'images/123.jpg')
            cli('review-gallery', '--images', root / 'images', '--output', root / 'gallery.html')
            self.assertEqual((root / 'gallery.html').read_text().count('<img '), 1)
            cli('export-results', '--output', root / 'unreviewed')
            self.assertEqual(rows(root / 'unreviewed/leads.csv'), [])
            cli('review-template', '--output', root / 'reviews.csv')
            reviews = rows(root / 'reviews.csv')
            self.assertEqual(len(reviews), 3)
            for row in reviews:
                row.update(visibility='usable', property_match='verified', address_verified='yes',
                           reviewer='Synthetic reviewer', reviewed_on=date.today().isoformat(),
                           evidence='Synthetic property association; not a real approval')
            write_rows(root / 'reviews.csv', reviews, list(reviews[0]))
            cli('import-review', root / 'reviews.csv')
            for i, label in enumerate(['likely_resin_bound', 'other', 'unclear'], 1):
                cli('review-material', f'test-{i}', '123', '--detection-id', f'd{i}', '--class', label,
                    '--reviewer', 'Synthetic reviewer', '--date', date.today().isoformat(),
                    '--evidence', 'Synthetic material review; not a real approval')
            cli('export-results', '--output', root / 'reviewed')
            leads, qa = rows(root / 'reviewed/leads.csv'), rows(root / 'reviewed/qa.csv')
            self.assertEqual(len(leads), 1)
            self.assertEqual(leads[0]['property_uprn'], '000121')
            self.assertEqual([row['exported'] for row in qa], ['yes', 'no', 'no'])
            self.assertEqual(len(qa), 3)
            cli('report', '--output', root / 'feasibility')
            cli('export-results', '--output', root / 'reviewed', expected=2)
            cli('import-classification', '123', root / 'result.json', root / 'images/123.jpg')
            cli('export-results', '--output', root / 'reused')
            self.assertEqual(rows(root / 'reused/leads.csv'), leads)
            changed = json.loads((root / 'result.json').read_text())
            changed['driveways'][0]['classification'] = 'other'
            (root / 'changed.json').write_text(json.dumps(changed))
            cli('import-classification', '123', root / 'changed.json', root / 'images/123.jpg')
            cli('export-results', '--output', root / 'invalidated')
            self.assertEqual(rows(root / 'invalidated/leads.csv'), [])
            with closing(connect(db_path)) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM material_reviews').fetchone()[0], 0)
