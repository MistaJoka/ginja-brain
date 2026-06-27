#!/bin/bash
# Nightly auto-learn: ingest new shell history and git commits into ginja brain
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"

GINJA="$HOME/bin/ginja"
LOG="$HOME/.ginja/ingest-history.log"
CFG="$HOME/.ginja/config.json"
OLLAMA_URL=$(python3 -c "import json; print(json.load(open('$CFG')).get('ollama_url','http://localhost:11434'))" 2>/dev/null || echo "http://localhost:11434")
FAST_MODEL=$(python3 -c "import json; print(json.load(open('$CFG')).get('fast_model','llama3.2:3b'))" 2>/dev/null || echo "llama3.2:3b")

echo "[$(date)] Starting nightly ingest" >> "$LOG"

# ── Shell history — batch + summarize, skip noise ─────────────────────────────
NOISE_PATTERN="^(ls|ll|la|cd|clear|pwd|exit|history|cat |echo |man |which |ping |sleep |fg |bg |jobs |top |htop |btop|ginja watch)"
HISTORY_FILE="$HOME/.bash_history"
LAST_HISTORY_FILE="$HOME/.ginja/.last-history-line"

if [ -f "$HISTORY_FILE" ]; then
    LAST=$(cat "$LAST_HISTORY_FILE" 2>/dev/null || echo 0)
    CURRENT=$(wc -l < "$HISTORY_FILE")
    if [ "$CURRENT" -gt "$LAST" ]; then
        NEW_CMDS=$(tail -n +"$((LAST + 1))" "$HISTORY_FILE" \
            | grep -v "^#" \
            | grep -v -E "$NOISE_PATTERN" \
            | grep -v "^$" \
            | head -60)

        if [ -n "$NEW_CMDS" ]; then
            CMD_COUNT=$(echo "$NEW_CMDS" | wc -l)
            echo "[$(date)] Summarizing $CMD_COUNT meaningful commands" >> "$LOG"

            SUMMARY=$(curl -s "$OLLAMA_URL/api/generate" \
                -d "{\"model\":\"$FAST_MODEL\",\"prompt\":\"Summarize in 2-3 sentences what a Linux homelab developer was working on, based on these shell commands. Be specific and factual, mention actual tools and actions.\n\nCommands:\n$NEW_CMDS\",\"stream\":false}" \
                2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('response',''))" 2>/dev/null)

            if [ -n "$SUMMARY" ]; then
                DATE=$(date '+%Y-%m-%d')
                "$GINJA" remember "Shell activity $DATE: $SUMMARY" 2>>"$LOG" \
                    && echo "[$(date)] ✓ Shell summary stored" >> "$LOG"
            fi
        fi
        echo "$CURRENT" > "$LAST_HISTORY_FILE"
    fi
fi

# ── Git commits — local repos ──────────────────────────────────────────────────
LAST_GIT_FILE="$HOME/.ginja/.last-git-ingest"
SINCE=$(cat "$LAST_GIT_FILE" 2>/dev/null || echo "1970-01-01")

find "$HOME" -maxdepth 4 -name ".git" -type d 2>/dev/null | while read -r gitdir; do
    repo=$(dirname "$gitdir")
    commits=$(git -C "$repo" log --after="$SINCE" --format="%s" 2>/dev/null | head -10)
    if [ -n "$commits" ]; then
        repo_name=$(basename "$repo")
        "$GINJA" remember "Recent git commits in $repo_name: $commits" 2>>"$LOG"
    fi
done

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LAST_GIT_FILE"

# ── Gitea API — commits across all repos ──────────────────────────────────────
GITEA_URL="http://localhost:3000"
GITEA_TOKEN=$(cat "$HOME/.ginja/.gitea-token" 2>/dev/null || echo "")

if [ -n "$GITEA_TOKEN" ]; then
    echo "[$(date)] Ingesting Gitea activity" >> "$LOG"
    curl -s -H "Authorization: token $GITEA_TOKEN" \
        "$GITEA_URL/api/v1/repos/search?limit=20" 2>/dev/null \
    | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('data', []):
    print(r['full_name'])
" 2>/dev/null | while read -r repo; do
        recent=$(curl -s -H "Authorization: token $GITEA_TOKEN" \
            "$GITEA_URL/api/v1/repos/$repo/commits?limit=5" 2>/dev/null \
            | python3 -c "
import sys, json
commits = json.load(sys.stdin)
if isinstance(commits, list):
    for c in commits:
        msg = c.get('commit',{}).get('message','').split('\n')[0][:80]
        author = c.get('commit',{}).get('author',{}).get('name','?')
        print(f'{author}: {msg}')
" 2>/dev/null)
        if [ -n "$recent" ]; then
            "$GINJA" remember "Gitea activity in $repo: $recent" 2>>"$LOG"
        fi
    done
else
    echo "[$(date)] No Gitea token found — save one to ~/.ginja/.gitea-token to enable Gitea ingestion" >> "$LOG"
fi

# ── Andre-model — infer Andre's current focus from shell + git activity ───────
ANDRE_MODEL="$HOME/.ginja/andre-model.json"
RECENT_CMDS=$(tail -n 80 "$HOME/.bash_history" 2>/dev/null \
    | grep -v "^#" \
    | grep -v -E "$NOISE_PATTERN" \
    | grep -v "^$" \
    | tail -40)
RECENT_COMMITS=$(find "$HOME" -maxdepth 4 -name ".git" -type d 2>/dev/null \
    | while read -r gitdir; do
        git -C "$(dirname "$gitdir")" log --after="2 days ago" --format="%s" 2>/dev/null | head -5
    done | head -20)
COMBINED_ACTIVITY="Shell commands:\n$RECENT_CMDS\n\nRecent git commits:\n$RECENT_COMMITS"

if [ -n "$RECENT_CMDS" ] || [ -n "$RECENT_COMMITS" ]; then
    ANDRE_JSON=$(curl -s "$OLLAMA_URL/api/generate" \
        -d "{\"model\":\"$FAST_MODEL\",\"prompt\":\"Based on a developer's recent shell commands and git commits, infer their current context. Return ONLY valid JSON with exactly these fields: {\\\"current_project\\\": \\\"main project or area being worked on\\\", \\\"current_focus_area\\\": \\\"specific technical area\\\", \\\"recent_concerns\\\": [\\\"thing 1\\\", \\\"thing 2\\\"], \\\"energy_signal\\\": \\\"active|stressed|routine\\\", \\\"last_asks\\\": []}. Be specific and factual. If unclear, use 'routine' for energy_signal.\n\nActivity:\n$COMBINED_ACTIVITY\",\"stream\":false}" \
        2>/dev/null | python3 -c "
import sys, json
resp = json.load(sys.stdin).get('response', '')
start = resp.find('{')
end = resp.rfind('}') + 1
if start >= 0 and end > start:
    try:
        d = json.loads(resp[start:end])
        d['last_updated'] = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')
        print(json.dumps(d, indent=2))
    except Exception:
        pass
" 2>/dev/null)

    if [ -n "$ANDRE_JSON" ]; then
        echo "$ANDRE_JSON" > "$ANDRE_MODEL"
        echo "[$(date)] ✓ Andre-model updated" >> "$LOG"
    fi
fi

echo "[$(date)] Nightly ingest complete" >> "$LOG"
