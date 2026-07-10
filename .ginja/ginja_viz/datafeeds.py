"""Cheap, cached, read-only feeds for the dashboard.

Everything here reads state the brain already writes (JSON files, JSONL logs,
Qdrant over HTTP). A module-level TTL cache means N browser tabs cost one
file-parse per TTL window. Stdlib only — urllib, not requests.
"""

import calendar
import json
import os
import re
import subprocess
import time
import urllib.request
from collections import deque
from pathlib import Path

GINJA_DIR = Path.home() / ".ginja"
QDRANT_URL = "http://localhost:6333"

_cache = {}


def _cached(key, ttl, fn):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    data = fn()
    _cache[key] = (now, data)
    return data


def _read_json(path, default):
    try:
        return json.loads((GINJA_DIR / path).read_text())
    except Exception:
        return default


# ── Live vitals ─────────────────────────────────────────────────────────────────

def _vitals():
    v = {"gpu_pct": 0, "vram_used": 0, "vram_total": 4096,
         "load": 0.0, "ram_used_mb": 0, "ram_total_mb": 0, "lock": None}
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2)
        p = [x.strip() for x in r.stdout.strip().split(",")]
        v["gpu_pct"], v["vram_used"], v["vram_total"] = int(p[0]), int(p[1]), int(p[2])
    except Exception:
        pass
    try:
        v["load"] = float(Path("/proc/loadavg").read_text().split()[0])
    except Exception:
        pass
    try:
        total = avail = 0
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) // 1024
        v["ram_total_mb"], v["ram_used_mb"] = total, total - avail
    except Exception:
        pass
    try:
        st = os.statvfs(str(GINJA_DIR))
        v["disk_used_gb"] = round((st.f_blocks - st.f_bavail) * st.f_frsize / 1e9, 1)
        v["disk_total_gb"] = round(st.f_blocks * st.f_frsize / 1e9, 1)
    except Exception:
        pass
    try:
        v["uptime_s"] = float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        pass
    try:
        raw = (GINJA_DIR / ".inference.lock").read_text().strip()
        v["lock"] = json.loads(raw) if raw else None
    except Exception:
        pass
    _note_lock_transition(v["lock"])
    return v


_telemetry = None


def _engines(vitals):
    """Real engine activities (see telemetry.py) — cached 5 s."""
    def _load():
        global _telemetry
        from .telemetry import EngineTelemetry
        if _telemetry is None:
            _telemetry = EngineTelemetry()
        acts = _telemetry.refresh(vitals)
        return {"activity": dict(acts), "detail": dict(_telemetry.detail)}
    try:
        return _cached("engines", 5, _load)
    except Exception:
        return None


def _load_self_model():
    try:
        return json.loads((GINJA_DIR / "self-model.json").read_text())
    except Exception:
        return {}


def state():
    def _load():
        vitals = _vitals()
        return {
            "self_model": _load_self_model(),
            "vitals": vitals,
            "engines": _engines(vitals),
            "staleness": staleness(),
            "ts": time.time(),
        }
    return _cached("state", 2, _load)


# ── Knowledge graph (Qdrant scroll) ─────────────────────────────────────────────

def _qdrant_scroll(collection, limit=2000):
    points, offset = [], None
    while len(points) < limit:
        body = {"limit": min(500, limit - len(points)),
                "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{collection}/points/scroll",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read()).get("result", {})
        batch = result.get("points", [])
        points.extend(batch)
        offset = result.get("next_page_offset")
        if not batch or offset is None:
            break
    return points


