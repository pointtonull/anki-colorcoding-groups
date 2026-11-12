"""A fake OpenAI-compatible `/v1/embeddings` server for the harness drivers. NOT part of the shipped add-on.

It runs inside the throwaway Anki (a thread of the driver), on 127.0.0.1 with a random port, so the checks and the
screenshots need no real service, no key and no network. The "meaning" is invented: each demo group name has a
fixed position in a small space (cities near countries, drugs near diseases...), anything else gets a stable
pseudo-random position.

    server = FakeEmbeddings()
    base_url = server.start()      # "http://127.0.0.1:<port>/v1"
    server.requests                # what arrived: {"path", "authorization", "body"}
    server.mode = "unauthorized"   # answer 401 (the message repeats the key, as a careless service might)
    server.stop()
"""
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# group name -> position in an invented meaning space (geography, medicine, programming, people, time)
MEANING = {
    "Cities": [1.0, 0.2, 0.0, 0.0, 0.0], "Countries": [0.9, 0.3, 0.0, 0.0, 0.1], "Rivers": [0.7, 0.1, 0.0, 0.0, 0.0],
    "Places": [0.95, 0.25, 0.0, 0.0, 0.0],
    "Drugs": [0.0, 1.0, 0.1, 0.0, 0.0], "Diseases": [0.0, 0.9, 0.0, 0.1, 0.0],
    "Python": [0.0, 0.0, 1.0, 0.1, 0.0], "JavaScript": [0.0, 0.0, 0.95, 0.15, 0.0], "Code": [0.0, 0.0, 0.9, 0.0, 0.0],
    "People": [0.0, 0.1, 0.0, 1.0, 0.0], "Scientists": [0.05, 0.1, 0.0, 0.9, 0.05],
    "Dates": [0.0, 0.0, 0.0, 0.1, 1.0],
}


def vector_for(text):
    name = text.split(":", 1)[0]
    if name in MEANING:
        return MEANING[name]
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return [byte / 255 - 0.5 for byte in digest[:5]]


class FakeEmbeddings:
    def __init__(self):
        self.requests = []
        self.mode = "ok"
        self._server = None

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"null")
                owner.requests.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                       "body": body})
                if owner.mode == "unauthorized":
                    status = 401
                    reply = {"error": {"message": f"Incorrect API key provided: {self.headers.get('Authorization')}."}}
                else:
                    status = 200
                    reply = {"object": "list", "model": body["model"],
                             "data": [{"object": "embedding", "index": i, "embedding": vector_for(text)}
                                      for i, text in enumerate(body["input"])]}
                raw = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
