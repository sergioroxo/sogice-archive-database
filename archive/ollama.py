"""Small Ollama HTTP client (standard library only).

Two calls are all the pipeline needs:

* ``chat_json`` — one structured reading call. Output is constrained by a JSON
  schema (Ollama ``format``), output tokens are always capped (a 31B call once
  looped for 14 minutes in October) and "thinking" is switched off unless asked.
* ``embed`` — EmbeddingGemma 2 vectors for text, images and audio. Each input
  is a string or a ``media_input(...)`` object. Ollama does not accept video:
  embed sampled frames and the audio track instead.

Model names come from ``config/models.yaml``; callers pass them in.
Every call sends ``keep_alive`` (default 0) so a model is unloaded as soon as
it finishes: one resident model at a time on the Mac Studio.
"""
from __future__ import annotations

import base64
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Sequence

DEFAULT_NUM_PREDICT = 1024
DEFAULT_KEEP_ALIVE = 0


class OllamaError(RuntimeError):
    """The Ollama server could not be reached or returned an error."""


@dataclass
class ChatResult:
    data: Any                 # parsed JSON object, or None if the reply was not JSON
    text: str                 # raw reply text
    model: str
    seconds: float
    prompt_tokens: int | None
    output_tokens: int | None
    done_reason: str | None   # "stop", or "length" when the token cap was hit
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def truncated(self) -> bool:
        return self.done_reason == "length"


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    model: str
    seconds: float
    raw: dict = field(repr=False, default_factory=dict)


def media_input(*, text: str | None = None, image: bytes | None = None,
                audio: bytes | None = None) -> dict:
    """One multimodal embedding input: any of text, image bytes, audio bytes."""
    item: dict[str, str] = {}
    if text is not None:
        item["text"] = text
    if image is not None:
        item["image"] = base64.b64encode(image).decode("ascii")
    if audio is not None:
        item["audio"] = base64.b64encode(audio).decode("ascii")
    if not item:
        raise ValueError("media_input needs text, image or audio")
    return item


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector lengths differ: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class OllamaClient:
    def __init__(self, base_url: str, timeout_seconds: float = 600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    # -- transport ---------------------------------------------------------

    def _request(self, path: str, body: dict | None = None, *, timeout: float | None = None) -> dict:
        url = self.base_url + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method="GET" if body is None else "POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            try:
                detail = json.loads(detail).get("error", detail)
            except (ValueError, AttributeError):
                pass
            raise OllamaError(f"{path}: HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise OllamaError(f"{path}: cannot reach {self.base_url}: {exc}") from None
        try:
            return json.loads(payload)
        except ValueError:
            raise OllamaError(f"{path}: reply is not JSON: {payload[:200]!r}") from None

    # -- server ------------------------------------------------------------

    def version(self) -> str:
        return str(self._request("/api/version", timeout=10).get("version", ""))

    def tags(self) -> dict[str, str]:
        """Installed models: name -> digest."""
        models = self._request("/api/tags", timeout=10).get("models", [])
        return {m["name"]: m.get("digest", "") for m in models}

    def show(self, model: str) -> dict:
        return self._request("/api/show", {"model": model}, timeout=30)

    def loaded(self) -> list[str]:
        return [m["name"] for m in self._request("/api/ps", timeout=10).get("models", [])]

    # -- calls -------------------------------------------------------------

    def chat_json(self, model: str, messages: list[dict], *, schema: dict | None = None,
                  temperature: float | None = 0, num_predict: int = DEFAULT_NUM_PREDICT,
                  num_ctx: int | None = None, think: bool | None = False,
                  keep_alive: int | str = DEFAULT_KEEP_ALIVE) -> ChatResult:
        """One non-streaming chat call whose reply must be JSON.

        ``schema`` constrains the reply; without it Ollama's plain JSON mode is
        used. ``think=None`` leaves the model's thinking setting alone (for
        models without that capability).
        """
        if num_predict <= 0:
            raise ValueError("num_predict must be positive: output is always capped")
        options: dict[str, Any] = {"num_predict": num_predict}
        if temperature is not None:
            options["temperature"] = temperature
        if num_ctx is not None:
            options["num_ctx"] = num_ctx
        body: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False,
            "format": schema if schema is not None else "json",
            "options": options, "keep_alive": keep_alive,
        }
        if think is not None:
            body["think"] = think
        started = time.monotonic()
        reply = self._request("/api/chat", body)
        seconds = time.monotonic() - started
        text = (reply.get("message") or {}).get("content", "")
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        return ChatResult(
            data=data, text=text, model=reply.get("model", model), seconds=seconds,
            prompt_tokens=reply.get("prompt_eval_count"), output_tokens=reply.get("eval_count"),
            done_reason=reply.get("done_reason"), raw=reply)

    def embed(self, model: str, inputs: Sequence[str | dict], *,
              keep_alive: int | str = DEFAULT_KEEP_ALIVE, truncate: bool = False) -> EmbedResult:
        """Embed a batch. Raises if the server returns a different number of vectors.

        ``truncate`` defaults to False: an over-long input is an error, not a
        silently shortened vector.
        """
        if not inputs:
            raise ValueError("embed needs at least one input")
        body = {"model": model, "input": list(inputs), "keep_alive": keep_alive,
                "truncate": truncate}
        started = time.monotonic()
        reply = self._request("/api/embed", body)
        seconds = time.monotonic() - started
        vectors = reply.get("embeddings") or []
        if len(vectors) != len(inputs):
            raise OllamaError(
                f"/api/embed: sent {len(inputs)} inputs, got {len(vectors)} vectors "
                "(the server may not accept this input type)")
        return EmbedResult(vectors=vectors, model=reply.get("model", model),
                           seconds=seconds, raw=reply)

    def unload(self, model: str) -> None:
        """Ask the server to drop a model from memory now."""
        self._request("/api/generate", {"model": model, "keep_alive": 0}, timeout=60)
