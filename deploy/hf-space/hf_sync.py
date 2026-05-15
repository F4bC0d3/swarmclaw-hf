#!/usr/bin/env python3
"""
HF Dataset <-> /app/data sync for SwarmClaw on Hugging Face Spaces.

Why a single tarball instead of per-file sync?
- SwarmClaw stores SQLite DBs (with WAL/SHM sidecars) under /app/data.
  Uploading hundreds of tiny files repeatedly is slow and racy.
- A tarball gives us one atomic upload + one atomic download.

Usage:
  hf_sync.py pull   # download + extract latest snapshot into $DATA_DIR
  hf_sync.py push   # tar $DATA_DIR and upload as data.tar.gz

Env:
  HF_DATASET_REPO   e.g. "your-username/swarmclaw-state" (required)
  HF_TOKEN          HF write token (required)
  DATA_DIR          local data directory (default: /app/data)
  HF_SNAPSHOT_NAME  filename in the dataset (default: data.tar.gz)
"""
from __future__ import annotations

import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError

REPO_ID = os.environ.get("HF_DATASET_REPO", "").strip()
TOKEN = os.environ.get("HF_TOKEN", "").strip()
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data")).resolve()
SNAPSHOT_NAME = os.environ.get("HF_SNAPSHOT_NAME", "data.tar.gz")
LOCK_FILE = Path("/tmp/swarmclaw_hf_sync.lock")


def log(msg: str) -> None:
    print(f"[hf_sync] {msg}", flush=True)


def require_config() -> None:
    if not REPO_ID or not TOKEN:
        log("HF_DATASET_REPO and HF_TOKEN must be set")
        sys.exit(2)


def acquire_lock() -> bool:
    """Best-effort, single-host lock so periodic + shutdown pushes don't race."""
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def ensure_repo(api: HfApi) -> None:
    """Create the dataset repo if it doesn't exist yet."""
    try:
        api.repo_info(repo_id=REPO_ID, repo_type="dataset", token=TOKEN)
    except RepositoryNotFoundError:
        log(f"Dataset {REPO_ID} not found, creating (private)")
        api.create_repo(
            repo_id=REPO_ID,
            repo_type="dataset",
            token=TOKEN,
            private=True,
            exist_ok=True,
        )


def pull() -> int:
    require_config()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    api = HfApi()
    try:
        ensure_repo(api)
    except Exception as e:
        log(f"could not verify dataset repo: {e}")
        return 1

    try:
        local_path = hf_hub_download(
            repo_id=REPO_ID,
            repo_type="dataset",
            filename=SNAPSHOT_NAME,
            token=TOKEN,
            cache_dir=os.environ.get("HF_HOME"),
        )
    except EntryNotFoundError:
        log(f"no snapshot {SNAPSHOT_NAME} yet, starting fresh")
        return 0
    except Exception as e:
        log(f"download failed: {e}")
        return 1

    log(f"extracting snapshot into {DATA_DIR}")
    # Filter prevents path traversal via crafted tar entries
    def safe_filter(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
        target = (Path(dest) / member.name).resolve()
        if not str(target).startswith(str(DATA_DIR)):
            log(f"skipping unsafe entry: {member.name}")
            return None
        return member

    with tarfile.open(local_path, "r:gz") as tf:
        try:
            tf.extractall(DATA_DIR, filter=safe_filter)  # py3.12+
        except TypeError:
            # Older Python: fall back to manual safe extract
            for m in tf.getmembers():
                if safe_filter(m, str(DATA_DIR)) is not None:
                    tf.extract(m, DATA_DIR)
    log("pull complete")
    return 0


def push() -> int:
    require_config()
    if not DATA_DIR.exists():
        log(f"{DATA_DIR} does not exist, nothing to push")
        return 0

    if not acquire_lock():
        log("another sync is in progress, skipping")
        return 0

    try:
        api = HfApi()
        ensure_repo(api)

        with tempfile.NamedTemporaryFile(
            suffix=".tar.gz", delete=False, prefix="swarmclaw-snap-"
        ) as tmp:
            tmp_path = tmp.name

        try:
            log(f"packing {DATA_DIR} -> {tmp_path}")
            with tarfile.open(tmp_path, "w:gz") as tf:
                # arcname="data" -> tarball extracts cleanly into DATA_DIR
                tf.add(DATA_DIR, arcname="data", recursive=True)

            size = os.path.getsize(tmp_path)
            log(f"uploading {SNAPSHOT_NAME} ({size / 1_048_576:.2f} MiB)")

            commit_msg = f"swarmclaw snapshot {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
            api.upload_file(
                path_or_fileobj=tmp_path,
                path_in_repo=SNAPSHOT_NAME,
                repo_id=REPO_ID,
                repo_type="dataset",
                token=TOKEN,
                commit_message=commit_msg,
            )
            log("push complete")
            return 0
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as e:
        log(f"push failed: {e}")
        return 1
    finally:
        release_lock()


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"pull", "push"}:
        print("usage: hf_sync.py {pull|push}", file=sys.stderr)
        return 2
    return pull() if sys.argv[1] == "pull" else push()


if __name__ == "__main__":
    sys.exit(main())