def graph(domain=None, category=None, limit=600):
    def _load():
        nodes = _qdrant_scroll("kg_nodes")
        edges = _qdrant_scroll("kg_edges")
        return nodes, edges

    nodes_raw, edges_raw = _cached("kg", 60, _load)

    nodes = []
    for p in nodes_raw:
        pl = p.get("payload", {})
        nodes.append({"id": str(p.get("id")), "label": pl.get("label") or "?",
                      "category": pl.get("category") or "concept",
                      "domain": pl.get("domain") or "general",
                      "confidence": pl.get("confidence") or 0.5,
                      "cycle": pl.get("cycle") or 0,
                      "description": (pl.get("description") or "")[:300]})
    if domain:
        nodes = [n for n in nodes if n["domain"] == domain]
    if category:
        nodes = [n for n in nodes if n["category"] == category]

    ids = {n["id"] for n in nodes}
    edges = []
    for p in edges_raw:
        pl = p.get("payload", {})
        s, t = str(pl.get("source_id")), str(pl.get("target_id"))
        if s in ids and t in ids:
            edges.append({"source": s, "target": t,
                          "relation": pl.get("relation", "related_to"),
                          "weight": pl.get("weight", 1.0)})

    # keep the best-connected nodes when over limit
    total_nodes = len(nodes)
    if len(nodes) > limit:
        degree = {}
        for e in edges:
            degree[e["source"]] = degree.get(e["source"], 0) + 1
            degree[e["target"]] = degree.get(e["target"], 0) + 1
        nodes.sort(key=lambda n: (degree.get(n["id"], 0), n["cycle"]), reverse=True)
        nodes = nodes[:limit]
        ids = {n["id"] for n in nodes}
        edges = [e for e in edges if e["source"] in ids and e["target"] in ids]

    return {"nodes": nodes, "edges": edges,
            "total_nodes": total_nodes, "truncated": total_nodes > len(nodes)}


# ── Perception time-series ──────────────────────────────────────────────────────

_P_LOAD = re.compile(r"Load: ([\d.]+)")
_P_RAM = re.compile(r"RAM: ([\d.]+)/([\d.]+)GB")
_P_GPU = re.compile(r"GPU: (\d+)% · ([\d.]+)/([\d.]+)GB")
_P_EVO = re.compile(r"Evolution: #(\d+)")
_P_QD = re.compile(r"Qdrant: ([^|]+)")


def timeseries(hours=24):
    def _load():
        path = GINJA_DIR / "perception.log"
        out = []
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                f.seek(max(0, size - 1_500_000))
                raw = f.read().decode(errors="replace")
        except Exception:
            return out
        for line in raw.splitlines():
            if not line.startswith('{"'):
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            s = d.get("summary", "")
            pt = {"ts": d.get("ts")}
            m = _P_LOAD.search(s)
            if m:
                pt["load"] = float(m.group(1))
            m = _P_RAM.search(s)
            if m:
                pt["ram_used"], pt["ram_total"] = float(m.group(1)), float(m.group(2))
            m = _P_GPU.search(s)
            if m:
                pt["gpu_pct"] = int(m.group(1))
                pt["vram_used"], pt["vram_total"] = float(m.group(2)), float(m.group(3))
            m = _P_EVO.search(s)
            if m:
                pt["evolution"] = int(m.group(1))
            m = _P_QD.search(s)
            if m:
                try:
                    pt["qdrant"] = {k.strip(): int(v) for k, v in
                                    (kv.split(":") for kv in m.group(1).split(","))}
                except Exception:
                    pass
            out.append(pt)
        return out

    pts = _cached("timeseries", 60, _load)
    if hours and pts:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        pts = [p for p in pts if (p.get("ts") or "") >= cutoff]
    if len(pts) > 1000:  # multi-day windows: stride-downsample, keep newest point
        stride = -(-len(pts) // 1000)
        pts = pts[::stride] + ([pts[-1]] if (len(pts) - 1) % stride else [])
    return pts


# ── Self-eval, learning narrative, goals ────────────────────────────────────────

def evals():
    def _load():
        out = []
        try:
            for line in (GINJA_DIR / "self-eval.log").read_text().splitlines():
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            pass
        return out
    return _cached("evals", 60, _load)


def learning(last=30):
    hist = _cached("learning", 60,
                   lambda: _read_json("learning-history.json", {}).get("history", []))
    return hist[-last:]


def goals():
    return _cached("goals", 10, lambda: {
        "goals": _read_json("goals.json", {}),
        "goal_stack": _read_json("goal-stack.json", {}),
        "operator_intent": _read_json("operator-intent.json", {}),
    })


# ── Staleness (mtime vs expected cadence — never a service-status guess) ────────

_CADENCE = [
    # (name, file, expected seconds — already padded past the nominal cadence)
    ("perception snapshot", "perception.log", 35 * 60),
    ("evolve loop", "auto-evolve.log", 45 * 60),
    ("self-eval", "self-eval.log", 8 * 3600),
    ("morning brief", "today.md", 26 * 3600),
    ("operator model", "andre-model.json", 26 * 3600),
    ("rss ingest", "rss-ingest.log", 26 * 3600),
    ("qdrant backup", "qdrant-backup.log", 26 * 3600),
    ("memory consolidation", ".last-consolidation", 36 * 3600),
]


def staleness():
    def _load():
        now = time.time()
        rows = []
        for name, fname, expected in _CADENCE:
            try:
                age = now - (GINJA_DIR / fname).stat().st_mtime
            except Exception:
                age = None
            rows.append({
                "name": name,
                "age_s": age,
                "expected_s": expected,
                "overdue_s": max(0.0, age - expected) if age is not None else None,
                "ok": age is not None and age <= expected,
            })
        qdrant_up = False
        try:
            with urllib.request.urlopen(QDRANT_URL + "/collections", timeout=2) as r:
                qdrant_up = r.status == 200
        except Exception:
            pass
        return {"sources": rows, "qdrant_up": qdrant_up}
    return _cached("staleness", 30, _load)


# ── Activity feed (merged real events, newest first) ────────────────────────────

def _tail_text(fname, size):
    try:
        p = GINJA_DIR / fname
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - size))
            return f.read().decode(errors="replace")
    except Exception:
        return ""


