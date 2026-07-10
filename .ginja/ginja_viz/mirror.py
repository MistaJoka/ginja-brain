"""MirrorScene — animates the brain's portrait spec on a braille canvas.

The LLM decides *what* the face is (portrait.json, via spec.py); this module
decides *where every dot goes*, as a pure function of time + spec + live
signals. No LLM calls happen here, ever.
"""

import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .braille import BrailleCanvas, render_bands
from . import spec as specmod

GINJA_DIR = Path.home() / ".ginja"
INFERENCE_LOCK = GINJA_DIR / ".inference.lock"

TAU = 2 * math.pi

ENGINE_GLYPHS = {"Memory": "M", "Cognition": "C", "Perception": "P",
                 "Effector": "E", "Drive": "D", "Safety": "S", "Spine": "◆"}


def _hash(i: float) -> float:
    """Deterministic pseudo-random in [0, 1) — stable across frames."""
    return (math.sin(i * 12.9898 + 78.233) * 43758.5453) % 1.0


def _heartbeat(phase: float) -> float:
    """Cardiac 'lub-dub' waveform in [0, 1]: sharp systole, softer diastole,
    long quiet interval — nothing biological pulses like a sine."""
    p = phase % 1.0
    lub = math.exp(-((p - 0.06) / 0.045) ** 2)
    dub = 0.45 * math.exp(-((p - 0.24) / 0.065) ** 2)
    return lub + dub


def _breath(t: float) -> float:
    """Slow respiration with natural variability (drifting rate, ~6.5 s cycle)."""
    return 1.0 + 0.045 * math.sin(TAU * t / 6.5 + 0.6 * math.sin(t * 0.13))


# ── Live signals ────────────────────────────────────────────────────────────────

class LiveSignals:
    """1 Hz background poller: GPU, load, and the inference lock.

    Never polls faster than its interval, so the mirror adds no meaningful
    load to the box regardless of render FPS.
    """

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self.gpu_pct = 0
        self.vram_used = 0
        self.vram_total = 4096
        self.load = 0.0
        self.lock = None          # {"model": str, "since": iso} | None
        self.gpu_history = []     # last 240 (t, gpu_pct) samples at 1 Hz
        self.engines = None       # {engine: activity} — real telemetry
        self.engine_detail = {}
        self.mem_counts = {}
        self._telemetry = None
        self._tick = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._poll()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self.interval):
            self._poll()

    def _poll(self):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2)
            parts = [x.strip() for x in r.stdout.strip().split(",")]
            self.gpu_pct, self.vram_used, self.vram_total = (
                int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            pass
        try:
            self.load = float(Path("/proc/loadavg").read_text().split()[0])
        except Exception:
            pass
        try:
            raw = INFERENCE_LOCK.read_text().strip()
            self.lock = json.loads(raw) if raw else None
        except Exception:
            self.lock = None
        self.gpu_history.append((time.time(), self.gpu_pct))
        if len(self.gpu_history) > 240:
            self.gpu_history.pop(0)
        # real engine telemetry every 5th poll (~5 s) — file mtimes + Qdrant counts
        if self._tick % 5 == 0:
            try:
                if self._telemetry is None:
                    from .telemetry import EngineTelemetry
                    self._telemetry = EngineTelemetry()
                self.engines = dict(self._telemetry.refresh({
                    "gpu_pct": self.gpu_pct, "vram_used": self.vram_used,
                    "vram_total": self.vram_total, "load": self.load,
                    "lock": self.lock}))
                self.engine_detail = dict(self._telemetry.detail)
                self.mem_counts = dict(self._telemetry.counts)
            except Exception:
                pass
        self._tick += 1

    def thinking_secs(self):
        """Seconds the current inference has been running, or None."""
        if not self.lock:
            return None
        since = self.lock.get("since")
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(str(since))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - dt).total_seconds())
        except Exception:
            return 0


# ── Scene ───────────────────────────────────────────────────────────────────────

