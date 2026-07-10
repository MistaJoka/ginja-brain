#!/bin/bash
# Qdrant collection backup — daily snapshot of every collection to /mnt/brain/qdrant-backups.
# Delivers operator goal: "Design a Qdrant collection backup strategy — daily snapshot to /mnt/brain/qdrant-backups"
# Run daily via cron: 0 3 * * * /home/ginja/.ginja/qdrant-backup.sh
#
# Strategy: server-side snapshot per collection via the Qdrant HTTP API, download
# the archive to the 1.9TB HDD, then delete the server-side copy so the docker
# volume on the SSD stays lean. Keep 14 daily generations. No LLM involved.

set -u
CFG="$HOME/.ginja/config.json"
LOG="$HOME/.ginja/qdrant-backup.log"
QDRANT_URL=$(python3 -c "import json; print(json.load(open('$CFG')).get('qdrant_url','http://localhost:6333'))" 2>/dev/null || echo "http://localhost:6333")
BACKUP_ROOT="/mnt/brain/qdrant-backups"
DEST="$BACKUP_ROOT/$(date +%F)"
KEEP_DAYS=14

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

log "=== Qdrant backup starting → $DEST ==="
mkdir -p "$DEST" || { log "FATAL: cannot create $DEST"; exit 1; }

COLLECTIONS=$(curl -s --max-time 10 "$QDRANT_URL/collections" \
    | python3 -c "import sys,json; print('\n'.join(c['name'] for c in json.load(sys.stdin)['result']['collections']))" 2>/dev/null)

if [ -z "$COLLECTIONS" ]; then
    log "FATAL: could not list collections — is Qdrant up at $QDRANT_URL?"
    exit 1
fi

FAILED=0
for COL in $COLLECTIONS; do
    SNAP=$(curl -s --max-time 300 -X POST "$QDRANT_URL/collections/$COL/snapshots" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['name'])" 2>/dev/null)
    if [ -z "$SNAP" ]; then
        log "✗ $COL: snapshot creation failed"
        FAILED=1
        continue
    fi
    if curl -s --max-time 600 -o "$DEST/$COL.snapshot" \
            "$QDRANT_URL/collections/$COL/snapshots/$SNAP"; then
        SIZE=$(stat -c%s "$DEST/$COL.snapshot" 2>/dev/null || echo 0)
        log "✓ $COL → $COL.snapshot (${SIZE} bytes)"
    else
        log "✗ $COL: download failed"
        FAILED=1
    fi
    # Remove the server-side snapshot regardless — don't accumulate on the SSD
    curl -s --max-time 60 -X DELETE "$QDRANT_URL/collections/$COL/snapshots/$SNAP" >/dev/null 2>&1
done

# Prune old generations (by directory name, YYYY-MM-DD)
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime "+$KEEP_DAYS" \
    -exec rm -rf {} \; 2>/dev/null

if [ "$FAILED" = "0" ]; then
    log "=== Backup complete: $(ls "$DEST" | wc -l) collections in $DEST ==="
else
    log "=== Backup finished WITH ERRORS — check above ==="
fi
exit "$FAILED"
