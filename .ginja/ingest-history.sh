#!/bin/bash
# Nightly auto-learn: ingest new shell history and git commits into ginja brain
export PATH="$HOME/.local/bin:$HOME/bin:$PATH"

GINJA="$HOME/bin/ginja"
LAST_HISTORY_FILE="$HOME/.ginja/.last-history-line"
LOG="$HOME/.ginja/ingest-history.log"

echo "[$(date)] Starting nightly ingest" >> "$LOG"

# ── Shell history ──────────────────────────────────────────────────────────────
HISTORY_FILE="$HOME/.bash_history"
if [ -f "$HISTORY_FILE" ]; then
    LAST=$(cat "$LAST_HISTORY_FILE" 2>/dev/null || echo 0)
    CURRENT=$(wc -l < "$HISTORY_FILE")
    if [ "$CURRENT" -gt "$LAST" ]; then
        NEW=$(( CURRENT - LAST ))
        echo "[$(date)] Ingesting $NEW new history lines" >> "$LOG"
        tail -n +"$((LAST + 1))" "$HISTORY_FILE" | grep -v "^#" | while IFS= read -r line; do
            [ -n "$line" ] && "$GINJA" remember "Shell command I ran: $line" 2>>"$LOG"
        done
        echo "$CURRENT" > "$LAST_HISTORY_FILE"
    fi
fi

# ── Git commits ────────────────────────────────────────────────────────────────
LAST_GIT_FILE="$HOME/.ginja/.last-git-ingest"
SINCE=$(cat "$LAST_GIT_FILE" 2>/dev/null || echo "1970-01-01")

find "$HOME" -maxdepth 4 -name ".git" -type d 2>/dev/null | while read -r gitdir; do
    repo=$(dirname "$gitdir")
    commits=$(git -C "$repo" log --after="$SINCE" --oneline --no-walk=unsorted 2>/dev/null | head -20)
    if [ -n "$commits" ]; then
        repo_name=$(basename "$repo")
        while IFS= read -r commit; do
            "$GINJA" remember "Git commit in $repo_name: $commit" 2>>"$LOG"
        done <<< "$commits"
    fi
done

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LAST_GIT_FILE"
echo "[$(date)] Nightly ingest complete" >> "$LOG"
