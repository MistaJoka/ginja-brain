#!/usr/bin/env python3
"""Watch /mnt/brain/inbox for new files and auto-ingest them into ginja."""

import logging
import subprocess
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

INBOX = Path("/mnt/brain/inbox")
INGESTED = Path("/mnt/brain/ingested")
LOG_FILE = Path.home() / ".ginja" / "inbox-watcher.log"
GINJA = Path.home() / "bin" / "ginja"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)


class InboxHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        # Wait briefly for file to finish writing
        time.sleep(2)
        if not path.exists():
            return
        logging.info(f"New file detected: {path.name}")
        try:
            result = subprocess.run(
                [str(GINJA), "ingest", str(path)],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                INGESTED.mkdir(parents=True, exist_ok=True)
                dest = INGESTED / path.name
                path.rename(dest)
                logging.info(f"✓ Ingested and moved to {dest}")
            else:
                logging.error(f"Ingest failed: {result.stderr}")
        except Exception as e:
            logging.error(f"Error processing {path.name}: {e}")


if __name__ == "__main__":
    INBOX.mkdir(parents=True, exist_ok=True)
    INGESTED.mkdir(parents=True, exist_ok=True)
    logging.info(f"Watching {INBOX} for new files…")

    observer = Observer()
    observer.schedule(InboxHandler(), str(INBOX), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
