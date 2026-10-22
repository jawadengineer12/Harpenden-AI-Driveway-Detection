"""Offline tests for the address-text -> postcode-centroid fallback geocoder."""
import json
import unittest
from unittest.mock import Mock, patch

from driveway_poc.imagery import ApiError
from driveway_poc.geocode import postcode_centroid, resolve_address


def response(data):
    result = Mock()
    result.read.return_value = data
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def sv_payload(status="OK", lat=51.82, lng=-0.35):
    if status != "OK":
        return {"status": status}
    return {"status": "OK", "pano_id": "CAoSLEFGMVFpcFBfakePano", "date": "2024-09",
            "location": {"lat": lat, "lng": lng}}


class PostcodeCentroidTests(unittest.TestCase):
    def test_rejects_malformed_postcode(self):
        with self.assertRaises(ValueError):
            postcode_centroid("not a postcode!!")

    def test_happy_path(self):
        opener = Mock()
        opener.open.return_value = response(json.dumps({
            "status": 200, "result": {"latitude": 51.8, "longitude": -0.36}}).encode())
        with patch("driveway_poc.geocode.build_opener", return_value=opener):
            lat, lon = postcode_centroid("AL5 2PA")
        self.assertEqual((lat, lon), (51.8, -0.36))
        self.assertIn("AL52PA", opener.open.call_args.args[0].full_url)

    def test_not_found_raises(self):
        opener = Mock()
        opener.open.return_value = response(json.dumps({"status": 404}).encode())
        with patch("driveway_poc.geocode.build_opener", return_value=opener):
            with self.assertRaises(ApiError):
                postcode_centroid("ZZ99 9ZZ")


class ResolveAddressTests(unittest.TestCase):
    def test_resolves_directly_from_address_text_without_fallback(self):
        opener = Mock()
        opener.open.return_value = response(json.dumps(sv_payload()).encode())
        with patch("driveway_poc.streetview.build_opener", return_value=opener):
            lat, lon, meta, method = resolve_address("KEY", "33 The Uplands", "AL5 2PA")
        self.assertEqual(method, "address_text")
        self.assertEqual((lat, lon), (51.82, -0.35))
        self.assertEqual(opener.open.call_count, 1)

    def test_falls_back_to_postcode_when_address_text_fails(self):
        sv_opener = Mock()
        sv_opener.open.side_effect = [
            response(json.dumps(sv_payload("UNKNOWN_ERROR")).encode()),
            response(json.dumps(sv_payload(lat=51.79, lng=-0.36)).encode()),
        ]
        pc_opener = Mock()
        pc_opener.open.return_value = response(json.dumps({
            "status": 200, "result": {"latitude": 51.79, "longitude": -0.36}}).encode())
        with patch("driveway_poc.streetview.build_opener", return_value=sv_opener), \
             patch("driveway_poc.geocode.build_opener", return_value=pc_opener):
            lat, lon, meta, method = resolve_address("KEY", "26 The Uplands", "AL5 2PQ")
        self.assertEqual(method, "postcode_fallback")
        self.assertEqual((lat, lon), (51.79, -0.36))

    def test_no_postcode_and_failed_address_text_raises(self):
        opener = Mock()
        opener.open.return_value = response(json.dumps(sv_payload("ZERO_RESULTS")).encode())
        with patch("driveway_poc.streetview.build_opener", return_value=opener):
            with self.assertRaises(ApiError):
                resolve_address("KEY", "Nowhere Lane", None)


if __name__ == "__main__":
    unittest.main()
