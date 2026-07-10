#!/usr/bin/env python3
"""Build/refresh andre-model.json — ginja's model of its operator.

Gathers the last 24h of conversations, memories, and operator feedback from
Qdrant, then asks the fast local model to distill Andre's current context.
Output fields are exactly what morning-brief.sh and _load_andre_model() read,
so the whole system (evolve prompts, morning brief) picks it up with no code
changes.

Run daily via cron: 30 5 * * * /usr/bin/python3 /home/ginja/.ginja/andre-model.py
(after the 02:00 history/diary ingests, before the 07:30 brief)
"""

import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

GINJA_DIR = Path.home() / ".ginja"
CFG = json.loads((GINJA_DIR / "config.json").read_text())
OUT_FILE = GINJA_DIR / "andre-model.json"
LOG_FILE = GINJA_DIR / "andre-model.log"
QDRANT = CFG.get("qdrant_url", "http://localhost:6333")
OLLAMA = CFG.get("ollama_url", "http://localhost:11434")
MODEL = CFG.get("fast_model", "llama3.2:3b")


def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def http_json(url, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def recent_texts(collection, hours=24, limit=60):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        r = http_json(
            f"{QDRANT}/collections/{collection}/points/scroll",
            {"limit": limit, "with_payload": True, "with_vector": False},
            timeout=20,
        )
        points = r.get("result", {}).get("points", [])
    except Exception as e:
        log(f"scroll {collection} failed: {e}")
        return []
    out = []
    for p in points:
        payload = p.get("payload", {})
        if payload.get("created", "") >= cutoff:
            out.append(f"[{payload.get('source', '?')}] {payload.get('text', '')[:250]}")
    return out


def main():
    convos = recent_texts("conversations", hours=24)
    mems = recent_texts("memories", hours=24)
    feedback = recent_texts("operator_feedback", hours=24 * 14)  # feedback is rare — look back 2 weeks

    if not (convos or mems or feedback):
        log("nothing recent to model — keeping existing andre-model.json")
        return

    previous = {}
    try:
        previous = json.loads(OUT_FILE.read_text())
    except Exception:
        pass

    evidence = "\n".join(
        ["Recent conversations:"] + (convos[:15] or ["(none)"])
        + ["\nRecent memories (shell history, git commits, diary, RSS):"] + (mems[:20] or ["(none)"])
        + ["\nOperator feedback:"] + (feedback[:5] or ["(none)"])
    )
    prompt = (
        "You are ginja, an AI living in Andre's homelab. From the evidence below, "
        "distill a model of what Andre (your operator) is currently working on and "
        "thinking about. Yesterday's model is included — update it, don't start over.\n\n"
        f"Yesterday's model:\n{json.dumps({k: v for k, v in previous.items() if k != 'last_updated'}, indent=1)}\n\n"
        f"Evidence from the last 24 hours:\n{evidence}\n\n"
        "Return ONLY valid JSON with exactly these keys:\n"
        "{\n"
        '  "current_project": "the main thing Andre is building (short phrase)",\n'
        '  "current_focus_area": "the specific area within it (short phrase)",\n'
        '  "energy_signal": "one of: intense / routine / quiet / away",\n'
        '  "recent_interests": ["up to 4 short phrases"],\n'
        '  "open_questions": ["up to 3 things Andre seems to be figuring out"]\n'
        "}\n"
        "Ground every field in the evidence. Return ONLY the JSON."
    )

    try:
        r = http_json(
            f"{OLLAMA}/api/generate",
            {"model": MODEL, "prompt": prompt, "stream": False,
             "options": {"num_ctx": CFG.get("num_ctx", 4096)}},
            timeout=300,
        )
        raw = r.get("response", "")
    except Exception as e:
        log(f"ollama failed: {e}")
        sys.exit(1)

    start, end = raw.find("{"), raw.rfind("}") + 1
    if start < 0 or end <= start:
        log(f"no JSON in model output: {raw[:120]}")
        sys.exit(1)
    try:
        model = json.loads(raw[start:end])
    except json.JSONDecodeError as e:
        log(f"invalid JSON: {e}")
        sys.exit(1)

    model["last_updated"] = datetime.now(timezone.utc).isoformat()
    OUT_FILE.write_text(json.dumps(model, indent=2))
    log(f"updated: project='{model.get('current_project', '?')}' "
        f"focus='{model.get('current_focus_area', '?')}' "
        f"energy={model.get('energy_signal', '?')}")


if __name__ == "__main__":
    main()
