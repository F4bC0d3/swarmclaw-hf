---
title: SwarmClaw
emoji: 🦞
colorFrom: red
colorTo: orange
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Self-hosted AI agent runtime with persistent memory
---

# SwarmClaw

[SwarmClaw](https://github.com/swarmclawai/swarmclaw) — a self-hosted AI agent
runtime — running on a Hugging Face Space with state persisted to a private
HF Dataset so it survives sleep and restarts.

This Space is managed automatically from a GitHub repo. **Do not edit files
here directly** — changes will be overwritten on the next deploy. Edit the
source repo and push.

## How persistence works

1. On boot the entrypoint downloads `data.tar.gz` from the configured HF
   Dataset and extracts it into `/app/data`.
2. SwarmClaw runs against `/app/data`.
3. Every `SYNC_INTERVAL_SECONDS` (default 300) the entrypoint repacks the
   directory and commits it back to the Dataset.
4. On `SIGTERM` (Space sleeping or restarting) one final push runs before
   exit.

## Configured by the deploy workflow

These are pushed by the GitHub Action on every deploy:

| Key                     | Where    | Set by                          |
|-------------------------|----------|---------------------------------|
| `HF_TOKEN`              | Secret   | GitHub `secrets.HF_TOKEN`       |
| `ACCESS_KEY`            | Secret   | GitHub `secrets.ACCESS_KEY` (or auto-generated) |
| `CREDENTIAL_SECRET`     | Secret   | GitHub `secrets.CREDENTIAL_SECRET` (or auto-generated) |
| `HF_DATASET_REPO`       | Variable | `<HF_USERNAME>/<HF_DATASET_NAME>` |
| `SYNC_INTERVAL_SECONDS` | Variable | GitHub `vars.SYNC_INTERVAL_SECONDS` |

To change any of these, update the GitHub repo secret/variable and re-run
the **Deploy SwarmClaw** workflow.
