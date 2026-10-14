import json
import io
from contextlib import redirect_stderr, redirect_stdout
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from driveway_poc.gemini import Gemini, MODEL
from driveway_poc.imagery import ApiError, RequestLimit, normalize_image
from driveway_poc.download import fetch_image


def response(data):
    result = Mock()
    result.read.return_value = data
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class GeminiTests(unittest.TestCase):
    def client(self, text="OK.", finish="STOP"):
        client = Gemini("SECRET")
        client.opener = Mock()
        client.opener.open.return_value = response(json.dumps({"candidates": [{
            "finishReason": finish, "content": {"parts": [{"text": text}]}}]}).encode())
        return client

    def test_check_auth_model_budget(self):
        client = self.client()
        self.assertEqual(client.check()["status"], "success")
        req = client.opener.open.call_args.args[0]
        self.assertNotIn("SECRET", req.full_url)
        self.assertEqual(req.get_header("X-goog-api-key"), "SECRET")
        self.assertIn(MODEL, req.full_url)
        self.assertEqual(json.loads(req.data)["generationConfig"]["maxOutputTokens"], 256)
        with self.assertRaises(RequestLimit): client.check()
        self.assertEqual(client.opener.open.call_count, 1)

    def test_errors_no_retry_no_secret(self):
        for error in [HTTPError("SECRET", 503, "SECRET", {}, None), URLError("SECRET"), TimeoutError()]:
            client = self.client()
            client.opener.open.side_effect = error
            with self.assertRaises(ApiError) as caught: client.check()
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertEqual(client.opener.open.call_count, 1)

    def test_incomplete_or_bad_response_rejected(self):
        for finish in ["MAX_TOKENS", "SAFETY", None]:
            with self.assertRaises(ApiError): self.client(finish=finish).check()
        client = self.client()
        client.opener.open.return_value = response(b'{}')
        with self.assertRaises(ApiError): client.check()

    def test_classification_is_unverified_and_abstains(self):
        data = {"classification": "likely_resin_bound", "visibility": "limited",
                "observations": "A small surface", "limitations": "Too distant"}
        client = self.client(json.dumps(data))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            path.write_bytes(b"\xff\xd8\xffsynthetic")
            result = client.classify(path)
        self.assertEqual(result["classification"], "unclear")
        self.assertTrue(result["human_review_required"])
        self.assertFalse(result["address_verified"])
        body = json.loads(client.opener.open.call_args.args[0].data)
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")

    def test_obscured_negative_is_unclear(self):
        data = {"classification": "other", "visibility": "limited",
                "observations": "Car hides driveway", "limitations": "Insufficient texture"}
        client = self.client(json.dumps(data))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            path.write_bytes(b"\xff\xd8\xffsynthetic")
            result = client.classify(path)
        self.assertEqual(result["classification"], "unclear")
        self.assertEqual(len(result["image_sha256"]), 64)

    def test_invalid_image_no_request(self):
        client = self.client()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            path.write_text("not an image")
            with self.assertRaises(ValueError): client.classify(path)
        client.opener.open.assert_not_called()

    def test_invalid_classification_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            path.write_bytes(b"\xff\xd8\xffsynthetic")
            for text in ['{}', '[]', '{"classification":"certain_resin"}']:
                with self.assertRaises(ApiError): self.client(text).classify(path)


class DownloadTests(unittest.TestCase):
    def config(self):
        return {"client_owned_account": True, "permissions": {
            k: {"status": "approved"} for k in ("metadata_access", "image_use", "image_storage")}}

    def metadata(self, url="https://scontent.example.fbcdn.net/image.jpg?signed=SECRET"):
        return {"id": "123", "geometry": {"type": "Point", "coordinates": [-0.35, 51.82]},
                "thumb_2048_url": url, "creator": {"username": "synthetic"}}

    def test_download_provenance_no_token_to_cdn_no_overwrite(self):
        opener = Mock()
        opener.open.side_effect = [response(json.dumps(self.metadata()).encode()), response(b"\xff\xd8\xffsynthetic")]
        with tempfile.TemporaryDirectory() as directory, patch('driveway_poc.download.build_opener', return_value=opener):
            result = fetch_image(self.config(), "TOKEN", "123", directory)
            metadata = Path(result["metadata"]).read_text()
            self.assertNotIn("SECRET", metadata)
            self.assertNotIn("TOKEN", metadata)
            self.assertFalse(json.loads(metadata)["ownership_verified"])
            self.assertIsNone(opener.open.call_args_list[1].args[0].get_header("Authorization"))
            with self.assertRaises(ValueError): fetch_image(self.config(), "TOKEN", "123", directory)
        self.assertEqual(opener.open.call_count, 2)

    def test_unknown_bearing_is_not_a_direction(self):
        metadata = self.metadata()
        metadata["compass_angle"] = -1
        self.assertIsNone(normalize_image(metadata)["bearing"])
        metadata["computed_compass_angle"] = 125
        self.assertEqual(normalize_image(metadata)["bearing"], 125)

    def test_untrusted_cdn_not_requested(self):
        opener = Mock()
        opener.open.return_value = response(json.dumps(self.metadata('https://evil.test/image')).encode())
        with tempfile.TemporaryDirectory() as directory, patch('driveway_poc.download.build_opener', return_value=opener):
            with self.assertRaises(ApiError): fetch_image(self.config(), "TOKEN", "123", directory)
        self.assertEqual(opener.open.call_count, 1)


class CommandTests(unittest.TestCase):
    def config(self):
        return {"client_owned_account": True, "permissions": {
            k: {"status": "approved"} for k in ("image_use", "image_storage", "external_ai")}}

    def test_existing_output_never_calls_model(self):
        from driveway_poc.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text("keep")
            with patch('driveway_poc.__main__.load_config', return_value=self.config()), patch('driveway_poc.__main__.Gemini') as model, redirect_stderr(io.StringIO()):
                self.assertEqual(main(['classify-image', 'image.jpg', '--output', str(path)]), 2)
                model.assert_not_called()
            self.assertEqual(path.read_text(), "keep")

    def test_failed_inference_does_not_leave_success_artifact(self):
        from driveway_poc.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            with patch('driveway_poc.__main__.load_config', return_value=self.config()), patch('driveway_poc.__main__.Gemini') as model, redirect_stderr(io.StringIO()):
                model.return_value.classify.side_effect = ApiError("synthetic failure")
                self.assertEqual(main(['classify-image', 'image.jpg', '--output', str(path)]), 2)
            self.assertFalse(path.exists())
