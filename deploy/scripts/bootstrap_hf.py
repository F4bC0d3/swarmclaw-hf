#!/usr/bin/env python3
"""
One-shot bootstrap for SwarmClaw on Hugging Face Spaces.

Idempotent. Safe to re-run. Run from CI:

  python deploy/scripts/bootstrap_hf.py

Required env:
  HF_TOKEN           HF token with write on Spaces + Datasets
  HF_USERNAME        e.g. "alice"

Optional env (with sensible defaults):
  HF_SPACE_NAME      default: "swarmclaw"
  HF_DATASET_NAME    default: "swarmclaw-state"
  ACCESS_KEY         SwarmClaw login key. Auto-generated if missing.
  CREDENTIAL_SECRET  SwarmClaw cred encryption. Auto-generated if missing.
  SPACE_HARDWARE     default: "cpu-basic"
  SPACE_PRIVATE      "true"|"false", default: "false"
  DATASET_PRIVATE    "true"|"false", default: "true"
  SPACE_DIR          path to upload, default: "deploy/hf-space"

Side effects:
  - Creates the dataset if it doesn't exist
  - Creates the Space if it doesn't exist (Docker SDK)
  - Sets Space secrets/variables (HF_TOKEN, HF_DATASET_REPO, ACCESS_KEY, ...)
  - Uploads SPACE_DIR contents to the Space repo
  - Triggers a rebuild via factory_reboot (only on first secret install)

Outputs (GitHub Actions friendly):
  Writes to $GITHUB_OUTPUT:
    space_url, dataset_url, access_key_generated (true/false)
  And $GITHUB_STEP_SUMMARY: a markdown report.
"""
from __future__ import annotations

import os
import secrets
import string
import sys
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.utils import (
    BadRequestError,
    HfHubHTTPError,
    RepositoryNotFoundError,
)


def env(name: str, default: str | None = None, required: bool = False) -> str:
    raw = os.environ.get(name, default)
    # Strip whitespace/newlines that often sneak in via copy-paste of secrets.
    # An HF token with a trailing "\n" produces httpx LocalProtocolError later.
    v = raw.strip() if isinstance(raw, str) else raw
    if required and not v:
        print(f"ERROR: missing required env {name}", file=sys.stderr)
        sys.exit(2)
    return v or ""


