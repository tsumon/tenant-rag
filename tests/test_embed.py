"""All provider tests use stub HTTP responses; no credentials or network needed."""
import concurrent.futures
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import threading
import unittest
from unittest.mock import patch
import urllib.error

from tenantrag.embed import EmbeddingResponseError, FakeEmbedder, OpenAIEmbedder


def response(*vectors):
    return {"data": [{"index": i, "embedding": vector} for i, vector in enumerate(vectors)]}


def body(payload):
    return io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode())


class FakeEmbedderTest(unittest.TestCase):
    def test_is_deterministic_and_normalized(self):
        first = FakeEmbedder().embed(["Hello 腾讯视频", "", "!!!"])
        self.assertEqual(first, FakeEmbedder().embed(["Hello 腾讯视频", "", "!!!"]))
        self.assertAlmostEqual(math.sqrt(sum(x * x for x in first[0])), 1)
        self.assertEqual(first[1:], [[0.0] * 256, [0.0] * 256])

    def test_rejects_invalid_dimensions(self):
        for value in (0, -1, True, 2.5, "256", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FakeEmbedder(value)

    def test_rejects_non_list_or_non_string_input(self):
        for value in (None, "abc", ("abc",), [1], [None]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FakeEmbedder().embed(value)


class OpenAIEmbedderTest(unittest.TestCase):
    def setUp(self):
        self.embedder = OpenAIEmbedder("dummy-test-key", base_url="http://localhost:9999/v1/")

    def _embed(self, payload, texts=None):
        with patch("tenantrag.embed.urllib.request.OpenerDirector.open", return_value=body(payload)):
            return self.embedder.embed(["hello"] if texts is None else texts)

    def test_request_and_out_of_order_batch(self):
        payload = {"data": [{"index": 1, "embedding": [3, 4]},
                            {"index": 0, "embedding": [1, 2]}]}
        with patch("tenantrag.embed.urllib.request.OpenerDirector.open", return_value=body(payload)) as call:
            self.assertEqual(self.embedder.embed(["one", "two"]), [[1.0, 2.0], [3.0, 4.0]])
            self.assertEqual(self.embedder.dim, 2)
            call.assert_called_once()
            request = call.call_args.args[0]
            self.assertEqual(request.full_url, "http://localhost:9999/v1/embeddings")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer dummy-test-key")
            self.assertEqual(json.loads(request.data), {"model": "text-embedding-3-small",
                                                       "input": ["one", "two"], "encoding_format": "float"})
            self.assertEqual(call.call_args.kwargs["timeout"], 30)

    def test_empty_batch_never_calls_provider(self):
        with patch("tenantrag.embed.urllib.request.OpenerDirector.open") as call:
            self.assertEqual(self.embedder.embed([]), [])
            call.assert_not_called()
        self.assertIsNone(self.embedder._dim)

    def test_invalid_input_never_calls_provider(self):
        with patch("tenantrag.embed.urllib.request.OpenerDirector.open") as call:
            for texts in (None, "text", [None], [1], [""], ["   "], ("text",)):
                with self.subTest(texts=texts), self.assertRaises(ValueError):
                    self.embedder.embed(texts)
            call.assert_not_called()

    def test_dimension_probe_is_cached(self):
        with patch("tenantrag.embed.urllib.request.OpenerDirector.open", return_value=body(response([1, 2]))) as call:
            self.assertEqual(self.embedder.dim, 2)
            self.assertEqual(self.embedder.dim, 2)
            call.assert_called_once()

    def test_invalid_response_structure(self):
        for payload in (None, [], {}, {"data": None}, {"data": {}}, {"data": []},
                        {"data": [None]}, {"data": [{"index": 0}]}, response([1], [2])):
            with self.subTest(payload=payload), self.assertRaises(EmbeddingResponseError):
                self._embed(payload)
        self.assertIsNone(self.embedder._dim)

    def test_invalid_indexes(self):
        for index in (None, -1, 1, True, "0", 0.0):
            with self.subTest(index=index), self.assertRaises(EmbeddingResponseError):
                self._embed({"data": [{"index": index, "embedding": [1, 2]}]})
        for indexes in ((0, 0), (1, 1), (0, 2)):
            payload = {"data": [{"index": i, "embedding": [1]} for i in indexes]}
            with self.subTest(indexes=indexes), self.assertRaises(EmbeddingResponseError):
                self._embed(payload, ["one", "two"])

    def test_invalid_vector_values(self):
        for vector in ([], None, "base64", {}, ["1"], [None], [True], [float("nan")],
                       [float("inf")], [float("-inf")], [1e39], [10**400], [[1]]):
            with self.subTest(vector=vector), self.assertRaises(EmbeddingResponseError):
                self._embed(response(vector))
        self.assertIsNone(self.embedder._dim)

    def test_mixed_batch_dimensions_do_not_pin_cache(self):
        with self.assertRaises(EmbeddingResponseError):
            self._embed(response([1], [2, 3]), ["one", "two"])
        self.assertIsNone(self.embedder._dim)
        self.assertEqual(self._embed(response([1, 2, 3])), [[1.0, 2.0, 3.0]])

    def test_dimension_change_is_rejected(self):
        self._embed(response([1, 2]))
        with self.assertRaisesRegex(EmbeddingResponseError, "dimension changed"):
            self._embed(response([1]))
        self.assertEqual(self.embedder.dim, 2)

    def test_concurrent_first_requests_cannot_pin_conflicting_dimensions(self):
        barrier = threading.Barrier(2)

        def provider(request, timeout):
            text = json.loads(request.data)["input"][0]
            barrier.wait(timeout=5)
            return body(response([1] if text == "one" else [1, 2]))

        def embed(text):
            try:
                return self.embedder.embed([text])
            except EmbeddingResponseError:
                return "dimension-error"

        with patch("tenantrag.embed.urllib.request.OpenerDirector.open", side_effect=provider):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(embed, ["one", "two"]))
        self.assertEqual(results.count("dimension-error"), 1)

    def test_invalid_json_is_sanitized(self):
        for payload in (b"not-json private-payload", b"\xff"):
            with self.subTest(payload=payload), self.assertRaises(EmbeddingResponseError) as error:
                self._embed(payload)
            self.assertNotIn("private-payload", str(error.exception))

    def test_oversized_response_is_rejected(self):
        with patch.object(self.embedder, "_MAX_RESPONSE_BYTES", 8):
            with self.assertRaisesRegex(EmbeddingResponseError, "size limit"):
                self._embed(b"x" * 9)

    def test_network_errors_are_not_retried(self):
        with patch("tenantrag.embed.urllib.request.OpenerDirector.open", side_effect=urllib.error.URLError("offline")) as call:
            with self.assertRaises(urllib.error.URLError):
                self.embedder.embed(["hello"])
            call.assert_called_once()

    def test_redirect_does_not_forward_authorization_or_input(self):
        requests = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(self.path)
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(302)
                self.send_header("Location", "/untrusted-destination")
                self.end_headers()

            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            embedder = OpenAIEmbedder("dummy-test-key", base_url=f"http://127.0.0.1:{server.server_port}/v1")
            with self.assertRaisesRegex(EmbeddingResponseError, "redirected"):
                embedder.embed(["synthetic test input"])
            self.assertEqual(requests, ["/v1/embeddings"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

    def test_invalid_configuration(self):
        for kwargs in ({"api_key": ""}, {"api_key": None}, {"model": ""}, {"model": None},
                       {"base_url": "file:///tmp/test"}, {"base_url": "relative"},
                       {"base_url": "https://user:pass@example.com/v1"},
                       {"base_url": "https://example.com/v1?key=private"},
                       {"base_url": "https://example.com/v1#fragment"},
                       {"base_url": None}, {"timeout": 0}, {"timeout": -1},
                       {"timeout": True}, {"timeout": float("nan")}, {"timeout": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                OpenAIEmbedder(**{"api_key": "dummy-test-key", **kwargs})


if __name__ == "__main__":
    unittest.main()