def _epoch_local(ts, fmt):
    """Naive local-time string → epoch (auto-evolve/evolution logs)."""
    try:
        return time.mktime(time.strptime(ts, fmt))
    except Exception:
        return None


def _epoch_utc(ts, fmt):
    """Naive UTC string → epoch (suggestion-history outcome_ts)."""
    try:
        return float(calendar.timegm(time.strptime(ts, fmt)))
    except Exception:
        return None


def _event(epoch, source, kind, severity, text):
    return {"epoch": epoch,
            "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch)),
            "source": source, "kind": kind, "severity": severity,
            "text": text[:240]}


_EVOLVE_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (.+)$")


def _evolve_events():
    """auto-evolve.log tail → events. The log writes every line twice, so
    consecutive duplicates are collapsed (not global dedupe — legit repeats
    recur across cycles)."""
    events, prev = [], None
    for line in _tail_text("auto-evolve.log", 24_000).splitlines():
        m = _EVOLVE_LINE.match(line)
        if not m or line == prev:
            prev = line if m else prev
            continue
        prev = line
        ts, msg = m.group(1), m.group(2)
        if msg.startswith("--- Cycle"):
            continue  # internal counter; misleading vs the real evolution #
        epoch = _epoch_local(ts, "%Y-%m-%d %H:%M:%S")
        if epoch is None:
            continue
        if "ROLLBACK" in msg:
            kind, sev = "rollback", "alert"
        elif msg.startswith("Safety OK"):
            kind, sev = "safety", "ok"
        elif msg.startswith("evolve:"):
            kind, sev = "cycle", "info"
        elif msg.startswith("approve:"):
            kind, sev = "approve", "info"
        elif msg.startswith("sleeping"):
            kind, sev = "sleep", "info"
        else:
            kind, sev = "event", "info"
        events.append(_event(epoch, "evolve", kind, sev, msg))
    return events


_CYCLE_HEAD = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] Cycle #(\d+) — (.+)$")
_REFLECT_END = ("Next goal:", "ReAct", "Self-portrait:", "Observed:", "====")


def _reflection_events():
    """evolution.log tail → reflection / goal / technique events per cycle."""
    events = []
    epoch, cycle = None, None
    reflecting, reflection = False, []

    def _flush():
        nonlocal reflecting, reflection
        if reflecting and reflection and epoch is not None:
            events.append(_event(epoch, "reflection", "reflection", "info",
                                 f"#{cycle} · " + " ".join(reflection)))
        reflecting, reflection = False, []

    for line in _tail_text("evolution.log", 40_000).splitlines():
        m = _CYCLE_HEAD.match(line)
        if m:
            _flush()
            epoch = _epoch_local(m.group(1), "%Y-%m-%d %H:%M")
            cycle = m.group(2)
            continue
        if epoch is None:
            continue  # partial block cut off by the tail seek
        if line.startswith(_REFLECT_END):
            _flush()
            if line.startswith("Next goal:"):
                events.append(_event(epoch, "reflection", "goal", "info",
                                     f"#{cycle} goal · " + line[len("Next goal:"):].strip()))
            continue
        if line.startswith("Techniques discovered:"):
            _flush()
            events.append(_event(epoch, "reflection", "technique", "info",
                                 f"#{cycle} · " + line[len("Techniques discovered:"):].strip()))
            continue
        if line.startswith("Reflection:"):
            reflecting = True
            continue
        if reflecting and line.strip():
            reflection.append(line.strip())
    _flush()
    return events