def env_bool(name: str, default: bool) -> bool:
    return env(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


def gen_secret(n: int = 48) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def write_output(key: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as f:
        # Use heredoc form to be safe with multiline values
        f.write(f"{key}<<EOF\n{value}\nEOF\n")


def append_summary(md: str) -> None:
    s = os.environ.get("GITHUB_STEP_SUMMARY")
    if not s:
        print(md)
        return
    with open(s, "a", encoding="utf-8") as f:
        f.write(md + "\n")


def ensure_dataset(api: HfApi, repo_id: str, private: bool, token: str) -> bool:
    """Returns True if newly created."""
    try:
        api.repo_info(repo_id=repo_id, repo_type="dataset", token=token)
        print(f"[bootstrap] dataset exists: {repo_id}")
        return False
    except RepositoryNotFoundError:
        print(f"[bootstrap] creating dataset: {repo_id} (private={private})")
        api.create_repo(
            repo_id=repo_id,
            repo_type="dataset",
            token=token,
            private=private,
            exist_ok=True,
        )
        return True


def ensure_space(
    api: HfApi,
    repo_id: str,
    private: bool,
    hardware: str,
    token: str,
) -> bool:
    """Returns True if newly created."""
    try:
        api.repo_info(repo_id=repo_id, repo_type="space", token=token)
        print(f"[bootstrap] space exists: {repo_id}")
        return False
    except RepositoryNotFoundError:
        print(f"[bootstrap] creating space: {repo_id} (private={private}, hw={hardware})")
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            token=token,
            private=private,
            exist_ok=True,
            space_sdk="docker",
            space_hardware=hardware,
        )
        return True


def list_existing_secrets(api: HfApi, repo_id: str, token: str) -> set[str]:
    """Best-effort: HF only lets us list names, not values."""
    try:
        items = api.get_space_secrets(repo_id=repo_id, token=token) or []
        return {s["key"] for s in items if "key" in s}
    except Exception:
        return set()


def list_existing_vars(api: HfApi, repo_id: str, token: str) -> set[str]:
    try:
        items = api.get_space_variables(repo_id=repo_id, token=token) or []
        return {v["key"] for v in items if "key" in v}
    except Exception:
        return set()


def set_space_secret(api: HfApi, repo_id: str, key: str, value: str, token: str) -> None:
    print(f"[bootstrap] set secret: {key}")
    api.add_space_secret(repo_id=repo_id, key=key, value=value, token=token)


def set_space_variable(api: HfApi, repo_id: str, key: str, value: str, token: str) -> None:
    print(f"[bootstrap] set var:    {key}={value}")
    api.add_space_variable(repo_id=repo_id, key=key, value=value, token=token)


def upload_space_content(api: HfApi, repo_id: str, folder: Path, token: str) -> None:
    if not folder.is_dir():
        print(f"ERROR: SPACE_DIR not found: {folder}", file=sys.stderr)
        sys.exit(2)
    print(f"[bootstrap] uploading {folder} -> {repo_id}")
    api.upload_folder(
        repo_id=repo_id,
        repo_type="space",
        folder_path=str(folder),
        commit_message="chore: deploy from GitHub",
        token=token,
        # Don't try to be clever about deletes; HF git history covers us.
        delete_patterns=["*"],
    )


def main() -> int:
    token = env("HF_TOKEN", required=True)
    user = env("HF_USERNAME", required=True)
    space_name = env("HF_SPACE_NAME", "swarmclaw")
    dataset_name = env("HF_DATASET_NAME", "swarmclaw-state")
    hardware = env("SPACE_HARDWARE", "cpu-basic")
    space_private = env_bool("SPACE_PRIVATE", False)
    dataset_private = env_bool("DATASET_PRIVATE", True)
    space_dir = Path(env("SPACE_DIR", "deploy/hf-space"))

    space_repo = f"{user}/{space_name}"
    dataset_repo = f"{user}/{dataset_name}"

    api = HfApi()

    # --- Create / verify repos ---------------------------------------------
    try:
        ensure_dataset(api, dataset_repo, dataset_private, token)
        space_created = ensure_space(api, space_repo, space_private, hardware, token)
    except (HfHubHTTPError, BadRequestError) as e:
        print(f"ERROR: HF API call failed: {e}", file=sys.stderr)
        return 1

    # --- Resolve secret values ---------------------------------------------
    access_key_in = env("ACCESS_KEY", "")
    cred_secret_in = env("CREDENTIAL_SECRET", "")

    access_key_generated = False
    cred_secret_generated = False

    existing_secrets = list_existing_secrets(api, space_repo, token)
    existing_vars = list_existing_vars(api, space_repo, token)

    if not access_key_in:
        if "ACCESS_KEY" in existing_secrets:
            access_key_in = ""  # already set, leave alone
        else:
            access_key_in = gen_secret(40)
            access_key_generated = True

    if not cred_secret_in:
        if "CREDENTIAL_SECRET" in existing_secrets:
            cred_secret_in = ""
        else:
            cred_secret_in = gen_secret(48)
            cred_secret_generated = True

    # --- Push secrets / vars (only when we have a value) -------------------
    # HF_TOKEN: rotate every run so the Space can always read/write the dataset
    set_space_secret(api, space_repo, "HF_TOKEN", token, token)

    if access_key_in:
        set_space_secret(api, space_repo, "ACCESS_KEY", access_key_in, token)
    if cred_secret_in:
        set_space_secret(api, space_repo, "CREDENTIAL_SECRET", cred_secret_in, token)

    # Public-ish config goes in variables so it's visible in the Space settings
    if existing_vars and "HF_DATASET_REPO" in existing_vars:
        # update by re-adding (HF API treats add as upsert)
        pass
    set_space_variable(api, space_repo, "HF_DATASET_REPO", dataset_repo, token)
    set_space_variable(api, space_repo, "SYNC_INTERVAL_SECONDS",
                       env("SYNC_INTERVAL_SECONDS", "300"), token)

    # --- Upload Space content ----------------------------------------------
    upload_space_content(api, space_repo, space_dir, token)

    # --- Outputs / summary -------------------------------------------------
    space_url = f"https://huggingface.co/spaces/{space_repo}"
    space_direct = f"https://{user.lower().replace('_','-')}-{space_name.lower().replace('_','-')}.hf.space"
    dataset_url = f"https://huggingface.co/datasets/{dataset_repo}"

    write_output("space_repo", space_repo)
    write_output("space_url", space_url)
    write_output("space_direct_url", space_direct)
    write_output("dataset_url", dataset_url)
    write_output("space_created", "true" if space_created else "false")
    write_output("access_key_generated", "true" if access_key_generated else "false")
    write_output("credential_secret_generated", "true" if cred_secret_generated else "false")

    summary = [
        "## SwarmClaw HF deploy",
        "",
        f"- Space: [{space_repo}]({space_url})",
        f"- Live URL: <{space_direct}>",
        f"- Dataset: [{dataset_repo}]({dataset_url})",
        f"- Space created this run: **{space_created}**",
    ]
    if access_key_generated:
        summary += [
            "",
            "### ⚠ Generated ACCESS_KEY (save this — it won't be shown again)",
            "",
            f"```\n{access_key_in}\n```",
            "",
            "Add this as `ACCESS_KEY` in your GitHub repo secrets to keep it stable across runs.",
        ]
    if cred_secret_generated:
        summary += [
            "",
            "### ⚠ Generated CREDENTIAL_SECRET (save this — it won't be shown again)",
            "",
            f"```\n{cred_secret_in}\n```",
            "",
            "Add this as `CREDENTIAL_SECRET` in your GitHub repo secrets so future",
            "deploys keep using the same value. **Do not change it after you've",
            "saved provider API keys in SwarmClaw** — those keys are encrypted",
            "with this secret and would become unreadable.",
        ]
    append_summary("\n".join(summary))

    print("[bootstrap] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
