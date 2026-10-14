"""OS tests use synthetic responses and never consume credits."""
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from driveway_poc.addresses import OSPlaces
from driveway_poc.imagery import ApiError, RequestLimit


class AddressTests(unittest.TestCase):
    def client(self, payload):
        client = OSPlaces("SYNTHETIC_SECRET")
        response = Mock()
        response.read.return_value = json.dumps(payload).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        client.opener = Mock()
        client.opener.open.return_value = response
        return client

    def test_normalization_auth_and_cap(self):
        client = self.client({"results": [{"DPA": {
            "UPRN": "000123", "ADDRESS": "1 SYNTHETIC STREET", "POSTCODE": "TEST",
            "LNG": -0.35, "LAT": 51.82}}]})
        result = client.radius(-0.35, 51.82)
        self.assertEqual(result[0]["property_uprn"], "000123")
        self.assertEqual(result[0]["address_match_status"], "unverified")
        request = client.opener.open.call_args.args[0]
        self.assertNotIn("SYNTHETIC_SECRET", request.full_url)
        self.assertEqual(request.get_header("Key"), "SYNTHETIC_SECRET")
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["point"], ["51.82,-0.35"])
        self.assertEqual(query["srs"], ["EPSG:4326"])
        self.assertEqual(result[0]["longitude"], -0.35)
        self.assertEqual(result[0]["latitude"], 51.82)
        self.assertEqual(query["maxresults"], ["1"])
        with self.assertRaises(RequestLimit):
            client.radius(-0.35, 51.82)
        self.assertEqual(client.opener.open.call_count, 1)

    def test_errors_never_retry_or_leak(self):
        for error in [HTTPError("secret", 429, "secret", {}, None), URLError("secret")]:
            client = self.client({})
            client.opener.open.side_effect = error
            with self.assertRaises(ApiError) as caught:
                client.radius(-0.35, 51.82)
            self.assertNotIn("secret", str(caught.exception))
            self.assertEqual(client.opener.open.call_count, 1)
            self.assertEqual(client.requests, 1)

    def test_bad_responses(self):
        for payload in [{}, {"results": "bad"}, {"results": [{}]}, {"error": "secret"}]:
            with self.assertRaises(ApiError):
                self.client(payload).radius(-0.35, 51.82)
        self.assertEqual(self.client({"header": {"totalresults": 0}}).radius(-0.35, 51.82), [])

    def test_invalid_input_costs_nothing(self):
        client = self.client({})
        for kwargs in [{"longitude": float("nan"), "latitude": 51},
                       {"longitude": 0, "latitude": 51, "maxresults": 100}]:
            with self.assertRaises(ValueError):
                client.radius(**kwargs)
        client.opener.open.assert_not_called()
