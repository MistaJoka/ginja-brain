#!/usr/bin/env python3
"""Watch /mnt/brain/inbox for new files: documents get ingested into memory,
messages (*.msg.txt or anything in inbox/messages/) get answered — the reply
is written to /mnt/brain/outbox by `ginja answer`."""

import logging
import subprocess
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
# /mnt/brain is an ntfs-3g FUSE mount — inotify events never fire there,
# so we must poll. 5s interval on two small directories is negligible.
from watchdog.observers.polling import PollingObserver as Observer

INBOX = Path("/mnt/brain/inbox")
MESSAGES = INBOX / "messages"
INGESTED = Path("/mnt/brain/ingested")
OUTBOX = Path("/mnt/brain/outbox")
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


def is_message(path: Path) -> bool:
    return path.name.endswith(".msg.txt") or MESSAGES in path.parents


class InboxHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        # Wait briefly for file to finish writing
        time.sleep(2)
        if not path.exists():
            return
        if is_message(path):
            self.handle_message(path)
        else:
            self.handle_document(path)

    def handle_message(self, path: Path):
        logging.info(f"Message from Andre: {path.name}")
        try:
            result = subprocess.run(
                [str(GINJA), "answer", str(path)],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode == 0:
                dest_dir = INGESTED / "messages"
                dest_dir.mkdir(parents=True, exist_ok=True)
                path.rename(dest_dir / path.name)
                logging.info(f"✓ Answered — reply in {OUTBOX}")
            else:
                logging.error(f"Answer failed: {result.stderr[-400:]}")
        except Exception as e:
            logging.error(f"Error answering {path.name}: {e}")

    def handle_document(self, path: Path):
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
                logging.error(f"Ingest failed: {result.stderr[-400:]}")
        except Exception as e:
            logging.error(f"Error processing {path.name}: {e}")


def catch_up(handler: InboxHandler):
    """Process anything already sitting in the inbox at startup — files that
    arrived while the watcher was down (or during the years inotify silently
    never fired on this FUSE mount)."""
    for path in sorted(INBOX.rglob("*")):
        if path.is_file():
            if is_message(path):
                handler.handle_message(path)
            else:
                handler.handle_document(path)


if __name__ == "__main__":
    INBOX.mkdir(parents=True, exist_ok=True)
    MESSAGES.mkdir(parents=True, exist_ok=True)
    INGESTED.mkdir(parents=True, exist_ok=True)
    OUTBOX.mkdir(parents=True, exist_ok=True)
    logging.info(f"Watching {INBOX} (polling — FUSE mount) …")

    handler = InboxHandler()
    catch_up(handler)

    observer = Observer(timeout=5)
    observer.schedule(handler, str(INBOX), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
