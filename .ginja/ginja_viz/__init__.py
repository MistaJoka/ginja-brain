"""ginja_viz — the brain's dashboard: live, factual, data-driven views only.

This package lives OUTSIDE /home/ginja/bin/ginja on purpose: the evolution
loop rewrites `_make_watch_layout` inside that script, and nothing here may
ever be inside that blast radius. Everything served is a measurement over
state the brain already writes; nothing here is decorative.
"""

__version__ = "0.2.0"
