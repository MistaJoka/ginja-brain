#!/bin/bash
# Homelab health alerts — checks docker, disk, GPU, RAM/swap, and core services;
# alerts Andre via the outbox (and ntfy if configured) when something is critical.
# Delivers operator goal: "homelab health alert script that pings docker, disk, and
# GPU and sends ntfy notification if anything is critical"
# Run via cron: */30 * * * * /home/ginja/.ginja/health-alert.sh
#
# Pure checks, no LLM. De-dupes: the same set of problems alerts at most once per 6h.

set -u
CFG="$HOME/.ginja/config.json"
LOG="$HOME/.ginja/health-alert.log"
STATE="$HOME/.ginja/.last-alert"
OUTBOX="/mnt/brain/outbox"
NTFY_TOPIC=$(python3 -c "import json; print(json.load(open('$CFG')).get('ntfy_topic',''))" 2>/dev/null || echo "")

PROBLEMS=()

# ── Docker: watched containers that have exited ────────────────────────────────
EXITED=$(docker ps -a --filter "status=exited" --format "{{.Names}}" 2>/dev/null | paste -sd, -)
[ -n "$EXITED" ] && PROBLEMS+=("Containers exited: $EXITED")

# ── Disk ───────────────────────────────────────────────────────────────────────
for MNT in / /mnt/brain; do
    PCT=$(df --output=pcent "$MNT" 2>/dev/null | tail -1 | tr -dc '0-9')
    [ -n "$PCT" ] && [ "$PCT" -gt 90 ] && PROBLEMS+=("Disk $MNT at ${PCT}%")
done

# ── GPU temperature ────────────────────────────────────────────────────────────
GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1)
[ -n "$GPU_TEMP" ] && [ "$GPU_TEMP" -gt 85 ] 2>/dev/null && PROBLEMS+=("GPU at ${GPU_TEMP}°C")

# ── Swap pressure (8GB box — heavy swap means something is wedged) ─────────────
SWAP_PCT=$(free | awk '/^Swap:/ { if ($2>0) printf "%d", $3*100/$2; else print 0 }')
[ "$SWAP_PCT" -gt 60 ] && PROBLEMS+=("Swap at ${SWAP_PCT}%")

# ── Core endpoints ─────────────────────────────────────────────────────────────
curl -sf --max-time 5 "http://localhost:6333/healthz" >/dev/null 2>&1 \
    || PROBLEMS+=("Qdrant not responding on :6333")
curl -sf --max-time 5 "http://localhost:11434/api/tags" >/dev/null 2>&1 \
    || PROBLEMS+=("Ollama not responding on :11434")

# ── ginja's own organs ─────────────────────────────────────────────────────────
for SVC in ginja-evolve ginja-inbox; do
    STATUS=$(systemctl --user is-active "$SVC.service" 2>/dev/null)
    [ "$STATUS" = "active" ] || PROBLEMS+=("$SVC.service is $STATUS")
done

# ── Nothing wrong → clear state and exit quietly ───────────────────────────────
if [ "${#PROBLEMS[@]}" -eq 0 ]; then
    rm -f "$STATE"
    exit 0
fi

# ── De-dupe: same problem set within 6h → stay quiet ───────────────────────────
FINGERPRINT=$(printf '%s\n' "${PROBLEMS[@]}" | md5sum | cut -d' ' -f1)
if [ -f "$STATE" ]; then
    read -r LAST_FP LAST_TS < "$STATE" || true
    NOW=$(date +%s)
    if [ "$FINGERPRINT" = "${LAST_FP:-}" ] && [ $(( NOW - ${LAST_TS:-0} )) -lt 21600 ]; then
        exit 0
    fi
fi
echo "$FINGERPRINT $(date +%s)" > "$STATE"

# ── Alert: outbox file + optional ntfy push ────────────────────────────────────
BODY=$(printf -- '- %s\n' "${PROBLEMS[@]}")
mkdir -p "$OUTBOX" 2>/dev/null
ALERT_FILE="$OUTBOX/ALERT-$(date +%Y%m%d-%H%M%S).md"
cat > "$ALERT_FILE" << EOF
# ⚠ homelab alert — $(date '+%A %H:%M')

$BODY

*— ginja health watch (checks every 30 min)*
EOF
echo "[$(date '+%Y-%m-%d %H:%M:%S')] ALERT: ${PROBLEMS[*]}" >> "$LOG"

if [ -n "$NTFY_TOPIC" ]; then
    curl -s \
        -H "Title: homelab alert" \
        -H "Priority: high" \
        -H "Tags: warning" \
        -d "$BODY" \
        "https://ntfy.sh/$NTFY_TOPIC" >/dev/null 2>&1
fi
