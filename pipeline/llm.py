"""Client for the provided vLLM endpoint. The only generative model this pipeline may use.

Three things about this server are easy to get wrong, and all three look like an outage:

* it is a reasoning model and spends tokens on a hidden trace before it answers, so a small
  `max_tokens` returns `finish_reason: "length"` with `content: null`. We ask for room, and we
  treat a truncated reply as a retryable failure rather than an empty answer;
* the trace field is `reasoning`, not `reasoning_content` — reading the wrong one silently yields
  None, so we never rely on it for anything;
* it is shared with the other finalists during the window. Opening hundreds of connections gets
  us throttled and loses more time than it gains, so concurrency is bounded and every failure
  backs off rather than hammering.

Structured output (`response_format: json_schema`) is constrained during decoding, so what comes
back always parses. That it parses is not that it is right: everything here is validated against
the plan vocabulary afterwards, and a plan that fails validation is dropped, not repaired.
"""
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request

DEFAULT_MODEL = os.environ.get("LLM_MODEL", "qwen3.6-35b-a3b-nvfp4")
DEFAULT_BASE = os.environ.get("LLM_BASE_URL", "http://localhost:8100/v1")


class Endpoint:
    def __init__(self, base_url=None, model=None, timeout=180, log=print):
        self.base = (base_url or DEFAULT_BASE).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout
        self.log = log
        self.calls = 0
        self.failures = 0
        self.tokens = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ transport
    def _url(self):
        # LLM_BASE_URL is documented as the base the endpoints hang off. Accept it with or without
        # the /v1 suffix rather than failing on a trailing-slash difference.
        base = self.base
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _post(self, payload):
        req = urllib.request.Request(
            self._url(), method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def chat(self, messages, max_tokens=3072, temperature=0.0, schema=None, retries=3):
        """One completion. Returns the assistant text, or None if it could not be obtained."""
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": max_tokens, "temperature": temperature}
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "plan", "schema": schema}}

        delay = 1.0
        for attempt in range(retries):
            try:
                data = self._post(payload)
                choice = (data.get("choices") or [{}])[0]
                text = (choice.get("message") or {}).get("content")
                usage = data.get("usage") or {}
                with self._lock:
                    self.calls += 1
                    self.tokens += int(usage.get("total_tokens") or 0)
                if text:
                    return text
                # content is null: the trace consumed the budget before the answer began
                if choice.get("finish_reason") == "length":
                    payload["max_tokens"] = min(int(payload["max_tokens"] * 1.8), 16384)
            except urllib.error.HTTPError as e:
                # A server that will not take our json_schema must not cost us the question: fall
                # back to asking for JSON in the prompt and validating it ourselves, which is what
                # we do to the structured reply anyway.
                if e.code == 400 and "response_format" in payload:
                    payload.pop("response_format")
                    payload["messages"] = list(messages) + [
                        {"role": "system",
                         "content": "Reply with a single JSON object and nothing else."}]
                    self.log("[llm] endpoint rejected the response schema; "
                             "falling back to prompted JSON")
                    continue
                if attempt == retries - 1:
                    with self._lock:
                        self.failures += 1
                    self.log(f"[llm] giving up after {retries} attempts: HTTP {e.code}")
                    return None
            except (urllib.error.URLError, TimeoutError,
                    json.JSONDecodeError, OSError) as e:
                if attempt == retries - 1:
                    with self._lock:
                        self.failures += 1
                    self.log(f"[llm] giving up after {retries} attempts: {type(e).__name__}: {e}")
                    return None
            time.sleep(delay + random.random() * 0.5)
            delay *= 2
        with self._lock:
            self.failures += 1
        return None

    # ------------------------------------------------------------------ health
    def probe(self):
        """Is the endpoint there and answering? Called once, before any batch is dispatched."""
        t0 = time.time()
        out = self.chat([{"role": "user", "content": "Reply with the single word: ready"}],
                        max_tokens=2048, retries=2)
        if out is None:
            self.log(f"[llm] endpoint {self._url()} did not answer; continuing without the model")
            return False
        self.log(f"[llm] endpoint ready at {self._url()} as {self.model} "
                 f"({time.time() - t0:.1f}s round trip)")
        return True

    def stats(self):
        return {"calls": self.calls, "failures": self.failures, "tokens": self.tokens}


if __name__ == "__main__":                      # smoke test: python -m pipeline.llm
    import sys
    ep = Endpoint()
    prompt = " ".join(sys.argv[1:]) or "What is 17*243? Answer with digits only."
    print(ep.chat([{"role": "user", "content": prompt}]))
    print(ep.stats())
