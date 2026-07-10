"""Cheap, cached, read-only feeds for the dashboard.

Everything here reads state the brain already writes (JSON files, JSONL logs,
Qdrant over HTTP). A module-level TTL cache means N browser tabs cost one
file-parse per TTL window. Stdlib only — urllib, not requests.
"""

import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from . import spec as specmod

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
        raw = (GINJA_DIR / ".inference.lock").read_text().strip()
        v["lock"] = json.loads(raw) if raw else None
    except Exception:
        pass
    return v


def _portrait_resolved():
    """Spec + resolved archetype colors, so the web page needs no hex table."""
    spec = specmod.load_spec()
    pal = specmod.blended_palette(spec)
    arches = {k: {"hex": a["hex"], "inspiration": a["inspiration"], "shape": a["shape"],
                  "jitter": a["jitter"], "pulse_bias": a["pulse_bias"]}
              for k, a in specmod.ARCHETYPES.items()}
    return {**spec, "_resolved": {"hex": pal["hex"], "jitter": pal["jitter"],
                                  "pulse_bias": pal["pulse_bias"]},
            "_archetypes": arches}


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


def state():
    def _load():
        vitals = _vitals()
        return {
            "self_model": specmod.load_self_model(),
            "portrait": _portrait_resolved(),
            "vitals": vitals,
            "engines": _engines(vitals),
            "ts": time.time(),
        }
    return _cached("state", 2, _load)


def portrait():
    return _portrait_resolved()


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
                f.seek(max(0, size - 400_000))
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
