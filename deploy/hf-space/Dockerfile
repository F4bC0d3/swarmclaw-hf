# SwarmClaw for Hugging Face Spaces (Docker SDK)
# - Listens on port 7860 (HF requirement)
# - Runs as UID 1000 (HF requirement)
# - Restores /app/data from an HF Dataset on boot
# - Periodically syncs /app/data back to the HF Dataset
# - Performs a final sync on SIGTERM (when HF stops the Space)

FROM ghcr.io/swarmclawai/swarmclaw:latest

USER root

# Tools we need for backup/restore + signal-safe shutdown
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv \
      tini ca-certificates curl tar gzip \
 && rm -rf /var/lib/apt/lists/*

# huggingface_hub gives us a battle-tested Python client for upload/download
RUN python3 -m pip install --no-cache-dir --break-system-packages \
      "huggingface_hub>=0.24.0"

# HF Spaces require a writable home for UID 1000
RUN useradd -m -u 1000 -s /bin/bash spaceuser 2>/dev/null || true \
 && mkdir -p /app/data /home/spaceuser/.cache/huggingface \
 && chown -R 1000:1000 /app /home/spaceuser

COPY --chown=1000:1000 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chown=1000:1000 hf_sync.py    /usr/local/bin/hf_sync.py
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/hf_sync.py

USER 1000
WORKDIR /app

# SwarmClaw config: bind to HF's required port and the public iface
ENV PORT=7860 \
    HOST=0.0.0.0 \
    HOSTNAME=0.0.0.0 \
    NODE_ENV=production \
    DATA_DIR=/app/data \
    HF_HOME=/home/spaceuser/.cache/huggingface \
    SYNC_INTERVAL_SECONDS=300

EXPOSE 7860

# tini handles PID 1 so SIGTERM reaches our trap and the final HF push runs
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/entrypoint.sh"]