_SUGGESTION_SEV = {"implemented": "ok", "rejected": "warn", "deferred": "info"}


def _suggestion_events():
    """Recent suggestion outcomes. History re-appends the same id on every
    approve pass — dedupe by id keeping the latest outcome_ts (which is UTC,
    unlike the local-naive ts field)."""
    hist = _read_json("suggestion-history.json", {}).get("history", [])
    latest = {}
    for h in hist[-120:]:
        hid = h.get("id")
        if hid and (hid not in latest
                    or (h.get("outcome_ts") or "") >= (latest[hid].get("outcome_ts") or "")):
            latest[hid] = h
    events = []
    for h in latest.values():
        epoch = _epoch_utc(h.get("outcome_ts") or "", "%Y-%m-%d %H:%M")
        if epoch is None:
            continue
        outcome = h.get("outcome") or "pending"
        events.append(_event(epoch, "suggestion", "outcome",
                             _SUGGESTION_SEV.get(outcome, "info"),
                             f"{outcome} · {h.get('text') or ''}"))
    events.sort(key=lambda e: e["epoch"], reverse=True)
    return events[:15]


_CRON_ARTIFACTS = [
    ("morning brief written", "today.md"),
    ("operator model updated", "andre-model.json"),
    ("rss ingest ran", "rss-ingest.log"),
    ("qdrant backup ran", "qdrant-backup.log"),
    ("memory consolidation ran", ".last-consolidation"),
]


def _cron_events():
    now, events = time.time(), []
    for text, fname in _CRON_ARTIFACTS:
        try:
            mtime = (GINJA_DIR / fname).stat().st_mtime
        except Exception:
            continue
        if now - mtime <= 48 * 3600:
            events.append(_event(mtime, "cron", "artifact", "info", text))
    return events


# Inference-lock transitions can't be recovered from disk (the lock file is
# ephemeral), so they're recorded in memory as _vitals() observes them —
# the feed only knows transitions since server start.
_lock_events = deque(maxlen=40)
_last_lock_model = None


def _note_lock_transition(lock):
    global _last_lock_model
    model = lock.get("model") if isinstance(lock, dict) else None
    if model == _last_lock_model:
        return
    now = time.time()
    if model:
        _lock_events.append(_event(now, "lock", "thinking", "info",
                                   f"thinking started with {model}"))
    elif _last_lock_model:
        _lock_events.append(_event(now, "lock", "thinking", "info",
                                   f"thinking finished ({_last_lock_model})"))
    _last_lock_model = model


def activity(limit=50):
    def _load():
        events = (_evolve_events() + _reflection_events() + _suggestion_events()
                  + _cron_events() + list(_lock_events))
        events.sort(key=lambda e: e["epoch"], reverse=True)
        return events
    return _cached("activity", 15, _load)[:max(1, min(200, int(limit)))]


# ── Output: brief, pending suggestions, outcome stats ───────────────────────────

def output():
    def _load():
        brief, brief_age = "", None
        try:
            p = GINJA_DIR / "today.md"
            brief = p.read_text()[:2500]
            brief_age = time.time() - p.stat().st_mtime
        except Exception:
            pass
        pending = _read_json("suggestions.json", {}).get("pending", [])
        hist = _read_json("suggestion-history.json", {}).get("history", [])
        latest = {}
        for h in hist:
            hid = h.get("id")
            if hid and (hid not in latest
                        or (h.get("outcome_ts") or "") >= (latest[hid].get("outcome_ts") or "")):
                latest[hid] = h
        stats, last7d = {}, {}
        week_ago = time.time() - 7 * 86400
        for h in latest.values():
            outcome = h.get("outcome") or "pending"
            stats[outcome] = stats.get(outcome, 0) + 1
            epoch = _epoch_utc(h.get("outcome_ts") or "", "%Y-%m-%d %H:%M")
            if epoch and epoch >= week_ago:
                last7d[outcome] = last7d.get(outcome, 0) + 1
        return {"brief": brief, "brief_age_s": brief_age, "pending": pending,
                "suggestion_stats": stats, "last7d": last7d,
                "total_suggestions": len(latest)}
    return _cached("output", 60, _load)
