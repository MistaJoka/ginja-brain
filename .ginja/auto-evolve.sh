#!/usr/bin/env bash
# Autonomous evolution loop — runs for DURATION_HOURS, then exits.
# Each cycle: ginja evolve → ginja approve --auto --code-cap 1
# Log: ~/.ginja/auto-evolve.log

GINJA="$HOME/bin/ginja"
LOG="$HOME/.ginja/auto-evolve.log"
DURATION_HOURS="${1:-4}"
INTERVAL_MIN="${2:-25}"

DEADLINE=$(( $(date +%s) + DURATION_HOURS * 3600 ))
CYCLE=0

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== Auto-evolution started (${DURATION_HOURS}h, every ${INTERVAL_MIN}min, code-cap 1) ==="

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    CYCLE=$(( CYCLE + 1 ))
    REMAINING_MIN=$(( ( DEADLINE - $(date +%s) ) / 60 ))
    log "--- Cycle #${CYCLE} (${REMAINING_MIN}min remaining) ---"

    log "evolve: starting"
    if (( CYCLE % 3 == 0 )); then
        log "evolve: outward learning mode (cycle $CYCLE)"
        if "$GINJA" evolve --learn >> "$LOG" 2>&1; then
            log "evolve: done"
        else
            log "evolve: FAILED (exit $?)"
        fi
    else
        if "$GINJA" evolve >> "$LOG" 2>&1; then
            log "evolve: done"
        else
            log "evolve: FAILED (exit $?)"
        fi
    fi

    if (( CYCLE % 4 == 0 )); then
        log "reflect: storing self-assessment"
        "$GINJA" reflect --store >> "$LOG" 2>&1
    fi

    # ── Self-eval (every 5th cycle) ───────────────────────────────────────────
    if (( CYCLE % 5 == 0 )); then
        log "eval: scoring evolution quality (cycle $CYCLE)"
        "$GINJA" eval >> "$LOG" 2>&1 \
            && log "eval: done" || log "eval: FAILED (non-fatal)"
    fi

    log "approve: starting (auto, code-cap 1)"
    if "$GINJA" approve --auto --code-cap 1 >> "$LOG" 2>&1; then
        log "approve: done"
    else
        log "approve: FAILED (exit $?)"
    fi

    # Check if there's enough time for another cycle before sleeping
    NOW=$(date +%s)
    SLEEP_SECS=$(( INTERVAL_MIN * 60 ))
    if [ $(( NOW + SLEEP_SECS )) -ge "$DEADLINE" ]; then
        log "Not enough time for another full cycle — stopping."
        break
    fi

    log "sleeping ${INTERVAL_MIN}min…"
    sleep "${SLEEP_SECS}"
done

log "=== Auto-evolution complete after ${CYCLE} cycle(s) ==="
