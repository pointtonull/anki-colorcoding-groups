import contextlib
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import embeddings  # noqa: E402

KEY = "sk-test-SECRET-1234"


@contextlib.contextmanager
def serve(respond):
    """A real HTTP server on localhost. `respond(request)` returns (status, body, extra headers); `request` has
    .path, .headers and .body (the parsed JSON). Yields (base_url, list of the requests received)."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            request = type("Request", (), {})()
            request.path, request.headers = self.path, self.headers
            request.body = json.loads(self.rfile.read(length) or b"null")
            received.append(request)
            status, body, headers = respond(request)
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received
    finally:
        server.shutdown()
        server.server_close()


def vector_for(text):
    return [float(len(text)), float(sum(map(ord, text)) % 97), 1.0]


def openai_reply(request, shuffle=False):
    items = [{"object": "embedding", "index": i, "embedding": vector_for(t)} for i, t in enumerate(request.body["input"])]
    return 200, {"object": "list", "data": items[::-1] if shuffle else items, "model": request.body["model"]}, {}


class RequestTests(unittest.TestCase):
    def test_sends_the_openai_request_and_returns_vectors_in_order(self):
        with serve(openai_reply) as (base, received):
            vectors = embeddings.request_embeddings(["alpha", "be", "gamma ray"], base, "my-model", KEY)
        self.assertEqual(vectors, [vector_for("alpha"), vector_for("be"), vector_for("gamma ray")])
        (request,) = received
        self.assertEqual(request.path, "/v1/embeddings")
        self.assertEqual(request.body, {"model": "my-model", "input": ["alpha", "be", "gamma ray"]})
        self.assertEqual(request.headers["Authorization"], f"Bearer {KEY}")
        self.assertEqual(request.headers["Content-Type"], "application/json")

    def test_no_key_means_no_authorization_header(self):
        with serve(openai_reply) as (base, received):
            embeddings.request_embeddings(["a"], base, "m")
        self.assertIsNone(received[0].headers["Authorization"])

    def test_the_full_embeddings_url_is_accepted_too(self):
        with serve(openai_reply) as (base, received):
            embeddings.request_embeddings(["a"], base + "/embeddings/", "m")
        self.assertEqual(received[0].path, "/v1/embeddings")

    def test_vectors_follow_the_index_field_even_if_the_server_shuffles_them(self):
        with serve(lambda request: openai_reply(request, shuffle=True)) as (base, _):
            vectors = embeddings.request_embeddings(["one", "three", "five five"], base, "m")
        self.assertEqual(vectors, [vector_for("one"), vector_for("three"), vector_for("five five")])

    def test_a_server_without_index_fields_is_read_in_order(self):
        def reply(request):
            return 200, {"data": [{"embedding": vector_for(t)} for t in request.body["input"]]}, {}
        with serve(reply) as (base, _):
            vectors = embeddings.request_embeddings(["x", "yy"], base, "m")
        self.assertEqual(vectors, [vector_for("x"), vector_for("yy")])

    def test_many_texts_are_sent_in_batches_and_come_back_in_order(self):
        texts = [f"group {i}" for i in range(300)]
        with serve(openai_reply) as (base, received):
            vectors = embeddings.request_embeddings(texts, base, "m")
        self.assertEqual([len(r.body["input"]) for r in received], [128, 128, 44])
        self.assertEqual(vectors, [vector_for(t) for t in texts])

    def test_no_texts_no_request(self):
        self.assertEqual(embeddings.request_embeddings([], "http://127.0.0.1:1/v1", "m"), [])


class FailureTests(unittest.TestCase):
    def assertFails(self, base, *, contains, texts=("a",), key=KEY, model="m", **kwargs):
        with self.assertRaises(embeddings.EmbeddingError) as context:
            embeddings.request_embeddings(list(texts), base, model, key, **kwargs)
        message = str(context.exception)
        for fragment in contains:
            self.assertIn(fragment, message)
        self.assertNotIn(KEY, message)
        return message

    def test_rejected_key_explains_itself_and_never_repeats_the_key(self):
        def reply(request):
            return 401, {"error": {"message": f"Incorrect API key provided: {KEY}.", "type": "invalid_request_error"}}, {}
        with serve(reply) as (base, _):
            self.assertFails(base, contains=["HTTP 401", "Incorrect API key", "Check the API key"])

    def test_unknown_model(self):
        def reply(request):
            return 404, {"error": {"message": "model \"nope\" not found, try pulling it first"}}, {}
        with serve(reply) as (base, _):
            self.assertFails(base, contains=["HTTP 404", "not found, try pulling it first. Check the model name"], model="nope")

    def test_error_body_that_is_not_json(self):
        with serve(lambda request: (502, b"<html>Bad gateway</html>", {})) as (base, _):
            self.assertFails(base, contains=["HTTP 502", "Bad gateway"])

    def test_success_body_that_is_not_json(self):
        with serve(lambda request: (200, b"not json at all", {})) as (base, _):
            self.assertFails(base, contains=["could not be read"])

    def test_response_in_another_format(self):
        with serve(lambda request: (200, {"embedding": [1, 2, 3]}, {})) as (base, _):
            self.assertFails(base, contains=["OpenAI format"])

    def test_wrong_number_of_embeddings(self):
        def reply(request):
            return 200, {"data": [{"index": 0, "embedding": [1.0, 2.0]}]}, {}
        with serve(reply) as (base, _):
            self.assertFails(base, contains=["1 embeddings for 2 texts"], texts=["a", "b"])

    def test_vectors_of_different_sizes(self):
        def reply(request):
            return 200, {"data": [{"index": 0, "embedding": [1.0, 2.0]}, {"index": 1, "embedding": [1.0]}]}, {}
        with serve(reply) as (base, _):
            self.assertFails(base, contains=["2 embeddings for 2 texts"], texts=["a", "b"])

    def test_nobody_listening(self):
        with serve(openai_reply) as (base, _):
            pass  # the server is gone now: its port is closed
        self.assertFails(base, contains=["Could not reach 127.0.0.1"])

    def test_a_server_that_never_answers_times_out(self):
        def slow(request):
            time.sleep(1.5)
            return openai_reply(request)
        with serve(slow) as (base, _):
            message = self.assertFails(base, contains=["did not answer within"], timeout=0.3)
        self.assertIn("0.3 seconds", message)

    def test_a_missing_model_is_reported_before_any_request(self):
        self.assertFails("http://127.0.0.1:1/v1", contains=["No model"], model="")

    def test_a_key_is_never_sent_in_clear_to_a_remote_host(self):
        self.assertFails("http://api.example.com/v1", contains=["plain http", "Use https"])

    def test_a_key_may_go_over_http_to_this_machine(self):
        with serve(openai_reply) as (base, received):
            embeddings.request_embeddings(["a"], base, "m", KEY)
        self.assertEqual(received[0].headers["Authorization"], f"Bearer {KEY}")

    def test_the_key_does_not_follow_a_redirect_to_another_server(self):
        with serve(lambda request: openai_reply(request)) as (target, target_received):
            with serve(lambda request: (307, b"", {"Location": target + "/embeddings"})) as (base, _):
                try:
                    embeddings.request_embeddings(["a"], base, "m", KEY)
                except embeddings.EmbeddingError:
                    pass  # urllib refuses to re-POST after a redirect; either outcome is fine
        for request in target_received:
            self.assertIsNone(request.headers["Authorization"])

    def test_addresses_that_are_not_web_addresses(self):
        for base in ("", "localhost:11434/v1", "file:///etc/passwd", "ftp://example.com/v1", "http:///v1"):
            with self.assertRaises(embeddings.EmbeddingError, msg=base):
                embeddings.endpoint_url(base)


class AddressTests(unittest.TestCase):
    def test_endpoint_url(self):
        for base in ("https://api.openai.com/v1", "https://api.openai.com/v1/", "https://api.openai.com/v1/embeddings"):
            self.assertEqual(embeddings.endpoint_url(base), "https://api.openai.com/v1/embeddings")
        self.assertEqual(embeddings.endpoint_url(" http://localhost:11434/v1 "), "http://localhost:11434/v1/embeddings")
        self.assertEqual(embeddings.endpoint_url("http://localhost:8080"), "http://localhost:8080/embeddings")

    def test_which_hosts_are_this_machine(self):
        for host in ("localhost", "LOCALHOST", "127.0.0.1", "127.1.2.3", "::1"):
            self.assertTrue(embeddings.is_local_host(host), host)
        for host in ("api.openai.com", "192.168.1.20", "10.0.0.5", "my-laptop.local", "", None):
            self.assertFalse(embeddings.is_local_host(host), host)

    def test_the_user_is_warned_unless_the_endpoint_is_local(self):
        self.assertTrue(embeddings.sends_off_this_computer("https://api.openai.com/v1"))
        self.assertTrue(embeddings.sends_off_this_computer("http://192.168.1.20:11434/v1"))
        self.assertFalse(embeddings.sends_off_this_computer("http://localhost:11434/v1"))
        self.assertFalse(embeddings.sends_off_this_computer("http://[::1]:11434/v1"))


class SettingsTests(unittest.TestCase):
    def test_defaults_come_from_the_preset(self):
        self.assertEqual(embeddings.normalize_provider_settings(None),
                         {"provider": "openai", "baseUrl": "https://api.openai.com/v1",
                          "model": "text-embedding-3-small", "apiKey": ""})
        ollama = embeddings.normalize_provider_settings({"provider": "ollama"})
        self.assertEqual((ollama["baseUrl"], ollama["model"]), ("http://localhost:11434/v1", "nomic-embed-text"))

    def test_stored_values_win_and_junk_is_ignored(self):
        settings = embeddings.normalize_provider_settings(
            {"provider": "custom", "baseUrl": " http://x/v1 ", "model": "m", "apiKey": " k "})
        self.assertEqual(settings, {"provider": "custom", "baseUrl": "http://x/v1", "model": "m", "apiKey": "k"})
        self.assertEqual(embeddings.normalize_provider_settings({"provider": "bogus"})["provider"], "openai")
        self.assertEqual(embeddings.normalize_provider_settings("junk")["provider"], "openai")

    def test_an_empty_openai_key_falls_back_to_the_environment(self):
        environ = {"OPENAI_API_KEY": " sk-env "}
        self.assertEqual(embeddings.normalize_provider_settings({}, environ)["apiKey"], "sk-env")
        self.assertEqual(embeddings.normalize_provider_settings({"apiKey": "mine"}, environ)["apiKey"], "mine")
        self.assertEqual(embeddings.normalize_provider_settings({"provider": "ollama"}, environ)["apiKey"], "")


class GroupTextTests(unittest.TestCase):
    GROUPS = {"People": {}, "cities": {}, "Empty": {}}
    ENTRIES = [{"word": "Ada", "group": "People"}, {"word": "London", "group": "cities"},
               {"word": "Lisbon", "group": "cities"}, {"word": "Ada", "group": "People"},
               {"word": "  ", "group": "People"}, {"word": "stray", "group": "gone"}, {"word": "x", "group": ""}]

    def test_sorted_by_name_with_the_words_after_it(self):
        self.assertEqual(embeddings.group_texts(self.GROUPS, self.ENTRIES),
                         [("cities", "cities: London, Lisbon"), ("Empty", "Empty"), ("People", "People: Ada")])

    def test_only_a_few_words_per_group_and_long_ones_are_cut(self):
        entries = [{"word": f"word{i}", "group": "People"} for i in range(20)] + [{"word": "w" * 100, "group": "cities"}]
        texts = dict(embeddings.group_texts(self.GROUPS, entries, max_words=3))
        self.assertEqual(texts["People"], "People: word0, word1, word2")
        self.assertEqual(texts["cities"], "cities: " + "w" * embeddings.MAX_WORD_CHARS)

    def test_the_order_does_not_depend_on_how_the_groups_were_stored(self):
        reversed_groups = dict(reversed(list(self.GROUPS.items())))
        self.assertEqual(embeddings.group_texts(self.GROUPS, self.ENTRIES),
                         embeddings.group_texts(reversed_groups, self.ENTRIES))


if __name__ == "__main__":
    unittest.main()
