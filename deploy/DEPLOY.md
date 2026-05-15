# Deploying SwarmClaw

This repo deploys SwarmClaw to a free Hugging Face Space with state persisted
to a private HF Dataset, fronted by a Cloudflare Worker that wakes the Space
on demand. **Everything happens automatically when you push to `main`.**

```
git push --> GitHub Actions --> creates HF Dataset (state)
                            --> creates HF Space (Docker)
                            --> sets Space secrets/vars
                            --> uploads deploy/hf-space/ to Space
                            --> deploys Cloudflare Worker (proxy + wake)
```

You don't click anything in the HF or Cloudflare UIs. You set secrets in
GitHub once, push, and the workflow handles the rest. Re-running is safe and
idempotent.

---

## One-time setup

### 1. Get an HF token

<https://huggingface.co/settings/tokens> → **Create token** → **Fine-grained**.

Permissions:
- Repositories → **Write** access on Spaces and Datasets in your namespace

### 2. Get Cloudflare credentials (optional but recommended)

The Worker is what makes a sleeping Space wake on the first incoming request.
Without it, your Space still works but stays asleep until someone visits the
HF page and manually restarts it.

- **Account ID:** Cloudflare dashboard → right sidebar → copy "Account ID"
- **API Token:** <https://dash.cloudflare.com/profile/api-tokens> → **Create
  Token** → use the **Edit Cloudflare Workers** template → **Continue** →
  **Create Token** → copy the value

### 3. Set GitHub repo secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**.

Required:

| Secret                   | Value                                          |
|--------------------------|------------------------------------------------|
| `HF_TOKEN`               | the token from step 1                          |
| `HF_USERNAME`            | your HF username (e.g. `alice`)                |
| `CLOUDFLARE_API_TOKEN`   | from step 2 (omit to skip Worker deploy)       |
| `CLOUDFLARE_ACCOUNT_ID`  | from step 2 (omit to skip Worker deploy)       |

