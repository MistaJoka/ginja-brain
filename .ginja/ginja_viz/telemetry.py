"""Real engine telemetry — every activity value is a measurement, not an opinion.

Each of the 7 engines gets an activity in [0.05, 1.0] derived from observable
state. The mapping is deliberately legible:

  Memory      growth of Qdrant vectors observed this session, plus
              consolidation recency (.last-consolidation mtime)
  Cognition   1.0 while the inference lock is held; otherwise decays with the
              age of the last self-eval / reflection write
  Perception  freshness of the last perception.log snapshot (cadence ~30 min)
  Effector    freshness of the last self-modification artifact
              (evolution.log / visual-approved.md / newest bin/ginja backup)
  Drive       goal pressure: queued + medium goals and operator goal-stack depth
  Safety      recent ROLLBACK in auto-evolve.log pins it high (alarmed);
              a recent "Safety OK" reads as calm vigilance
  Spine       resource-manager pressure: max of load, VRAM fill, GPU util

Everything is file-mtime/tail/HTTP-count based and cached; a refresh costs
a few ms and runs on a background thread, never per-frame.
"""

import json
import time
import urllib.request
from pathlib import Path

GINJA_DIR = Path.home() / ".ginja"
QDRANT_URL = "http://localhost:6333"

ENGINES = ["Memory", "Cognition", "Perception", "Effector", "Drive", "Safety", "Spine"]


def _age_activity(age_s, half_life_s, floor=0.05, ceil=1.0):
    """Freshness: 1.0 at age 0, halving every half_life. Clamped."""
    if age_s is None:
        return floor
    return max(floor, min(ceil, ceil * (0.5 ** (age_s / half_life_s))))


def _mtime_age(path):
    try:
        return max(0.0, time.time() - Path(path).stat().st_mtime)
    except Exception:
        return None


def _qdrant_counts():
    out = {}
    try:
        with urllib.request.urlopen(QDRANT_URL + "/collections", timeout=3) as r:
            names = [c["name"] for c in json.loads(r.read())["result"]["collections"]]
        for n in names:
            req = urllib.request.Request(
                f"{QDRANT_URL}/collections/{n}/points/count",
                data=b'{"exact": false}',
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                out[n] = json.loads(r.read())["result"]["count"]
    except Exception:
        pass
    return out


def _tail(path, size=4000):
    try:
        p = Path(path)
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - size))
            return f.read().decode(errors="replace")
    except Exception:
        return ""


class EngineTelemetry:
    """Compute real activities. Call refresh() from a background thread."""

    def __init__(self):
        self.activities = {e: 0.05 for e in ENGINES}
        self.counts = {}
        self.detail = {}
        self._first_counts = None
        self._first_ts = time.time()
        self._last_growth = 0.0

    def refresh(self, vitals=None):
        """vitals: optional dict with gpu_pct / vram_used / vram_total / load /
        lock — reuses the caller's 1 Hz poll instead of re-measuring."""
        now = time.time()
        v = vitals or {}

        # ── Memory ── vector growth this session + consolidation recency
        counts = _qdrant_counts()
        if counts:
            self.counts = counts
        total = sum(self.counts.values()) if self.counts else 0
        if self._first_counts is None and total:
            self._first_counts, self._first_ts = total, now
        growth_per_hr = 0.0
        if self._first_counts is not None and now > self._first_ts + 60:
            growth_per_hr = (total - self._first_counts) / ((now - self._first_ts) / 3600)
            self._last_growth = growth_per_hr
        consol = _age_activity(_mtime_age(GINJA_DIR / ".last-consolidation"),
                               half_life_s=12 * 3600, floor=0.0, ceil=0.4)
        self.activities["Memory"] = max(0.05, min(1.0,
            0.1 + min(0.5, growth_per_hr / 40.0) + consol))
        self.detail["Memory"] = f"{total} vectors · +{growth_per_hr:.0f}/h"

        # ── Cognition ── thinking now, else decay from last eval/reflection
        lock = v.get("lock")
        if lock:
            self.activities["Cognition"] = 1.0
            self.detail["Cognition"] = f"thinking with {lock.get('model', '?')}"
        else:
            age = min(a for a in (_mtime_age(GINJA_DIR / "self-eval.log"),
                                  _mtime_age(GINJA_DIR / "evolution.log"),
                                  9e9) if a is not None)
            self.activities["Cognition"] = _age_activity(age, half_life_s=45 * 60)
            self.detail["Cognition"] = f"last thought {int(age // 60)}m ago" if age < 9e9 else "idle"

        # ── Perception ── freshness of last world-state snapshot
        page = _mtime_age(GINJA_DIR / "perception.log")
        self.activities["Perception"] = _age_activity(page, half_life_s=40 * 60)
        self.detail["Perception"] = (f"snapshot {int(page // 60)}m ago"
                                     if page is not None else "no snapshots")

        # ── Effector ── freshness of last self-modification artifact
        eage = min(a for a in (_mtime_age(GINJA_DIR / "visual-approved.md"),
                               _mtime_age(GINJA_DIR / "evolution.log"),
                               9e9) if a is not None)
        self.activities["Effector"] = _age_activity(eage, half_life_s=90 * 60)
        self.detail["Effector"] = (f"last act {int(eage // 60)}m ago"
                                   if eage < 9e9 else "dormant")

        # ── Drive ── goal pressure
        qn = mn = sn = 0
        try:
            g = json.loads((GINJA_DIR / "goals.json").read_text())
            qn, mn = len(g.get("queue") or []), len(g.get("medium") or [])
        except Exception:
            pass
        try:
            gs = json.loads((GINJA_DIR / "goal-stack.json").read_text())
            stack = gs.get("goals") or gs.get("stack") or []
            sn = len([x for x in stack
                      if not (isinstance(x, dict) and x.get("status") == "done")])
        except Exception:
            pass
        self.activities["Drive"] = max(0.05, min(1.0,
            0.1 + 0.18 * mn + 0.06 * qn + 0.25 * sn))
        self.detail["Drive"] = f"{mn} medium · {qn} queued · {sn} operator"

        # ── Safety ── alarmed on recent rollback, calm vigilance otherwise
        tail = _tail(GINJA_DIR / "auto-evolve.log", 6000)
        recent_rollback = "ROLLBACK" in tail.split("\n", 1)[-1][-3000:]
        ok_age = _mtime_age(GINJA_DIR / "auto-evolve.log")
        if recent_rollback:
            self.activities["Safety"] = 1.0
            self.detail["Safety"] = "rollback in recent window"
        else:
            self.activities["Safety"] = 0.2 + _age_activity(ok_age, 60 * 60, 0.0, 0.4)
            self.detail["Safety"] = "guarding (no recent rollback)"

        # ── Spine ── resource-manager pressure
        load = float(v.get("load") or 0.0)
        vt = float(v.get("vram_total") or 4096)
        vu = float(v.get("vram_used") or 0)
        gpu = float(v.get("gpu_pct") or 0)
        pressure = max(load / 4.0, vu / vt if vt else 0, gpu / 100.0)
        self.activities["Spine"] = max(0.1, min(1.0, pressure))
        self.detail["Spine"] = f"load {load:.1f} · vram {vu / 1024:.1f}/{vt / 1024:.1f}G"

        return self.activities