class MirrorScene:
    def __init__(self, spec: dict, cols: int, rows: int, maturity: float = 1.0):
        self.spec = spec
        self.maturity = max(0.0, min(1.0, maturity))
        self._m = 0.0
        self.cols, self.rows = max(20, cols), max(3, rows)
        self.pal = specmod.blended_palette(spec)
        self.dim = BrailleCanvas(self.cols, self.rows)
        self.mid = BrailleCanvas(self.cols, self.rows)
        self.bright = BrailleCanvas(self.cols, self.rows)
        self.W, self.H = self.dim.width, self.dim.height
        # pixels are ~twice as tall as wide in a terminal cell → squash y
        self.aspect = 0.55

    # -- composition ------------------------------------------------------------

    def compose(self, t: float, signals=None):
        """Fill the three canvases + overlay for time t. Returns overlay dict."""
        for cv in (self.dim, self.mid, self.bright):
            cv.clear()
        gpu = (signals.gpu_pct if signals else 0) / 100.0
        thinking = bool(signals and signals.lock)

        s = self.spec
        cx, cy = self.W / 2, self.H / 2
        R = s["core"]["radius"] * min(self.W, self.H / self.aspect)
        # no body pulsing (Andre's call) — life shows in the membrane morph instead
        pulse = 1.0
        # development replays each session over ~75 s, capped by real phase
        x = min(1.0, t / 75.0)
        self._m = self.maturity * (x * x * (3 - 2 * x))
        jit = self.pal["jitter"]
        jx = jit * R * 0.15 * math.sin(t * 2.1)
        jy = jit * R * 0.10 * math.sin(t * 3.3 + 1.0)
        cx, cy = cx + jx, cy + jy * self.aspect

        # live telemetry overrides the spec's orbiter activities — real, not chosen
        live = getattr(signals, "engines", None) if signals else None
        orbiters = ([{"engine": o["engine"], "activity": live.get(o["engine"], o["activity"])}
                     for o in s["orbiters"]] if live else s["orbiters"])

        self._weather(t, s["weather"])
        self._core(s["core"], t, cx, cy, R * pulse, gpu)
        overlay = self._orbiters(orbiters, t, cx, cy, R)
        self._particles(s["particles"], t, cx, cy, R)
        if thinking:
            self._flare(t, cx, cy, R * pulse)
        self._sparkline(signals)
        return overlay

    def _sparkline(self, signals):
        """Real GPU history (1 Hz samples) as a braille strip along the bottom."""
        hist = getattr(signals, "gpu_history", None) if signals else None
        if not hist or len(hist) < 2:
            return
        h_px = 6
        y0 = self.H - 1
        n = min(len(hist), self.W)
        samples = [g for _, g in hist[-n:]]
        x_start = self.W - n
        prev = None
        for i, g in enumerate(samples):
            x = x_start + i
            y = y0 - int(g / 100 * h_px)
            if prev is not None:
                self.mid.line(x - 1, prev, x, y)
            else:
                self.mid.plot(x, y)
            prev = y

    def _xy(self, cx, cy, r, ang):
        return cx + r * math.cos(ang), cy + r * math.sin(ang) * self.aspect

    def _core(self, core, t, cx, cy, R, gpu):
        shape = core["shape"]
        density = core["density"]
        asym = core["asymmetry"]
        b, m = self.bright, self.mid

        if shape == "cell":
            # ── ontogeny, done properly: cell → curled fetus → standing human ──
            # m ∈ [0,1] replays development each session, capped by real phase.
            mph = self._m
            smooth = lambda x: 0.0 if x <= 0 else 1.0 if x >= 1 else x * x * (3 - 2 * x)
            be = smooth((mph - 0.35) / 0.20)     # body emergence (fetus fades in)
            u_st = smooth((mph - 0.52) / 0.40)   # pose: 0 fetal, 1 standing
            mh = smooth((mph - 0.35) / 0.25)     # nucleus → head
            hf = smooth((mph - 0.82) / 0.18)     # membrane → faint halo

            # ── figure scale: embryo lives inside the cell, adult fills the frame ──
            s_full = 0.86 * self.H / (7.5 * self.aspect)   # px per head-unit
            s_emb = 0.5 * R
            s = s_emb + (s_full - s_emb) * smooth((mph - 0.38) / 0.62)

            # ── membrane / amniotic sac / halo ──
            amp = 1.0 - 0.55 * mph
            sm = smooth((mph - 0.35) / 0.50)
            sx = 1.0 + (max(0.5, 1.65 * s / R) - 1.0) * sm
            sy = 1.0 + (max(1.0, 4.05 * s / R) - 1.0) * sm

            def membrane_r(a):
                return (1.0
                        + amp * 0.20 * math.sin(2 * a + t * 0.90)
                        + amp * 0.13 * math.sin(3 * a - t * 0.63 + 1.7)
                        + amp * (0.08 + asym * 0.06) * math.sin(5 * a + t * 1.15 + 4.0)
                        + amp * 0.04 * math.sin(9 * a - t * 1.90))

            def mem_xy(a, frac=1.0):
                f = membrane_r(a) * frac
                return (cx + R * sx * f * math.cos(a),
                        cy + R * sy * f * math.sin(a) * self.aspect)

            n_mem = int((140 + 160 * density) * (1.0 - 0.72 * hf))
            for i in range(n_mem):
                a = TAU * i / max(1, n_mem)
                x, y = mem_xy(a)
                (m if hf > 0.3 else b).plot(int(x), int(y))
                if i % 2 == 0 and hf < 0.5:
                    x2, y2 = mem_xy(a, 0.97)
                    m.plot(int(x2), int(y2))

            n_cil = int(26 * max(0.0, 1.0 - mph / 0.35))
            for k in range(n_cil):
                a = TAU * k / max(1, n_cil) + 0.12 * math.sin(t * 0.7 + k)
                sway_c = 0.55 * math.sin(t * 2.1 + k * 1.9 + _hash(k) * TAU)
                ln = 2.5 + 2.5 * _hash(k * 3.3)
                x0, y0 = mem_xy(a)
                x1, y1 = mem_xy(a + sway_c * 0.03, 1.0 + ln / R)
                m.line(x0, y0, x1, y1)

            # ── anatomy: classical 7.5-head proportions, joints in head-units ──
            STAND = {
                "head": (0.0, 0.5), "neck": (0.0, 1.2), "chest": (0.0, 2.0),
                "pelvis": (0.0, 3.7),
                "shL": (-0.80, 1.5), "shR": (0.80, 1.5),
                "elL": (-1.02, 2.85), "elR": (1.02, 2.85),
                "wrL": (-1.12, 3.90), "wrR": (1.12, 3.90),
                "hipL": (-0.48, 3.85), "hipR": (0.48, 3.85),
                "knL": (-0.55, 5.50), "knR": (0.55, 5.50),
                "anL": (-0.60, 7.25), "anR": (0.60, 7.25),
                "ftL": (-0.90, 7.40), "ftR": (0.90, 7.40),
            }
            FETAL = {
                "head": (-0.67, 2.55), "neck": (-0.27, 3.05), "chest": (0.18, 3.45),
                "pelvis": (0.78, 4.10),
                "shL": (-0.37, 3.30), "shR": (-0.17, 2.80),
                "elL": (-0.72, 3.60), "elR": (-0.52, 3.20),
                "wrL": (-0.87, 3.15), "wrR": (-0.77, 2.80),
                "hipL": (0.63, 4.25), "hipR": (0.88, 3.95),
                "knL": (-0.07, 4.40), "knR": (0.13, 4.10),
                "anL": (-0.37, 4.90), "anR": (-0.17, 4.65),
                "ftL": (-0.72, 4.95), "ftR": (-0.52, 4.70),
            }
            # motion: breath, weight shift, arm sway, fetal twitches — all layered
            s_br = 0.5 + 0.5 * math.sin(TAU * t / 6.5 + 0.6 * math.sin(t * 0.13))
            ws = 0.12 * math.sin(t * 0.19) * u_st
            bob = 0.08 * math.sin(t * 0.45) * (1 - u_st)
            J = {}
            for k in STAND:
                fx, fy = FETAL[k]
                sx_, sy_ = STAND[k]
                x = fx + (sx_ - fx) * u_st
                y = fy + (sy_ - fy) * u_st + bob
                if k in ("head", "neck", "chest", "shL", "shR", "elL", "elR", "wrL", "wrR"):
                    y -= 0.045 * s_br
                    x += ws * 0.4
                if k == "head":
                    x += ws * -0.25 + 0.02 * math.sin(t * 0.6)
                if k in ("pelvis", "hipL", "hipR"):
                    x += ws
                if k in ("knL", "knR"):
                    x += ws * 0.35
                if k in ("elL", "wrL"):
                    x += 0.05 * math.sin(t * 0.50) * u_st
                if k in ("elR", "wrR"):
                    x += 0.05 * math.sin(t * 0.50 + 2.6) * u_st
                if u_st < 0.5 and k in ("wrL", "wrR", "ftL", "ftR", "knL", "knR"):
                    tw = max(0.0, math.sin(t * 0.83 + _hash(hash(k) % 97) * TAU)) ** 9
                    x += tw * 0.12 * (1 - u_st)
                    y += tw * 0.08 * (1 - u_st)
                J[k] = (x, y)

            def P(pt):
                # body space (head-units, figure centered at (0, 3.75)) → pixels
                return (cx + pt[0] * s, cy + (pt[1] - 3.75) * s * self.aspect)

            chub = 1.55 - 0.55 * u_st   # fetuses are round; adults taper
            CAPS = [
                ("neck", "head", 0.22, 0.19),
                ("neck", "pelvis", 0.62, 0.42),
                ("shL", "shR", 0.22, 0.22),
                ("shL", "elL", 0.17, 0.13), ("elL", "wrL", 0.13, 0.10),
                ("shR", "elR", 0.17, 0.13), ("elR", "wrR", 0.13, 0.10),
                ("hipL", "knL", 0.26, 0.17), ("knL", "anL", 0.17, 0.11),
                ("hipR", "knR", 0.26, 0.17), ("knR", "anR", 0.17, 0.11),
                ("anL", "ftL", 0.12, 0.10), ("anR", "ftR", 0.12, 0.10),
            ]

            if be > 0.01:
                for ci, (k0, k1, r0, r1) in enumerate(CAPS):
                    p0, p1 = J[k0], J[k1]
                    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                    ln = math.sqrt(dx * dx + dy * dy) or 1e-6
                    nxu, nyu = -dy / ln, dx / ln
                    steps = max(2, int(ln * s / 1.6))
                    for kk in range(steps + 1):
                        u = kk / steps
                        if _hash(ci * 131 + kk * 7.7) > be:
                            continue
                        r = (r0 + (r1 - r0) * u) * chub
                        bxu = p0[0] + dx * u
                        byu = p0[1] + dy * u
                        for sgn in (-1, 1):
                            x, y = P((bxu + nxu * r * sgn, byu + nyu * r * sgn))
                            b.plot(int(x), int(y))
                        if kk % 2 == 0:
                            off = (_hash(ci * 57 + kk * 3.1 + math.floor(t * 2)) * 2 - 1) * r * 0.8
                            x, y = P((bxu + nxu * off, byu + nyu * off))
                            m.plot(int(x), int(y))

            # ground shadow once it stands
            if hf > 0.2:
                gy = P((0, 7.55))[1]
                grx = 1.35 * s
                for i in range(int(20 * hf)):
                    a = TAU * i / max(1, int(20 * hf))
                    self.dim.plot(int(cx + ws * s + grx * math.cos(a)),
                                  int(gy + grx * 0.14 * math.sin(a) * self.aspect))

            # ── nucleus → head: wanders as a cell, becomes the mind ──
            wx = cx + R * 0.20 * (math.sin(t * 0.23 + 1.1) + 0.5 * math.sin(t * 0.111 + 4.2))
            wy = cy + R * 0.16 * (math.sin(t * 0.17) + 0.5 * math.sin(t * 0.087 + 2.6)) * self.aspect
            hx_px, hy_px = P(J["head"])
            ncx = wx + (hx_px - wx) * mh
            ncy = wy + (hy_px - wy) * mh
            nr = (1 - mh) * 0.24 * R + mh * 0.5 * s * chub
            namp = 1.0 - 0.72 * mh

            def nucleus_r(a):
                return nr * (1.0
                             + namp * 0.18 * math.sin(2 * a - t * 0.74 + 2.3)
                             + namp * 0.12 * math.sin(3 * a + t * 1.07)
                             + namp * 0.06 * math.sin(5 * a - t * 1.51 + 5.1))
            n_wall = int(40 + 50 * density)
            for i in range(n_wall):
                a = TAU * i / n_wall
                x, y = self._xy(ncx, ncy, nucleus_r(a), a)
                b.plot(int(x), int(y))
            n_nuc = int(30 + 50 * density)
            for i in range(n_nuc):
                a = TAU * _hash(i * 4.1) + t * 0.07
                rr = nucleus_r(a) * (_hash(i * 2.7) ** 0.6)
                x, y = self._xy(ncx, ncy, rr, a)
                (b if rr > nr * 0.7 else m).plot(int(x), int(y))

            # ── particles migrate: cytoplasm swirl → flesh inside the capsules ──
            n_cyt = int(70 + 150 * density)
            for i in range(n_cyt):
                h1, h2 = _hash(i * 1.9), _hash(i * 5.3)
                h3, h4 = _hash(i * 7.7), _hash(i * 3.7)
                wgt = max(0.0, min(1.0, (mph - 0.30 - 0.40 * h3) / 0.20))
                w = 0.06 + 0.22 * h2
                a = TAU * h1 + t * w * (1 + 0.35 * math.sin(t * 0.21 + i))
                frac = 0.30 + 0.62 * (h2 ** 0.7) + 0.05 * math.sin(t * 0.6 + i * 2.2)
                cxp, cyp = mem_xy(a, min(0.93, frac))
                if wgt <= 0.0:
                    m.plot(int(cxp), int(cyp))
                    continue
                k0, k1, r0, r1 = CAPS[int(h1 * len(CAPS)) % len(CAPS)]
                p0, p1 = J[k0], J[k1]
                u = h2
                bxu = p0[0] + (p1[0] - p0[0]) * u
                byu = p0[1] + (p1[1] - p0[1]) * u
                dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                ln = math.sqrt(dx * dx + dy * dy) or 1e-6
                r = (r0 + (r1 - r0) * u) * chub
                off = r * (h4 * 2 - 1) * (0.85 + 0.15 * math.sin(t * 0.9 + i))
                fx, fy = P((bxu - dy / ln * off, byu + dx / ln * off))
                x = cxp + (fx - cxp) * wgt
                y = cyp + (fy - cyp) * wgt
                m.plot(int(x), int(y))
        elif shape == "eye":
            b.ellipse(cx, cy, R, R * self.aspect)
            b.ellipse(cx, cy, R - 1, (R - 1) * self.aspect)
            iris = R * 0.60
            n = int(40 + 80 * density)
            for i in range(n):
                a = TAU * i / n + t * 0.15
                rr = iris * (0.75 + 0.25 * _hash(i * 3.7))
                x, y = self._xy(cx, cy, rr, a)
                m.plot(int(x), int(y))
            pr = max(1, R * 0.22)
            b.circle(cx, cy, 0)  # ensure center dot
            for i in range(int(pr)):
                b.ellipse(cx, cy, i, i * self.aspect)
            gl = self._xy(cx, cy, R * 0.45, -TAU / 8)
            b.plot(int(gl[0]), int(gl[1]))

        elif shape == "binocular":
            off = R * 0.62
            er = R * 0.48
            look = math.sin(t * 0.4) * er * 0.25
            for sx in (-1, 1):
                ex = cx + sx * off
                b.ellipse(ex, cy, er, er * self.aspect)
                for i in range(int(max(1, er * 0.30))):
                    b.ellipse(ex + look, cy, i, i * self.aspect)

        elif shape == "torus":
            Rm, rm = R * 0.75, R * 0.38
            rx, rz = t * 0.5, t * 0.23
            n = int(120 + 380 * density)
            for i in range(n):
                u = TAU * _hash(i * 1.3)
                v = TAU * _hash(i * 2.9 + 5)
                x = (Rm + rm * math.cos(v)) * math.cos(u)
                y = (Rm + rm * math.cos(v)) * math.sin(u) * (1 - asym * 0.5)
                z = rm * math.sin(v)
                y, z = (y * math.cos(rx) - z * math.sin(rx),
                        y * math.sin(rx) + z * math.cos(rx))
                x, y = (x * math.cos(rz) - y * math.sin(rz),
                        x * math.sin(rz) + y * math.cos(rz))
                cv = b if z > 0 else m
                cv.plot(int(cx + x), int(cy + y * self.aspect))

        elif shape == "spiral":
            arms = 2
            n = int(80 + 240 * density)
            for arm in range(arms):
                for i in range(n // arms):
                    th = 3 * TAU * i / (n // arms)
                    r = R * th / (3 * TAU)
                    a = th + t * 0.6 + arm * math.pi * (1 + asym * 0.3)
                    x, y = self._xy(cx, cy, r, a)
                    (b if i % 3 else m).plot(int(x), int(y))

        elif shape == "starburst":
            rays = int(8 + 16 * density)
            for i in range(rays):
                a = TAU * i / rays + t * 0.1
                ln = R * (0.5 + 0.5 * abs(math.sin(t * 1.3 + _hash(i) * TAU)))
                x0, y0 = self._xy(cx, cy, R * 0.12, a)
                x1, y1 = self._xy(cx, cy, ln, a)
                b.line(x0, y0, x1, y1)

        elif shape == "lissajous":
            n = int(100 + 300 * density)
            for i in range(n):
                p = i / n
                x = cx + R * math.sin(3 * TAU * p + t * 0.7)
                y = cy + R * self.aspect * math.sin(4 * TAU * p + t * 0.5 + asym)
                (b if i > n * 0.7 else m).plot(int(x), int(y))

        elif shape == "reticle":
            m.line(cx - R, cy, cx + R, cy)
            m.line(cx, cy - R * self.aspect, cx, cy + R * self.aspect)
            br = R * 0.9
            arm = R * 0.28
            rot = t * 0.25
            for i in range(4):
                a = rot + TAU * i / 4 + TAU / 8
                x, y = self._xy(cx, cy, br, a)
                x2, y2 = self._xy(cx, cy, br, a + 0.32)
                x3, y3 = self._xy(cx, cy, br - arm, a)
                b.line(x, y, x2, y2)
                b.line(x, y, x3, y3)
            for i in range(3):
                bx = cx + (2 * _hash(i * 7 + math.floor(t / 4)) - 1) * R * 1.3
                by = cy + (2 * _hash(i * 13 + math.floor(t / 4)) - 1) * R * 0.8 * self.aspect
                sz = 3 + 4 * _hash(i * 3)
                self.dim.rect(bx - sz, by - sz * self.aspect, bx + sz, by + sz * self.aspect)

        elif shape == "blocks":
            n = 4
            bw = R * 0.42
            gap = bw * 0.35
            total = n * bw + (n - 1) * gap
            x0 = cx - total / 2
            for i in range(n):
                phase = math.sin(t * 1.1 + i * 1.5)
                h = R * self.aspect * (0.9 + 0.35 * phase * (0.4 + 0.6 * _hash(i)))
                bx = x0 + i * (bw + gap)
                b.rect(bx, cy - h, bx + bw, cy + h)
                for yy in range(int(cy - h) + 2, int(cy + h), 4):
                    m.line(bx + 1, yy, bx + bw - 1, yy)

        elif shape == "rain":
            step = 2
            for col in range(0, self.W, step):
                speed = 8 + 18 * _hash(col * 0.37)
                head = (t * speed + _hash(col) * (self.H + 24)) % (self.H + 24) - 12
                self.bright.plot(col, int(head))
                tail = int(5 + 10 * _hash(col * 1.7))
                for k in range(1, tail):
                    (m if k < 3 else self.dim).plot(col, int(head - k))

        elif shape == "drift":
            n = int(60 + 140 * density)
            breathe = 1.0 + 0.18 * math.sin(t * 0.6)
            for i in range(n):
                a = TAU * _hash(i * 1.7) + t * 0.05 * (1 + _hash(i))
                rr = R * breathe * (0.25 + 0.85 * _hash(i * 2.3) ** 0.5)
                x, y = self._xy(cx, cy, rr, a)
                w = 1.5 * math.sin(t * 0.9 + _hash(i * 5) * TAU)
                (m if _hash(i * 9) > 0.3 else b).plot(int(x + w), int(y))

    def _orbiters(self, orbiters, t, cx, cy, R):
        overlay = {}
        n = len(orbiters)
        for i, o in enumerate(orbiters):
            act = o["activity"]
            # firefly drift: wobbling radius, uneven speed, gentle vertical bob
            orbit = R * (1.45 + 0.13 * (i % 3)) * (1 + 0.06 * math.sin(t * 0.33 + i * 2.1))
            a = (TAU * i / n + t * (0.12 + 0.5 * act)
                 + 0.25 * math.sin(t * 0.47 + _hash(i) * TAU))
            x, y = self._xy(cx, cy, orbit, a)
            y += 1.5 * math.sin(t * 1.3 + i * 2.7) * self.aspect
            size = 1 + int(act * 2)
            for rr in range(size):
                self.mid.ellipse(x, y, rr, rr * self.aspect, steps=12)
            row, col = int(y) >> 2, int(x) >> 1
            if 0 <= row < self.rows and 0 <= col < self.cols:
                overlay[(row, col)] = (ENGINE_GLYPHS[o["engine"]], 2)
        return overlay

    def _particles(self, particles, t, cx, cy, R):
        style = particles["style"]
        for i in range(particles["count"]):
            h1, h2 = _hash(i * 3.1), _hash(i * 7.7)
            if style == "orbit":
                a = TAU * h1 + t * 0.2 * (0.5 + h2)
                r = R * (1.8 + 1.6 * h2)
                x, y = self._xy(cx, cy, r, a)
            elif style == "rise":
                x = h1 * self.W
                y = (h2 * self.H - t * (4 + 6 * h1)) % self.H
            elif style == "fall":
                x = h1 * self.W
                y = (h2 * self.H + t * (4 + 6 * h1)) % self.H
            else:  # swirl
                a = TAU * h1 + t * (0.3 + 0.4 * h2)
                r = (self.W / 2) * ((h2 + t * 0.02) % 1.0)
                x, y = self._xy(cx, cy, r, a)
            self.dim.plot(int(x), int(y))

    def _weather(self, t, weather):
        if weather == "clear":
            return
        if weather == "storm":
            for i in range(24):
                x0 = (_hash(i) * self.W + t * 30) % self.W
                y0 = _hash(i * 3) * self.H
                self.dim.line(x0, y0, x0 - 4, y0 + 6)
        elif weather == "aurora":
            # field-wide twinkle — dots fading in and out on their own rhythms
            for i in range(30):
                if math.sin(t * (0.6 + _hash(i)) + _hash(i * 3) * TAU) > 0.3:
                    self.dim.plot(int(_hash(i * 7) * self.W), int(_hash(i * 11) * self.H))
        elif weather == "drift":
            for i in range(16):
                x = (_hash(i * 11) * self.W + t * (2 + 3 * _hash(i))) % self.W
                y = _hash(i * 5) * self.H
                self.dim.plot(int(x), int(y))

    def _flare(self, t, cx, cy, R):
        rays = 8
        for i in range(rays):
            a = TAU * i / rays + t * 2.2
            x0, y0 = self._xy(cx, cy, R * 1.02, a)
            x1, y1 = self._xy(cx, cy, R * (1.18 + 0.06 * math.sin(t * 9 + i)), a)
            self.bright.line(x0, y0, x1, y1)

    # -- output -----------------------------------------------------------------

    def frame(self, t: float, signals=None) -> list:
        """Plain braille rows (no color) — used by `watch` and smoke tests."""
        overlay = self.compose(t, signals)
        rows = []
        canvases = (self.dim, self.mid, self.bright)
        for r in range(self.rows):
            chars = []
            for c in range(self.cols):
                ov = overlay.get((r, c))
                if ov is not None:
                    chars.append(ov[0])
                    continue
                bits = 0
                for cv in canvases:
                    bits |= cv.buf[r * self.cols + c]
                chars.append(chr(0x2800 + bits) if bits else " ")
            rows.append("".join(chars))
        return rows

    def frame_ansi(self, t: float, signals=None) -> list:
        overlay = self.compose(t, signals)
        return render_bands((self.dim, self.mid, self.bright),
                            list(self.pal["ansi"]), overlay)

    def status_lines(self, signals=None) -> list:
        s = self.spec
        pal = self.pal
        dimc, midc, brightc = pal["ansi"]
        arch = specmod.ARCHETYPES[pal["primary"]]["inspiration"]
        if pal["secondary"]:
            arch += f"  ×  {specmod.ARCHETYPES[pal['secondary']]['inspiration']}"
        l1 = f"\x1b[38;5;{brightc}m{s['motto']}\x1b[0m  \x1b[38;5;{dimc}m·  {arch}\x1b[0m"
        l2 = f"\x1b[38;5;{dimc}mevo #{s['evolution_count']}\x1b[0m"
        lines = [_center_ansi(l1, self.cols), _center_ansi(l2, self.cols)]
        if signals is not None:
            counts = getattr(signals, "mem_counts", None) or {}
            vec = sum(counts.values())
            base = (f"\x1b[38;5;{dimc}mgpu {signals.gpu_pct}%  ·  "
                    f"vram {signals.vram_used / 1024:.1f}/{signals.vram_total / 1024:.1f}G  ·  "
                    f"load {signals.load:.2f}"
                    + (f"  ·  {vec:,} vectors" if vec else "") + "\x1b[0m")
            if signals.lock:
                model = signals.lock.get("model", "?")
                secs = int(signals.thinking_secs() or 0)
                lines.append(_center_ansi(
                    f"\x1b[38;5;{brightc}m▶ thinking with {model} · {secs}s\x1b[0m  " + base,
                    self.cols))
            else:
                lines.append(_center_ansi(base, self.cols))
            # rotate through real per-engine detail so the numbers explain themselves
            detail = getattr(signals, "engine_detail", None) or {}
            if detail:
                from .telemetry import ENGINES as _E
                eng = _E[int(time.monotonic() / 4) % len(_E)]
                act = (getattr(signals, "engines", None) or {}).get(eng, 0)
                bar = "▰" * round(act * 8) + "▱" * (8 - round(act * 8))
                lines.append(_center_ansi(
                    f"\x1b[38;5;{midc}m{eng}\x1b[0m \x1b[38;5;{brightc}m{bar}\x1b[0m "
                    f"\x1b[38;5;{dimc}m{detail.get(eng, '')}\x1b[0m", self.cols))
        return lines


def _visible_len(s: str) -> int:
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\x1b":
            j = s.find("m", i)
            i = (j + 1) if j != -1 else len(s)
        else:
            out += 1
            i += 1
    return out


def _center_ansi(s: str, width: int) -> str:
    pad = max(0, (width - _visible_len(s)) // 2)
    return " " * pad + s


# ── Entry points ────────────────────────────────────────────────────────────────

_strip_cache = {}


def mirror_strip(frame: int, self_model: dict, gpu_pct: int,
                 cols: int = 34, rows: int = 4) -> str:
    """Small plain-text mirror band for embedding in `ginja watch` panels."""
    key = (cols, rows)
    scene = _strip_cache.get(key)
    spec_mtime = specmod.PORTRAIT_FILE.stat().st_mtime if specmod.PORTRAIT_FILE.exists() else 0
    if scene is None or scene[1] != spec_mtime:
        # strips are tiny — stay at the cell stage, a humanoid would be unreadable
        scene = (MirrorScene(specmod.load_spec(), cols, rows, maturity=0.0), spec_mtime)
        _strip_cache[key] = scene
    try:
        raw = INFERENCE_LOCK.read_text().strip()
        lock = json.loads(raw) if raw else None
    except Exception:
        lock = None
    sig = type("S", (), {"gpu_pct": gpu_pct, "lock": lock, "load": 0.0})()
    return "\n".join(scene[0].frame(frame * 0.25, sig))


def run(fps: float = 24.0):
    """Full-screen mirror: raw ANSI on the alternate screen. Ctrl-C to exit."""
    fps = max(1.0, min(60.0, fps))
    size = shutil.get_terminal_size((100, 32))
    cols, rows = size.columns, max(8, size.lines - 5)  # 4 status + 1 spare

    spec = specmod.load_spec()
    maturity = specmod.maturity_from_phase(specmod.load_self_model().get("phase", "mature"))
    scene = MirrorScene(spec, cols, rows, maturity=maturity)
    signals = LiveSignals().start()
    spec_mtime = specmod.PORTRAIT_FILE.stat().st_mtime if specmod.PORTRAIT_FILE.exists() else 0
    last_spec_check = 0.0

    out = sys.stdout
    out.write("\x1b[?1049h\x1b[?25l\x1b[2J")  # alt screen, hide cursor
    t0 = time.monotonic()
    try:
        while True:
            t = time.monotonic() - t0
            # hot-reload the spec if the brain redraws itself mid-session
            if t - last_spec_check > 5:
                last_spec_check = t
                mt = specmod.PORTRAIT_FILE.stat().st_mtime if specmod.PORTRAIT_FILE.exists() else 0
                if mt != spec_mtime:
                    spec_mtime = mt
                    scene = MirrorScene(specmod.load_spec(), cols, rows, maturity=maturity)
            rows_ansi = scene.frame_ansi(t, signals)
            rows_ansi += scene.status_lines(signals)
            out.write("\x1b[H" + "\x1b[K\r\n".join(rows_ansi) + "\x1b[K")
            out.flush()
            elapsed = (time.monotonic() - t0) - t
            time.sleep(max(0.0, 1.0 / fps - elapsed))
    except KeyboardInterrupt:
        pass
    finally:
        signals.stop()
        out.write("\x1b[?25h\x1b[?1049l")  # restore cursor + screen
        out.flush()