Optional (let the workflow auto-generate on first run if you don't set them):

| Secret              | Notes                                                                |
|---------------------|----------------------------------------------------------------------|
| `ACCESS_KEY`        | SwarmClaw login key. If unset, workflow generates one and prints it in the run summary. **Copy it from the summary into this secret** so future runs don't drift. |
| `CREDENTIAL_SECRET` | Used by SwarmClaw to encrypt provider creds. Auto-generated if unset. Once generated, do **not** change it or saved provider creds become unreadable. |

### 4. (Optional) Set GitHub repo variables

Repo → **Settings → Secrets and variables → Actions → Variables tab**.

| Variable             | Default            | Purpose                                |
|----------------------|--------------------|----------------------------------------|
| `HF_SPACE_NAME`      | `swarmclaw`        | Space repo name                        |
| `HF_DATASET_NAME`    | `swarmclaw-state`  | Dataset repo name                      |
| `SPACE_HARDWARE`     | `cpu-basic`        | HF hardware tier                       |
| `SPACE_PRIVATE`      | `false`            | Make the Space private                 |
| `DATASET_PRIVATE`    | `true`             | Dataset visibility                     |
| `WORKER_NAME`        | `swarmclaw-wake`   | Cloudflare Worker name                 |
| `WORKER_REQUIRE_KEY` | `false`            | If `true`, the Worker requires `?key=ACCESS_KEY` |
| `SYNC_INTERVAL_SECONDS` | `300`           | How often the Space pushes a snapshot  |

---

## Deploy

```bash
git add .
git commit -m "deploy swarmclaw"
git push
```

Open the **Actions** tab on GitHub and watch the **Deploy SwarmClaw** run.

The summary at the bottom of the run shows:

- Your HF Space URL (`https://huggingface.co/spaces/<user>/<space>`)
- Your live Space URL (`https://<user>-<space>.hf.space`)
- Your Dataset URL
- Your Worker URL (`https://<worker>.<subdomain>.workers.dev`)
- Any auto-generated `ACCESS_KEY` (only on first run)

First run takes ~3–5 minutes: most of that is HF building the Docker image.
Later pushes only redeploy what changed (Space content + Worker).

> **First-run note:** if `ACCESS_KEY` was auto-generated, copy it from the run
> summary into the `ACCESS_KEY` GitHub secret. Then re-run the workflow so
> the Worker also gets it (only matters if you enable `WORKER_REQUIRE_KEY`).

---

## Using it

1. Visit your Worker URL.
2. If the Space is asleep, you see a "warming up" page that auto-refreshes.
3. After ~30–90s, the Worker proxies you straight to SwarmClaw.
4. Log in with `ACCESS_KEY`.

State (agents, conversations, memory) is snapshotted to your HF Dataset every
5 minutes and on shutdown. Restarts and sleep cycles preserve everything.

### Custom domain

Cloudflare dashboard → **Workers & Pages → swarmclaw-wake → Triggers →
Custom Domains → Add Custom Domain**. Cloudflare handles DNS + TLS.

---

## What's where

```
.github/workflows/deploy.yml         # the one workflow that does everything
deploy/
  scripts/bootstrap_hf.py            # creates Dataset + Space, pushes secrets, uploads
  hf-space/
    Dockerfile                       # extends ghcr.io/swarmclawai/swarmclaw, port 7860
    entrypoint.sh                    # pull-on-boot, periodic push, final push on SIGTERM
    hf_sync.py                       # tarball snapshot via huggingface_hub
    README.md                        # HF Space front-matter (sdk: docker)
  cloudflare-worker/
    src/worker.js                    # status -> proxy if running, else /restart + warm page
    wrangler.template.toml           # rendered to wrangler.toml at deploy time
    package.json
```

---

## Re-running, rotating, recovering

| You want to...                       | Do this                                           |
|--------------------------------------|---------------------------------------------------|
| Redeploy without changes             | **Actions → Deploy SwarmClaw → Run workflow**     |
| Change the Space hardware tier       | Set `SPACE_HARDWARE` repo variable, re-run        |
| Rotate `HF_TOKEN`                    | Update the GitHub secret, re-run workflow         |
| Rotate `ACCESS_KEY`                  | Update the GitHub secret, re-run workflow         |
| Roll back state to an earlier point  | In the dataset repo on huggingface.co, revert the commit, then restart the Space |
| Force a snapshot now                 | **Restart Space** in HF UI — entrypoint pushes on SIGTERM |
| Disable Worker                       | Remove `CLOUDFLARE_*` secrets; the Worker job auto-skips |

---

## Troubleshooting

**Bootstrap step fails with 401**
Your `HF_TOKEN` doesn't have write on Spaces and Datasets. Regenerate it as
a fine-grained token with both permissions.

**Bootstrap creates a Space but the build fails**
Open `https://huggingface.co/spaces/<user>/<space>?logs=build` to see the
Docker build log. The Dockerfile extends the official upstream image, so
build failures usually mean upstream has a bad release; pin a specific tag
in `deploy/hf-space/Dockerfile` (e.g. `ghcr.io/swarmclawai/swarmclaw:1.9.32`).

**Space runs but data doesn't persist**
Check the runtime logs for `[hf_sync]` lines. If you see permission errors
on the dataset, your `HF_TOKEN` likely lacks dataset write access.

**Worker shows "warming up" forever**
- Hit `https://<worker>/__wake/status` to see the live HF stage.
- If it's `BUILD_FAILED` or `RUNTIME_ERROR`, look at the Space logs.
- If your token can't `POST /restart`, the Worker can't wake the Space.
  Make sure `HF_TOKEN` has Space write access in your namespace.

**The auto-generated ACCESS_KEY is in the run summary but not anywhere safe**
Copy it. Set it as the `ACCESS_KEY` GitHub secret. From now on the workflow
uses your value instead of generating a new one.
