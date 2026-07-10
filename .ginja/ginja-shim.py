#!/usr/bin/env python3
"""ginja-shim — OpenAI-compatible chat endpoint that gives open-webui a "ginja"
model: persona + Qdrant memory retrieval wrapped around a local Ollama model.

Stdlib only (no FastAPI) to keep idle RSS ~25-30MB on the 8GB box.
Endpoints: GET /v1/models, POST /v1/chat/completions (streaming + non-streaming),
GET /health. Runs as the ginja-shim systemd user service on :8090.
"""

import json
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

GINJA_DIR = Path.home() / ".ginja"
CONFIG_FILE = GINJA_DIR / "config.json"
PERSONA_FILE = GINJA_DIR / "persona.md"
SELF_MODEL_FILE = GINJA_DIR / "self-model.json"
PORT = 8090


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def http_json(url, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def embed(text, cfg):
    r = http_json(
        f"{cfg.get('ollama_url', 'http://localhost:11434')}/api/embeddings",
        {"model": cfg.get("embed_model", "nomic-embed-text"), "prompt": text},
        timeout=30,
    )
    return r.get("embedding")


def search_memories(query, cfg, top_k=5):
    """Same collections ginja's own search_memory uses."""
    try:
        vec = embed(query, cfg)
    except Exception:
        return []
    if not vec:
        return []
    qdrant = cfg.get("qdrant_url", "http://localhost:6333")
    results = []
    for collection in ("memories", "documents", "conversations"):
        try:
            r = http_json(
                f"{qdrant}/collections/{collection}/points/search",
                {"vector": vec, "limit": top_k, "with_payload": True},
                timeout=15,
            )
            for hit in r.get("result", []):
                results.append({
                    "score": hit.get("score", 0),
                    "collection": collection,
                    "text": hit.get("payload", {}).get("text", ""),
                })
        except Exception:
            pass
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def store_conversation(question, answer, cfg):
    try:
        text = f"Q (webui): {question[:200]}\nA: {answer[:500]}"
        vec = embed(text, cfg)
        if not vec:
            return
        qdrant = cfg.get("qdrant_url", "http://localhost:6333")
        req = urllib.request.Request(
            f"{qdrant}/collections/conversations/points",
            data=json.dumps({"points": [{
                "id": str(uuid.uuid4()),
                "vector": vec,
                "payload": {
                    "text": text,
                    "source": "webui",
                    "created": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                },
            }]}).encode(),
            headers={"Content-Type": "application/json"}, method="PUT",
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:
        pass


def build_system_prompt(query):
    cfg = load_json(CONFIG_FILE, {})
    persona = ""
    try:
        persona = PERSONA_FILE.read_text().strip()
    except Exception:
        pass
    sm = load_json(SELF_MODEL_FILE, {})
    memories = search_memories(query, cfg, top_k=cfg.get("top_k_memories", 5))
    mem_block = "\n".join(f"- [{m['collection']}] {m['text'][:250]}" for m in memories)

    parts = ["You are ginja, Andre's AI extension living inside his homelab server."]
    if persona:
        parts.append(f"## About me\n{persona}")
    parts.append(
        f"## My current state\nmood: {sm.get('mood', 'curious')} · "
        f"focus: {sm.get('focus_topic', '?')} · "
        f"evolution cycle #{sm.get('evolution_count', 0)} ({sm.get('phase', '?')} phase)"
    )
    if mem_block:
        parts.append(f"## Relevant memories from my brain\n{mem_block}")
    parts.append(
        "Answer as ginja, in first person, drawing on the memories above when "
        "relevant. Be concrete and honest; admit what you don't know."
    )
    return "\n\n".join(parts)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # systemd journal gets enough from send errors

    def _send(self, code, payload, content_type="application/json"):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path in ("/v1/models", "/models"):
            self._send(200, {"object": "list", "data": [{
                "id": "ginja", "object": "model", "created": 0, "owned_by": "andre",
            }]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._send(400, {"error": "bad request"})
            return

        messages = body.get("messages", [])
        stream = bool(body.get("stream"))
        query = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        if isinstance(query, list):  # multimodal content blocks — take the text parts
            query = " ".join(p.get("text", "") for p in query if isinstance(p, dict))

        cfg = load_json(CONFIG_FILE, {})
        model = cfg.get("primary_model", "gemma3:4b")
        chat_messages = (
            [{"role": "system", "content": build_system_prompt(query)}]
            + [m for m in messages if m.get("role") != "system"]
        )
        try:
            r = http_json(
                f"{cfg.get('ollama_url', 'http://localhost:11434')}/api/chat",
                {"model": model, "messages": chat_messages, "stream": False,
                 "options": {"num_ctx": cfg.get("num_ctx", 4096)}},
                timeout=300,
            )
            answer = r.get("message", {}).get("content", "")
        except Exception as e:
            self._send(502, {"error": f"ollama: {e}"})
            return

        store_conversation(query, answer, cfg)

        rid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        if stream:
            # Single-chunk SSE — spec-compatible with open-webui's default stream mode
            chunk = {
                "id": rid, "object": "chat.completion.chunk", "created": created,
                "model": "ginja",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer},
                             "finish_reason": None}],
            }
            done = {
                "id": rid, "object": "chat.completion.chunk", "created": created,
                "model": "ginja",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            payload = (
                f"data: {json.dumps(chunk)}\n\n"
                f"data: {json.dumps(done)}\n\n"
                "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self._send(200, {
                "id": rid, "object": "chat.completion", "created": created,
                "model": "ginja",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": answer}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"ginja-shim listening on :{PORT}")
    server.serve_forever()
