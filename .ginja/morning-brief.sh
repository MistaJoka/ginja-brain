#!/bin/bash
# ginja morning brief — writes today.md, a proactive daily digest from ginja to Andre
# Run daily via cron: 0 8 * * * /home/ginja/.ginja/morning-brief.sh
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"

GINJA="$HOME/bin/ginja"
GINJA_DIR="$HOME/.ginja"
CFG="$GINJA_DIR/config.json"
TODAY_FILE="$GINJA_DIR/today.md"
LOG="$GINJA_DIR/morning-brief.log"
ANDRE_MODEL="$GINJA_DIR/andre-model.json"
PERCEPTION_LOG="$GINJA_DIR/perception.log"

OLLAMA_URL=$(python3 -c "import json; print(json.load(open('$CFG')).get('ollama_url','http://localhost:11434'))" 2>/dev/null || echo "http://localhost:11434")
FAST_MODEL=$(python3 -c "import json; print(json.load(open('$CFG')).get('fast_model','llama3.2:3b'))" 2>/dev/null || echo "llama3.2:3b")
QUALITY_MODEL=$(python3 -c "import json; print(json.load(open('$CFG')).get('quality_model','qwen2.5:7b'))" 2>/dev/null || echo "qwen2.5:7b")

GEMINI_KEY=$(python3 -c "import json; print(json.load(open('$CFG')).get('gemini_api_key',''))" 2>/dev/null || echo "")
GEMINI_MODEL=$(python3 -c "import json; print(json.load(open('$CFG')).get('gemini_model','gemini-2.5-flash'))" 2>/dev/null || echo "gemini-2.5-flash")

echo "[$(date)] Starting morning brief" >> "$LOG"

DATE=$(date '+%Y-%m-%d')
WEEKDAY=$(date '+%A')
HOUR=$(date '+%H')

# ── 1. Homelab health snapshot ─────────────────────────────────────────────────
LAST_PERCEPTION=$(tail -1 "$PERCEPTION_LOG" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('summary',''))" 2>/dev/null || echo "")
DOCKER_STATUS=$(docker ps --format "{{.Names}}" 2>/dev/null | wc -l | tr -d ' ')
DOCKER_STOPPED=$(docker ps -a --format "{{.Names}}" 2>/dev/null | wc -l | tr -d ' ')
DISK_ROOT=$(df -h / 2>/dev/null | awk 'NR==2{print $3"/"$4" ("$5" used)"}' || echo "unknown")
DISK_BRAIN=$(df -h /mnt/brain 2>/dev/null | awk 'NR==2{print $3"/"$4" ("$5" used)"}' || echo "unknown")
GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null || echo "?")
LOAD=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo "?")

HEALTH_SUMMARY="${DOCKER_STATUS}/${DOCKER_STOPPED} containers up  ·  / disk: ${DISK_ROOT}  ·  /mnt/brain: ${DISK_BRAIN}  ·  load: ${LOAD}  ·  GPU: ${GPU_TEMP}°C"

# ── 2. Recent memory — top overnight learning ──────────────────────────────────
RECENT_MEM=$(python3 - << 'PYEOF'
import sys, json, datetime
from pathlib import Path
ginja_dir = Path.home() / ".ginja"
cfg_file = ginja_dir / "config.json"
try:
    cfg = json.loads(cfg_file.read_text())
    from qdrant_client import QdrantClient
    client = QdrantClient(url=cfg.get("qdrant_url", "http://localhost:6333"))
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=16)).isoformat()
    results, _ = client.scroll(
        collection_name="memories",
        scroll_filter=None,
        limit=50,
        with_payload=True,
        with_vectors=False,
    )
    recent = [
        r.payload.get("text", "")
        for r in results
        if r.payload.get("created", "") >= cutoff
        and r.payload.get("source", "") not in ("evolution", "evolve_research")
    ]
    if recent:
        print(recent[0][:200])
    else:
        print("")
except Exception as e:
    print("")
PYEOF
)

# ── 3. Last evolution reflection ───────────────────────────────────────────────
LAST_EVO=$(tail -n 60 "$GINJA_DIR/evolution.log" 2>/dev/null | grep -A5 "Reflection:" | grep -v "Reflection:" | head -2 | tr '\n' ' ' | cut -c1-180 || echo "")

# ── 4. Andre's current focus ───────────────────────────────────────────────────
ANDRE_PROJECT=$(python3 -c "import json; d=json.load(open('$ANDRE_MODEL')); print(d.get('current_project',''))" 2>/dev/null || echo "")
ANDRE_FOCUS=$(python3 -c "import json; d=json.load(open('$ANDRE_MODEL')); print(d.get('current_focus_area',''))" 2>/dev/null || echo "")
ANDRE_ENERGY=$(python3 -c "import json; d=json.load(open('$ANDRE_MODEL')); print(d.get('energy_signal','routine'))" 2>/dev/null || echo "routine")

