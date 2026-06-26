#!/usr/bin/env python3
"""RSS feed ingestion for ginja-brain — daily passive internet intake."""

import feedparser
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

GINJA_DIR  = Path.home() / ".ginja"
CFG_FILE   = GINJA_DIR / "config.json"
FEEDS_FILE = GINJA_DIR / "rss-feeds.json"
STATE_FILE = GINJA_DIR / "rss-state.json"
LOG_FILE   = GINJA_DIR / "rss-ingest.log"
GINJA_BIN  = Path.home() / "bin" / "ginja"

feedparser.USER_AGENT = "ginja-brain/1.0 +https://github.com/ginja"


def log(msg: str) -> None:
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_cfg() -> dict:
    try:
        return json.loads(CFG_FILE.read_text())
    except Exception:
        return {}


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def ollama_summarize(prompt: str, cfg: dict) -> str:
    url   = cfg.get("ollama_url", "http://localhost:11434")
    model = cfg.get("fast_model", "llama3.2:3b")
    try:
        r = requests.post(
            f"{url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=90,
        )
        return r.json().get("response", "").strip()
    except Exception as e:
        log(f"  LLM error: {e}")
        return ""


def store_memory(text: str) -> bool:
    try:
        result = subprocess.run(
            [str(GINJA_BIN), "remember", text],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def entry_id(entry: dict) -> str:
    return (entry.get("id") or entry.get("link") or entry.get("title", "")).strip()


def main() -> None:
    log("── RSS ingest starting ──────────────────────────────")

    cfg = load_cfg()

    state: dict = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            pass

    if not FEEDS_FILE.exists():
        log("No rss-feeds.json — nothing to do")
        return

    feeds        = json.loads(FEEDS_FILE.read_text())
    total_stored = 0

    for feed_cfg in feeds:
        name = feed_cfg.get("name", "?")
        url  = feed_cfg.get("url", "")
        if not url:
            continue

        log(f"Fetching: {name}")
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            log(f"  Error fetching: {e}")
            continue

        if not parsed.entries:
            log(f"  Empty feed — skipping")
            continue

        seen_ids   = set(state.get(url, []))
        new_entries = []
        for entry in parsed.entries[:25]:
            eid = entry_id(entry)
            if eid and eid not in seen_ids:
                new_entries.append(entry)
                seen_ids.add(eid)

        if not new_entries:
            log(f"  No new entries")
            state[url] = list(seen_ids)[-150:]
            continue

        log(f"  {len(new_entries)} new entries")

        # Build entry digest for LLM (cap at 8, truncate summaries)
        entry_lines = []
        for e in new_entries[:8]:
            title   = e.get("title", "").strip()
            summary = strip_html(
                e.get("summary", "") or e.get("description", "")
            )[:250]
            entry_lines.append(f"- {title}: {summary}" if summary else f"- {title}")

        digest = "\n".join(entry_lines)
        prompt = (
            f"You are summarizing RSS updates for a homelab developer's AI brain.\n"
            f"Feed: {name}\n\n"
            f"New items:\n{digest}\n\n"
            "Write 2-3 sentences summarizing what's new. "
            "Be specific — mention tool names, version numbers, key techniques, or trends. "
            "No filler words. End with a period."
        )

        summary = ollama_summarize(prompt, cfg)
        if not summary:
            summary = "; ".join(
                e.get("title", "") for e in new_entries[:5]
            )

        date_str    = datetime.now().strftime("%Y-%m-%d")
        memory_text = f"RSS ({name}) {date_str}: {summary}"

        if store_memory(memory_text):
            log(f"  ✓ Stored: {summary[:90]}…")
            total_stored += 1
        else:
            log(f"  ✗ Store failed")

        # Keep last 150 IDs per feed to avoid unbounded growth
        state[url] = list(seen_ids)[-150:]

    STATE_FILE.write_text(json.dumps(state, indent=2))
    log(f"── RSS ingest done — {total_stored}/{len(feeds)} feeds stored ──")


if __name__ == "__main__":
    main()
