#!/usr/bin/env bash
# auto-evolve.sh — Unattended evolution loop for ginja-brain.
# No human approval needed — each cycle may auto-patch ginja itself.
#
# Installed to ~/.ginja/auto-evolve.sh by setup.sh
# Run:   ~/.ginja/auto-evolve.sh [interval_hours]   (default: 6h)
# Test:  INTERVAL_SECS=5 ~/.ginja/auto-evolve.sh

GINJA="${HOME}/bin/ginja"
LOG="${HOME}/.ginja/evolve-auto.log"
INTERVAL_SECS="${INTERVAL_SECS:-$(( ${1:-6} * 3600 ))}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

CYCLE=0
while true; do
    CYCLE=$(( CYCLE + 1 ))
    log "=== Cycle ${CYCLE} starting ==="

    if (( CYCLE % 3 == 0 )); then
        log "mode: learn (outward)"
        "$GINJA" evolve --learn >> "$LOG" 2>&1
    else
        log "mode: growth + auto-patch"
        "$GINJA" evolve >> "$LOG" 2>&1
    fi

    if (( CYCLE % 4 == 0 )); then
        log "reflect: storing self-assessment"
        "$GINJA" reflect --store >> "$LOG" 2>&1
    fi

    log "=== Cycle ${CYCLE} done, sleeping ${INTERVAL_SECS}s ==="
    sleep "$INTERVAL_SECS"
done
