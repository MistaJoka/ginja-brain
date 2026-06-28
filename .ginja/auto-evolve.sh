#!/usr/bin/env bash
# Autonomous evolution loop — hardened edition
# Usage: auto-evolve.sh [DURATION_HOURS] [INTERVAL_MIN]
#
# Safety layers (in order):
#   1. Lock file      — prevents concurrent sessions from clobbering each other
#   2. Load check     — skips cycles when system is already stressed (load >= 5)
#   3. Pre-backup     — snapshots bin/ginja before every approve
#   4. Change-size    — rolls back if delta > 80 lines (catastrophic rewrite guard)
#   5. Syntax check   — rolls back on any Python parse error
#   6. Runtime check  — rolls back if ginja --help fails (catches import errors)
#   7. Streak abort   — pauses and alerts if 3 consecutive rollbacks occur

export PATH="$HOME/.local/bin:$HOME/bin:$PATH"

GINJA="$HOME/bin/ginja"
GINJA_DIR="$HOME/.ginja"
LOG="$GINJA_DIR/auto-evolve.log"
LOCKFILE="$GINJA_DIR/auto-evolve.lock"
BACKUP_DIR="$HOME/bin"
DURATION_HOURS="${1:-0}"
INTERVAL_MIN="${2:-30}"
MAX_ROLLBACK_STREAK=3

# 0 = run forever; any other value = stop after that many hours
if [ "${DURATION_HOURS}" = "0" ]; then
    DEADLINE=9999999999
else
    DEADLINE=$(( $(date +%s) + DURATION_HOURS * 3600 ))
fi
CYCLE=0
ROLLBACK_STREAK=0

# ── 1. Lock file ──────────────────────────────────────────────────────────────
if [ -f "$LOCKFILE" ]; then
    HELD_PID=$(cat "$LOCKFILE" 2>/dev/null)
    if [ -n "$HELD_PID" ] && kill -0 "$HELD_PID" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Lock held by PID $HELD_PID — exiting" >> "$LOG"
        exit 0
    fi
    rm -f "$LOCKFILE"
fi
echo $$ > "$LOCKFILE"
trap "rm -f '$LOCKFILE'" EXIT INT TERM

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

