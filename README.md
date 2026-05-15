# SwarmClaw -> HF Spaces (auto-deploy from GitHub)

Push this folder to a GitHub repo, set 4 secrets, and you have a self-hosted
[SwarmClaw](https://github.com/swarmclawai/swarmclaw) running on Hugging Face
with persistent state and a Cloudflare Worker that wakes it on demand.

## What this does

```
git push --> GitHub Actions --> creates HF Dataset (state)
                            --> creates HF Space (Docker)
                            --> pushes Space secrets/vars
                            --> uploads deploy/hf-space/ to Space
                            --> deploys Cloudflare Worker (proxy + wake)
```

Everything is idempotent. Re-running the workflow is safe.

## Quick start

1. Create a new GitHub repo (private recommended).
2. Upload this folder's contents to it (or drag-and-drop in the GitHub UI).
3. Set repo secrets: **Settings -> Secrets and variables -> Actions -> Secrets**
   - `HF_TOKEN` - HF token with **Write** on Spaces + Datasets
   - `HF_USERNAME` - your HF username
   - `CLOUDFLARE_API_TOKEN` - Cloudflare token, "Edit Cloudflare Workers" template
   - `CLOUDFLARE_ACCOUNT_ID` - from Cloudflare dashboard sidebar
4. Trigger the workflow: **Actions -> Deploy SwarmClaw -> Run workflow**
   (or just push another commit)
5. Copy the auto-generated `ACCESS_KEY` from the run summary into a new repo
   secret of the same name. Done.

Full guide: [`deploy/DEPLOY.md`](deploy/DEPLOY.md)

## What's inside

```
.github/workflows/deploy.yml         the one workflow that does everything
deploy/
  DEPLOY.md                          full setup + troubleshooting guide
  scripts/bootstrap_hf.py            creates Dataset + Space, pushes secrets, uploads
  hf-space/                          contents uploaded to your HF Space
    Dockerfile                       extends ghcr.io/swarmclawai/swarmclaw, port 7860
    entrypoint.sh                    pull-on-boot, periodic sync, final push on SIGTERM
    hf_sync.py                       tarball snapshot via huggingface_hub
    README.md                        HF Space front-matter
  cloudflare-worker/
    src/worker.js                    status -> proxy if running, else /restart + warm page
    wrangler.template.toml           rendered to wrangler.toml during deploy
    package.json
```
