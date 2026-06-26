#!/bin/bash
# Homelab health diary — runs daily, stores a digest of system state into ginja memory.
# Gives the brain real operational context: what's running, what changed, what's stressed.
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"

GINJA="$HOME/bin/ginja"
LOG="$HOME/.ginja/homelab-diary.log"
CFG="$HOME/.ginja/config.json"
OLLAMA_URL=$(python3 -c "import json; print(json.load(open('$CFG')).get('ollama_url','http://localhost:11434'))" 2>/dev/null || echo "http://localhost:11434")
FAST_MODEL=$(python3 -c "import json; print(json.load(open('$CFG')).get('fast_model','llama3.2:3b'))" 2>/dev/null || echo "llama3.2:3b")

echo "[$(date)] Starting homelab diary" >> "$LOG"

# ── Docker containers ──────────────────────────────────────────────────────────
DOCKER_STATUS=$(docker ps --format "{{.Names}}: {{.Status}}" 2>/dev/null || echo "docker unavailable")
DOCKER_STOPPED=$(docker ps -a --filter "status=exited" --format "{{.Names}}" 2>/dev/null | tr '\n' ', ')

# ── Disk usage ─────────────────────────────────────────────────────────────────
DISK_ROOT=$(df -h / | awk 'NR==2 {print $3"/"$2" ("$5" used)"}')
DISK_BRAIN=$(df -h /mnt/brain 2>/dev/null | awk 'NR==2 {print $3"/"$2" ("$5" used)"}' || echo "not mounted")

# ── GPU ────────────────────────────────────────────────────────────────────────
GPU_STATUS=$(nvidia-smi --query-gpu=name,memory.used,memory.total,temperature.gpu \
    --format=csv,noheader,nounits 2>/dev/null \
    | awk -F, '{printf "%s — VRAM %s/%s MiB — %s°C", $1, $2, $3, $4}' || echo "no GPU data")

# ── Memory ─────────────────────────────────────────────────────────────────────
MEM_STATUS=$(free -h | awk 'NR==2 {print $3"/"$2" used"}')

# ── Qdrant vector counts ────────────────────────────────────────────────────────
QDRANT_STATUS=$(python3 -c "
from qdrant_client import QdrantClient
c = QdrantClient('http://localhost:6333')
parts = []
for col in c.get_collections().collections:
    parts.append(f'{col.name}: {c.count(col.name).count}')
print(', '.join(parts))
" 2>/dev/null || echo "Qdrant unavailable")

# ── Woodpecker CI recent builds ────────────────────────────────────────────────
WP_STATUS=$(curl -s http://localhost:8000/api/repos \
    -H "Authorization: Bearer $(cat $HOME/.ginja/.woodpecker-token 2>/dev/null)" \
    2>/dev/null | python3 -c "
import sys, json
repos = json.load(sys.stdin)
if isinstance(repos, list):
    for r in repos[:5]:
        print(f'{r.get(\"full_name\",\"?\")} — last build: {r.get(\"last_build_status\",\"?\")}')
" 2>/dev/null || echo "")

# ── Assemble raw status ────────────────────────────────────────────────────────
RAW="Date: $(date '+%Y-%m-%d %H:%M')
Docker running: $DOCKER_STATUS
Docker stopped: ${DOCKER_STOPPED:-none}
Disk /: $DISK_ROOT
Disk /mnt/brain: $DISK_BRAIN
RAM: $MEM_STATUS
GPU: $GPU_STATUS
Qdrant: $QDRANT_STATUS${WP_STATUS:+
CI builds: $WP_STATUS}"

# ── Ask LLM to write a meaningful diary entry ──────────────────────────────────
DIARY=$(curl -s "$OLLAMA_URL/api/generate" \
    -d "{\"model\":\"$FAST_MODEL\",\"prompt\":\"Write a 3-sentence homelab diary entry based on this system status. Note anything unusual, stressed, or worth remembering. Be factual and specific.\n\nStatus:\n$RAW\",\"stream\":false}" \
    2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('response',''))" 2>/dev/null)

if [ -n "$DIARY" ]; then
    DATE=$(date '+%Y-%m-%d')
    "$GINJA" remember "Homelab diary $DATE: $DIARY" 2>>"$LOG" \
        && echo "[$(date)] ✓ Diary entry stored" >> "$LOG"
else
    # Fallback: store raw status if LLM unavailable
    "$GINJA" remember "Homelab status $(date '+%Y-%m-%d'): $RAW" 2>>"$LOG"
    echo "[$(date)] ✓ Raw status stored (LLM unavailable)" >> "$LOG"
fi

echo "[$(date)] Homelab diary complete" >> "$LOG"