WINDOW_DESC="continuous" && [ "${DURATION_HOURS}" != "0" ] && WINDOW_DESC="${DURATION_HOURS}h window"
log "=== Auto-evolution started (${WINDOW_DESC}, ${INTERVAL_MIN}min interval, code-cap 1) ==="

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    CYCLE=$(( CYCLE + 1 ))
    REMAINING_MIN=$(( ( DEADLINE - $(date +%s) ) / 60 ))
    log "--- Cycle #${CYCLE} (${REMAINING_MIN}min remaining) ---"

    # ── 2. Load check ─────────────────────────────────────────────────────────
    LOAD=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo "0.0")
    LOAD_INT=$(printf "%.0f" "$LOAD" 2>/dev/null || echo "0")
    if [ "${LOAD_INT:-0}" -ge 5 ]; then
        log "High load ($LOAD) — skipping cycle, sleeping 5min"
        sleep 300
        continue
    fi

    # ── Evolve (inward or outward) ────────────────────────────────────────────
    if (( CYCLE % 3 == 0 )); then
        log "evolve: outward learning (--learn)"
        "$GINJA" evolve --learn >> "$LOG" 2>&1 \
            && log "evolve: done" || log "evolve: FAILED (non-fatal)"
    else
        log "evolve: inward"
        "$GINJA" evolve >> "$LOG" 2>&1 \
            && log "evolve: done" || log "evolve: FAILED (non-fatal)"
    fi

    if (( CYCLE % 4 == 0 )); then
        log "reflect: storing self-assessment"
        "$GINJA" reflect --store >> "$LOG" 2>&1
    fi

    # ── Memory consolidation (every 6th cycle — compress episodic → semantic) ──
    if (( CYCLE % 6 == 0 )); then
        log "consolidate: compressing episodic perception logs"
        "$GINJA" consolidate >> "$LOG" 2>&1 \
            && log "consolidate: done" || log "consolidate: FAILED (non-fatal)"
    fi

    # ── Visual self-review (every 7th cycle — ginja critiques its own watch display) ──
    if (( CYCLE % 7 == 0 )); then
        log "suggest-visuals: brain reviewing and auto-implementing watch improvements"
        "$GINJA" suggest-visuals --auto >> "$LOG" 2>&1 \
            && log "suggest-visuals: done" || log "suggest-visuals: FAILED (non-fatal)"
    fi

    # ── Self-eval (every 5th cycle) ───────────────────────────────────────────
    if (( CYCLE % 5 == 0 )); then
        log "eval: scoring evolution quality (cycle $CYCLE)"
        "$GINJA" eval >> "$LOG" 2>&1 \
            && log "eval: done" || log "eval: FAILED (non-fatal)"
    fi

    # ── 3. Pre-approve backup ─────────────────────────────────────────────────
    BACKUP="$BACKUP_DIR/ginja.bak.$(date +%s)"
    LINES_BEFORE=$(wc -l < "$GINJA")
    cp "$GINJA" "$BACKUP"

    log "approve: starting (auto, code-cap 1)"
    "$GINJA" approve --auto --code-cap 1 >> "$LOG" 2>&1 \
        && log "approve: done" || log "approve: finished (may have had no implementations)"

    # ── 4-6. Safety checks ────────────────────────────────────────────────────
    SAFE=true
    REASON=""

    LINES_AFTER=$(wc -l < "$GINJA")
    LINE_DELTA=$(( LINES_AFTER - LINES_BEFORE ))
    LINE_DELTA_ABS=${LINE_DELTA#-}

    if [ "${LINE_DELTA_ABS:-0}" -gt 80 ]; then
        SAFE=false
        REASON="large change (${LINE_DELTA} lines — limit is ±80)"
    elif ! python3 -c "import ast; ast.parse(open('$GINJA').read())" 2>/dev/null; then
        SAFE=false
        REASON="Python syntax error"
    elif ! python3 "$GINJA" --help >/dev/null 2>&1; then
        SAFE=false
        REASON="runtime import failure"
    fi

    if [ "$SAFE" = "false" ]; then
        log "ROLLBACK — $REASON"
        cp "$BACKUP" "$GINJA"
        log "Restored from $BACKUP"
        ROLLBACK_STREAK=$(( ROLLBACK_STREAK + 1 ))

        # ── 7. Streak abort ───────────────────────────────────────────────────
        if [ "$ROLLBACK_STREAK" -ge "$MAX_ROLLBACK_STREAK" ]; then
            log "=== $MAX_ROLLBACK_STREAK consecutive rollbacks — pausing evolution to prevent damage ==="
            log "=== Run 'ginja approve --list' to inspect suggestions, or clear with 'ginja approve --clear' ==="
            break
        fi
    else
        log "Safety OK — delta ${LINE_DELTA} lines, syntax clean, runtime clean"
        ROLLBACK_STREAK=0
    fi

    # ── Rotate backups (keep last 7) ─────────────────────────────────────────
    ls -t "$BACKUP_DIR/ginja.bak."* 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null

    # ── Sleep if time remains ─────────────────────────────────────────────────
    NOW=$(date +%s)
    SLEEP_SECS=$(( INTERVAL_MIN * 60 ))
    if [ $(( NOW + SLEEP_SECS )) -ge "$DEADLINE" ]; then
        log "Not enough time for another full cycle — stopping"
        break
    fi

    log "sleeping ${INTERVAL_MIN}min…"
    sleep "${SLEEP_SECS}"
done

log "=== Complete after ${CYCLE} cycle(s) — ${ROLLBACK_STREAK} consecutive rollbacks at exit ==="
