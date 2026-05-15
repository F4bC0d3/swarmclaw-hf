#!/usr/bin/env bash
# SwarmClaw HF Space entrypoint
# - Pulls data snapshot from HF Dataset (if configured) before starting
# - Starts SwarmClaw in background
# - Periodically pushes /app/data back to the Dataset
# - On SIGTERM (Space stop / sleep / restart) does one final push, then waits
#   for SwarmClaw to exit cleanly

set -euo pipefail

DATA_DIR="${DATA_DIR:-/app/data}"
SYNC_INTERVAL_SECONDS="${SYNC_INTERVAL_SECONDS:-300}"

mkdir -p "$DATA_DIR"

log() { echo "[entrypoint] $*"; }

# ---------------------------------------------------------------------------
# 1. Restore from HF Dataset (best-effort, never block startup)
# ---------------------------------------------------------------------------
if [ -n "${HF_DATASET_REPO:-}" ] && [ -n "${HF_TOKEN:-}" ]; then
  log "Restoring data from HF Dataset: $HF_DATASET_REPO"
  if ! python3 /usr/local/bin/hf_sync.py pull; then
    log "WARN: dataset pull failed, starting with empty/local data"
  fi
else
  log "HF_DATASET_REPO or HF_TOKEN not set, skipping restore (data is ephemeral!)"
fi

# ---------------------------------------------------------------------------
# 2. Background sync loop
# ---------------------------------------------------------------------------
sync_loop() {
  while true; do
    sleep "$SYNC_INTERVAL_SECONDS"
    if [ -n "${HF_DATASET_REPO:-}" ] && [ -n "${HF_TOKEN:-}" ]; then
      python3 /usr/local/bin/hf_sync.py push || log "WARN: periodic push failed"
    fi
  done
}

# ---------------------------------------------------------------------------
# 3. Start SwarmClaw
# ---------------------------------------------------------------------------
# The upstream image's CMD launches the server; we re-invoke it the same way.
# `swarmclaw` is on PATH in the official image.
log "Starting SwarmClaw on ${HOST:-0.0.0.0}:${PORT:-7860}"
swarmclaw &
APP_PID=$!

sync_loop &
SYNC_PID=$!

# ---------------------------------------------------------------------------
# 4. Graceful shutdown: final push, stop app
# ---------------------------------------------------------------------------
shutdown() {
  log "Shutdown signal received"
  # Stop the periodic loop first so it doesn't race with the final push
  kill "$SYNC_PID" 2>/dev/null || true
  if [ -n "${HF_DATASET_REPO:-}" ] && [ -n "${HF_TOKEN:-}" ]; then
    log "Final dataset push before exit"
    python3 /usr/local/bin/hf_sync.py push || log "WARN: final push failed"
  fi
  log "Stopping SwarmClaw (pid=$APP_PID)"
  kill -TERM "$APP_PID" 2>/dev/null || true
  wait "$APP_PID" 2>/dev/null || true
  exit 0
}
trap shutdown SIGTERM SIGINT

wait "$APP_PID"
