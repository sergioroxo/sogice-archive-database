import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from archive.ollama import OllamaClient, OllamaError, cosine, media_input


class FakeOllama:
    """A tiny HTTP server that records requests and replays canned replies."""

    def __init__(self):
        self.requests: list[tuple[str, dict | None]] = []
        self.replies: dict[str, tuple[int, dict]] = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, body):
                fake.requests.append((self.path, body))
                status, payload = fake.replies.get(self.path, (404, {"error": "not found"}))
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._reply(None)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self._reply(json.loads(self.rfile.read(length)))

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    server = FakeOllama()
    yield server
    server.close()


def test_chat_json_sends_cap_schema_and_no_thinking(fake):
    fake.replies["/api/chat"] = (200, {
        "model": "m:1", "message": {"role": "assistant", "content": '{"speaker": "A"}'},
        "prompt_eval_count": 12, "eval_count": 5, "done_reason": "stop"})
    client = OllamaClient(fake.url)
    schema = {"type": "object", "properties": {"speaker": {"type": "string"}}}
    result = client.chat_json("m:1", [{"role": "user", "content": "hi"}], schema=schema,
                              num_predict=200, num_ctx=8192)
    assert result.data == {"speaker": "A"}
    assert result.output_tokens == 5 and not result.truncated
    path, body = fake.requests[-1]
    assert path == "/api/chat"
    assert body["format"] == schema
    assert body["think"] is False
    assert body["stream"] is False
    assert body["keep_alive"] == 0
    assert body["options"] == {"num_predict": 200, "temperature": 0, "num_ctx": 8192}


def test_chat_json_non_json_reply_and_truncation(fake):
    fake.replies["/api/chat"] = (200, {"message": {"content": "-S-S-S"}, "done_reason": "length"})
    result = OllamaClient(fake.url).chat_json("m", [], think=None, schema=None)
    assert result.data is None and result.truncated
    _, body = fake.requests[-1]
    assert "think" not in body and body["format"] == "json"


def test_chat_json_requires_output_cap():
    with pytest.raises(ValueError):
        OllamaClient("http://127.0.0.1:1").chat_json("m", [], num_predict=0)


def test_embed_mixed_inputs(fake):
    fake.replies["/api/embed"] = (200, {"model": "e", "embeddings": [[1.0, 0.0], [0.0, 1.0]]})
    item = media_input(image=b"\x89PNG", text="caption")
    result = OllamaClient(fake.url).embed("e", ["plain", item])
    assert result.vectors == [[1.0, 0.0], [0.0, 1.0]]
    _, body = fake.requests[-1]
    assert body["input"][0] == "plain"
    assert base64.b64decode(body["input"][1]["image"]) == b"\x89PNG"
    assert body["input"][1]["text"] == "caption"
    assert body["truncate"] is False


def test_embed_count_mismatch_is_an_error(fake):
    fake.replies["/api/embed"] = (200, {"embeddings": []})
    with pytest.raises(OllamaError, match="got 0 vectors"):
        OllamaClient(fake.url).embed("e", [media_input(audio=b"RIFF")])


def test_http_error_carries_server_message(fake):
    fake.replies["/api/embed"] = (400, {"error": "video is not supported"})
    with pytest.raises(OllamaError, match="video is not supported"):
        OllamaClient(fake.url).embed("e", ["x"])


def test_unreachable_server():
    with pytest.raises(OllamaError, match="cannot reach"):
        OllamaClient("http://127.0.0.1:9", timeout_seconds=2).version()


def test_tags_and_version(fake):
    fake.replies["/api/version"] = (200, {"version": "0.40.3"})
    fake.replies["/api/tags"] = (200, {"models": [{"name": "a:1", "digest": "abc"}]})
    client = OllamaClient(fake.url + "/")
    assert client.version() == "0.40.3"
    assert client.tags() == {"a:1": "abc"}


def test_media_input_needs_content():
    with pytest.raises(ValueError):
        media_input()
    assert media_input(audio=b"x") == {"audio": "eA=="}


def test_cosine():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 1]) == 0.0
    with pytest.raises(ValueError):
        cosine([1], [1, 2])
