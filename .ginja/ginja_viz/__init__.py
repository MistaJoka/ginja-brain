"""ginja_viz — deterministic renderers for ginja-brain's self-portrait.

This package lives OUTSIDE /home/ginja/bin/ginja on purpose: the evolution
loop rewrites `_make_watch_layout` inside that script, and nothing here may
ever be inside that blast radius. The LLM authors portrait.json (validated
in spec.py); the code in this package only ever renders it.
"""

__version__ = "0.1.0"
