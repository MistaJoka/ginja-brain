"""Portrait spec — the contract between the brain's self-image and its mirrors.

The LLM (via `ginja portrait respec`) authors /home/ginja/.ginja/portrait.json.
Everything it writes passes through validate_spec(): enum whitelists, numeric
clamps, per-field fallbacks. The renderers (mirror.py, web/index.html) consume
only the validated spec — the model can choose and parameterize, never inject.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

GINJA_DIR = Path.home() / ".ginja"
PORTRAIT_FILE = GINJA_DIR / "portrait.json"
SELF_MODEL_FILE = GINJA_DIR / "self-model.json"

ENGINES = ["Memory", "Cognition", "Perception", "Effector", "Drive", "Safety", "Spine"]

CORE_SHAPES = ["cell", "eye", "binocular", "torus", "spiral", "starburst",
               "lissajous", "reticle", "blocks", "rain", "drift"]
WEATHERS = ["clear", "drift", "storm", "aurora"]
PARTICLE_STYLES = ["orbit", "rise", "fall", "swirl"]

# Sci-fi archetypes: each is a complete deterministic preset. The brain picks
# one (or blends two); the preset supplies geometry + palette for both the
# braille mirror (ansi bands: dim/mid/bright 256-color codes) and the web page
# (hex). `blurb` is shown to the LLM in the respec menu.
ARCHETYPES = {
    "jarvis": {
        "inspiration": "JARVIS — Iron Man",
        "blurb": "holographic HUD, cool competence, orbiting readouts",
        "shape": "torus",
        "ansi": (24, 45, 51),
        "hex": {"bg": "#020b12", "primary": "#00e5ff", "secondary": "#4dd0e1",
                "accent": "#b2ebf2", "text": "#d9f6fb"},
        "jitter": 0.0, "pulse_bias": 0.2,
    },
    "samantha": {
        "inspiration": "Samantha — Her",
        "blurb": "no face at all: warm breathing presence, words up front",
        "shape": "drift",
        "ansi": (131, 209, 216),
        "hex": {"bg": "#1a0f0d", "primary": "#ff6f61", "secondary": "#ffb4a2",
                "accent": "#ffe8d6", "text": "#ffe8d6"},
        "jitter": 0.05, "pulse_bias": -0.1,
    },
    "wall-e": {
        "inspiration": "WALL-E",
        "blurb": "curious binocular eyes, earthy, playful",
        "shape": "binocular",
        "ansi": (94, 172, 214),
        "hex": {"bg": "#141008", "primary": "#e8a33d", "secondary": "#a67433",
                "accent": "#f5d491", "text": "#f2e3c2"},
        "jitter": 0.35, "pulse_bias": 0.1,
    },
    "hal": {
        "inspiration": "HAL 9000 — 2001: A Space Odyssey",
        "blurb": "one unblinking red eye, deep slow pulse, few words",
        "shape": "eye",
        "ansi": (52, 160, 196),
        "hex": {"bg": "#000000", "primary": "#ff2b2b", "secondary": "#8a0f0f",
                "accent": "#ffd7a8", "text": "#e6e6e6"},
        "jitter": 0.0, "pulse_bias": -0.3,
    },
    "machine": {
        "inspiration": "The Machine — Person of Interest",
        "blurb": "surveillance reticles on black; the knowledge graph is the face",
        "shape": "reticle",
        "ansi": (240, 250, 226),
        "hex": {"bg": "#050505", "primary": "#f5f5f5", "secondary": "#e03131",
                "accent": "#ffd43b", "text": "#f1f3f5"},
        "jitter": 0.15, "pulse_bias": 0.0,
    },
    "glados": {
        "inspiration": "GLaDOS — Portal",
        "blurb": "clinical amber optic, cold lab gray, dry wit",
        "shape": "eye",
        "ansi": (240, 214, 220),
        "hex": {"bg": "#101312", "primary": "#ffb000", "secondary": "#6c757d",
                "accent": "#ffe066", "text": "#ced4da"},
        "jitter": 0.08, "pulse_bias": -0.15,
    },
    "mother": {
        "inspiration": "MU/TH/UR 6000 — Alien",
        "blurb": "green phosphor terminal, dense text, scanlines",
        "shape": "rain",
        "ansi": (22, 34, 46),
        "hex": {"bg": "#020a02", "primary": "#33ff33", "secondary": "#1a801a",
                "accent": "#ccffcc", "text": "#a9f5a9"},
        "jitter": 0.0, "pulse_bias": -0.2,
    },
    "matrix": {
        "inspiration": "The Matrix",
        "blurb": "digital rain of memory glyphs",
        "shape": "rain",
        "ansi": (22, 40, 82),
        "hex": {"bg": "#000400", "primary": "#00ff41", "secondary": "#008f11",
                "accent": "#d0ffd8", "text": "#c5f7cf"},
        "jitter": 0.1, "pulse_bias": 0.25,
    },
    "tars": {
        "inspiration": "TARS — Interstellar",
        "blurb": "monolithic shifting blocks, honesty setting 90%",
        "shape": "blocks",
        "ansi": (240, 250, 255),
        "hex": {"bg": "#0a0c0e", "primary": "#dee2e6", "secondary": "#868e96",
                "accent": "#74c0fc", "text": "#e9ecef"},
        "jitter": 0.05, "pulse_bias": 0.0,
    },
}

_DEFAULT_ARCHETYPE = "jarvis"

# self-model color_theme → closest archetype, for derive_default()
_THEME_ARCHETYPE_HINT = {
    "cyan": "jarvis", "blue": "jarvis", "red": "hal", "gold": "glados",
    "magenta": "samantha", "green": "matrix",
}
_MOOD_WEATHER = {
    "anxious": "storm", "stressed": "storm", "chaotic": "storm",
    "calm": "clear", "serene": "clear",
    "curious": "drift", "playful": "drift",
    "creative": "aurora", "excited": "aurora", "focused": "aurora",
}


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _enum(v, allowed, default):
    return v if isinstance(v, str) and v in allowed else default


def _text(v, max_len, default=""):
    if not isinstance(v, str):
        return default
    return " ".join(v.split())[:max_len]


def validate_spec(raw: dict) -> dict:
    """Clamp an untrusted (LLM-written) spec into a fully valid one.

    Every field falls back independently, so partial output still renders.
    """
    if not isinstance(raw, dict):
        raw = {}

    arch_raw = raw.get("archetype")
    if not isinstance(arch_raw, dict):
        arch_raw = {"primary": arch_raw} if isinstance(arch_raw, str) else {}
    primary = _enum(arch_raw.get("primary"), ARCHETYPES, _DEFAULT_ARCHETYPE)
    secondary = arch_raw.get("secondary")
    secondary = secondary if (isinstance(secondary, str) and secondary in ARCHETYPES
                              and secondary != primary) else None
    archetype = {"primary": primary, "secondary": secondary,
                 "mix": _clamp(arch_raw.get("mix"), 0.0, 1.0, 0.0) if secondary else 0.0}

    preset = ARCHETYPES[primary]

    core_raw = raw.get("core") if isinstance(raw.get("core"), dict) else {}
    core = {
        "shape": _enum(core_raw.get("shape"), CORE_SHAPES, preset["shape"]),
        "radius": _clamp(core_raw.get("radius"), 0.15, 0.48, 0.35),
        "density": _clamp(core_raw.get("density"), 0.2, 1.0, 0.7),
        "asymmetry": _clamp(core_raw.get("asymmetry"), 0.0, 0.6, 0.15),
    }

    orb_raw = raw.get("orbiters")
    by_engine = {}
    if isinstance(orb_raw, list):
        for o in orb_raw:
            if isinstance(o, dict) and o.get("engine") in ENGINES:
                by_engine[o["engine"]] = _clamp(o.get("activity"), 0.05, 1.0, 0.5)
    orbiters = [{"engine": e, "activity": by_engine.get(e, 0.5)} for e in ENGINES]

    pulse_raw = raw.get("pulse") if isinstance(raw.get("pulse"), dict) else {}
    particles_raw = raw.get("particles") if isinstance(raw.get("particles"), dict) else {}

    return {
        "version": 1,
        "generated_at": _text(raw.get("generated_at"), 40) or
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "evolution_count": int(_clamp(raw.get("evolution_count"), 0, 10_000_000, 0)),
        "archetype": archetype,
        "core": core,
        "rings": int(_clamp(raw.get("rings"), 0, 12, 3)),
        "orbiters": orbiters,
        "pulse": {
            "base_hz": _clamp(pulse_raw.get("base_hz"), 0.05, 2.0, 0.5),
            "gpu_gain": _clamp(pulse_raw.get("gpu_gain"), 0.0, 3.0, 1.5),
        },
        "weather": _enum(raw.get("weather"), WEATHERS, "drift"),
        "particles": {
            "count": int(_clamp(particles_raw.get("count"), 0, 120, 40)),
            "style": _enum(particles_raw.get("style"), PARTICLE_STYLES, "orbit"),
        },
        "motto": _text(raw.get("motto"), 60, "mind · memory"),
        "artist_statement": _text(raw.get("artist_statement"), 400),
    }


def derive_default(self_model: dict) -> dict:
    """Deterministic portrait from the self-model — always available, no LLM."""
    self_model = self_model or {}
    mood = str(self_model.get("mood", "curious")).lower()
    weather = next((w for m, w in _MOOD_WEATHER.items() if m in mood), "drift")
    theme = str(self_model.get("color_theme", "cyan")).lower()
    primary = _THEME_ARCHETYPE_HINT.get(theme, _DEFAULT_ARCHETYPE)
    evo = int(self_model.get("evolution_count", 0) or 0)
    trend = str(self_model.get("eval_trend", "")).lower()
    base_hz = 0.8 if "improving" in trend or "rising" in trend else 0.5

    return validate_spec({
        "evolution_count": evo,
        "archetype": {"primary": primary},
        "core": {"shape": "cell"},   # Andre: the portrait must feel alive, organic
        "rings": evo // 100,
        "pulse": {"base_hz": base_hz, "gpu_gain": 1.5},
        "weather": weather,
        "motto": _text(str(self_model.get("focus_topic", "")), 60) or "mind · memory",
        "artist_statement": "Default portrait derived from my self-model — "
                            "I have not yet drawn myself.",
    })


def blended_palette(spec: dict) -> dict:
    """Resolve the spec's archetype (incl. blend) to concrete colors.

    Returns {"ansi": (dim, mid, bright), "hex": {...}, "primary": key,
    "secondary": key|None, "mix": float}. ANSI colors come from whichever
    archetype dominates the mix; hex blending is left to the web renderer.
    """
    arch = spec["archetype"]
    a, b, mix = arch["primary"], arch.get("secondary"), arch.get("mix", 0.0)
    lead = b if (b and mix > 0.5) else a
    return {
        "ansi": ARCHETYPES[lead]["ansi"],
        "hex": ARCHETYPES[lead]["hex"],
        "primary": a, "secondary": b, "mix": mix,
        "jitter": ARCHETYPES[lead]["jitter"],
        "pulse_bias": ARCHETYPES[lead]["pulse_bias"],
    }


PHASE_ORDER = ["newborn", "emerging", "developing", "mature"]  # mirrors bin/ginja


def maturity_from_phase(phase: str) -> float:
    """0.0 (newborn) … 1.0 (mature) — how far the portrait may evolve."""
    try:
        return PHASE_ORDER.index(str(phase)) / (len(PHASE_ORDER) - 1)
    except ValueError:
        return 1.0


def load_self_model() -> dict:
    try:
        return json.loads(SELF_MODEL_FILE.read_text())
    except Exception:
        return {}


def load_spec(path: Path = None) -> dict:
    """Load portrait.json, validated; fall back to derive_default(self-model)."""
    path = path or PORTRAIT_FILE
    try:
        return validate_spec(json.loads(path.read_text()))
    except Exception:
        return derive_default(load_self_model())


def save_spec(spec: dict, path: Path = None) -> dict:
    """Validate and atomically write a spec; returns the validated form."""
    path = path or PORTRAIT_FILE
    spec = validate_spec(spec)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(spec, indent=2) + "\n")
    tmp.replace(path)
    return spec


def archetype_menu() -> str:
    """Human/LLM-readable menu of archetypes for the respec prompt."""
    return "\n".join(
        f'- "{key}": {a["inspiration"]} — {a["blurb"]}' for key, a in ARCHETYPES.items()
    )