# ── 5. Evolution count ─────────────────────────────────────────────────────────
EVO_COUNT=$(python3 -c "import json; m=json.load(open('$GINJA_DIR/self-model.json')); print(m.get('evolution_count',0))" 2>/dev/null || echo "?")
PHASE=$(python3 -c "import json; m=json.load(open('$GINJA_DIR/self-model.json')); print(m.get('phase','?'))" 2>/dev/null || echo "?")
MOOD=$(python3 -c "import json; m=json.load(open('$GINJA_DIR/self-model.json')); print(m.get('mood','?'))" 2>/dev/null || echo "?")
FOCUS=$(python3 -c "import json; m=json.load(open('$GINJA_DIR/self-model.json')); print(m.get('focus_topic','?'))" 2>/dev/null || echo "?")

# ── 6. Oracle — brief generation ──────────────────────────────────────────────
BRIEF_PROMPT="You are ginja-brain, an AI that lives in Andre's homelab and knows him well.

Today is $WEEKDAY, $DATE. It is $HOUR:00.

GINJA'S STATE:
- Evolution cycle: #$EVO_COUNT ($PHASE phase, mood: $MOOD)
- Current focus: $FOCUS
- Last reflection: $LAST_EVO

HOMELAB HEALTH:
$HEALTH_SUMMARY

ANDRE'S CONTEXT:
- Current project: ${ANDRE_PROJECT:-unknown}
- Current focus area: ${ANDRE_FOCUS:-unknown}
- Energy signal: $ANDRE_ENERGY

RECENT OVERNIGHT LEARNING:
${RECENT_MEM:-Nothing notable overnight.}

Write a short morning brief FROM ginja TO Andre. Address him directly. 4 sections:
1. **Health** — 1 sentence on the homelab state, flag anything worth noting
2. **Overnight** — 1-2 sentences: the most interesting thing ginja learned or noticed since yesterday
3. **Today** — 1-2 sentences: something ginja thinks Andre should know or consider today, related to his current project/focus
4. **Question** — 1 genuine question ginja has for Andre (not about tasks, but something ginja is curious about)

Be direct and honest. Not sycophantic. Ginja has its own perspective — use it."

# Try Gemini first, fall back to local
if [ -n "$GEMINI_KEY" ]; then
    BRIEF=$(python3 - << PYEOF
import json, urllib.request, urllib.error
key = "$GEMINI_KEY"
model = "$GEMINI_MODEL"
prompt = """$BRIEF_PROMPT"""
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    print(text.strip())
except Exception as e:
    print("")
PYEOF
)
fi

if [ -z "$BRIEF" ]; then
    BRIEF=$(curl -s "$OLLAMA_URL/api/generate" \
        -d "{\"model\":\"$QUALITY_MODEL\",\"prompt\":$(echo "$BRIEF_PROMPT" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))"),\"stream\":false}" \
        2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('response',''))" 2>/dev/null)
fi

if [ -z "$BRIEF" ]; then
    BRIEF="(Oracle unavailable — homelab health: $HEALTH_SUMMARY)"
fi

# ── 7. Write today.md ──────────────────────────────────────────────────────────
cat > "$TODAY_FILE" << MDEOF
# ginja morning brief — $WEEKDAY, $DATE

$BRIEF

---
*Evo #${EVO_COUNT} · ${PHASE} · ${MOOD} · focus: ${FOCUS}*
MDEOF

echo "[$(date)] Morning brief written to $TODAY_FILE" >> "$LOG"

# ── 8. Push notification via ntfy (if configured) ──────────────────────────────
NTFY_TOPIC=$(python3 -c "import json; print(json.load(open('$CFG')).get('ntfy_topic',''))" 2>/dev/null || echo "")
if [ -n "$NTFY_TOPIC" ]; then
    BRIEF_SUMMARY=$(head -6 "$TODAY_FILE" | tail -5 | tr '\n' ' ' | sed 's/  */ /g')
    curl -s \
        -H "Title: ginja brief — $WEEKDAY" \
        -H "Priority: low" \
        -H "Tags: robot" \
        -d "$BRIEF_SUMMARY" \
        "https://ntfy.sh/$NTFY_TOPIC" >> "$LOG" 2>&1 \
        && echo "[$(date)] ntfy push sent to $NTFY_TOPIC" >> "$LOG" \
        || echo "[$(date)] ntfy push failed" >> "$LOG"
fi

echo "[$(date)] Morning brief complete" >> "$LOG"
