# docNerd Secrets Setup Guide

This guide walks you through obtaining and configuring every secret and variable docNerd needs to run. All secrets are configured in your **source repository** (the repo where PRs are opened and where you comment `@docNerd, doc for <branch>`).

---

## Overview

| Secret / Variable | Required | Where to get it |
|------------------|----------|-----------------|
| `DOCNERD_TARGET_OWNER` | Yes | Your docs repo owner (org or username) |
| `DOCNERD_TARGET_NAME` | Yes | Your docs repo name |
| `GITHUB_TOKEN` | Yes | Auto-provided by GitHub (no setup) |
| `DOCNERD_TARGET_REPO_TOKEN` | No* | Create a Personal Access Token (PAT) |
| `ANTHROPIC_API_KEY` | Yes | Create at console.anthropic.com |

\* Only needed if your docs repo is in a different org or requires different permissions than the source repo.

---

## Step 1: Identify Your Docs Repository

Before creating secrets, you need two values from your MkDocs + Mike documentation repository:

1. **Owner** — The GitHub organization or username that owns the docs repo  
   - Example: `beamable` (org) or `johndoe` (user)
2. **Repository name** — The repo name (without the owner)  
   - Example: `beamable-docs` or `my-docs`

**How to find these:**
- Open your docs repo in a browser: `https://github.com/beamable/beamable-docs`
- Owner = `beamable`, Name = `beamable-docs`

---

## Step 2: Add Repository Secrets in Your Source Repo

1. Go to your **source repository** (where PRs are opened).
2. Click **Settings** → **Secrets and variables** → **Actions**.
3. Click **New repository secret** for each secret below.

### `DOCNERD_TARGET_OWNER`

- **Value:** The owner of your docs repo (org or username).
- **Example:** `beamable`
- **How to get it:** Look at the URL of your docs repo: `github.com/{owner}/{repo}`.

### `DOCNERD_TARGET_NAME`

- **Value:** The name of your docs repo (no `.git`, no URL).
- **Example:** `beamable-docs`
- **How to get it:** Same URL — the part after the owner.

### `GITHUB_TOKEN`

- **Value:** You do **not** create this. GitHub automatically provides it to every workflow run.
- **How to use it:** In your workflow, pass `${{ secrets.GITHUB_TOKEN }}` to the action. It is always available; no setup required.
- **Permissions:** The default `GITHUB_TOKEN` has access to the repo where the workflow runs. If your **source** and **docs** repos are in the same org and the workflow runs in the source repo, this token can typically read the source repo and write to the docs repo (if the docs repo is in the same org and the token has sufficient scope).

---

## Step 3: Create Your Anthropic API Key

docNerd uses Claude to generate documentation. You need an API key from Anthropic.

### Create the key

1. Go to [console.anthropic.com](https://console.anthropic.com/).
2. Sign in or create an account.
3. Navigate to **API Keys** (or **Settings** → **API Keys**).
4. Click **Create Key**.
5. Give it a name (e.g. `docNerd`).
6. Copy the key immediately — it is shown only once.

### Add it as a secret

1. In your source repo: **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret**.
3. Name: `ANTHROPIC_API_KEY`
4. Value: Paste the key you copied.

### Billing and usage

- Anthropic charges per token. Check [anthropic.com/pricing](https://www.anthropic.com/pricing) for current rates.
- Set usage limits in the Anthropic console if desired.
- Keep the key private; never commit it to a repo.

---

## Step 4: (Optional) Create a Token for the Docs Repo

You only need `DOCNERD_TARGET_REPO_TOKEN` if:

- The docs repo is in a **different organization** than the source repo.
- The docs repo requires **different permissions** than the default `GITHUB_TOKEN`.
- You hit **rate limits** or permission errors with the default token.

### When you can skip this

- Source and docs repos are in the **same org**.
- The default `GITHUB_TOKEN` has write access to the docs repo.

In that case, leave `DOCNERD_TARGET_REPO_TOKEN` unset. docNerd will use `GITHUB_TOKEN` for both repos.

### Create a Personal Access Token (PAT)

1. On GitHub, click your profile picture → **Settings**.
2. In the left sidebar, scroll to **Developer settings** → **Personal access tokens**.
3. Choose **Tokens (classic)** or **Fine-grained tokens**.

#### Option A: Classic token (simpler)

1. Click **Generate new token (classic)**.
2. Name: `docNerd docs repo`.
3. Expiration: choose a duration (e.g. 90 days or No expiration).
4. Scopes: enable **`repo`** (full control of private repositories).
5. Click **Generate token**.
6. Copy the token — it is shown only once.

#### Option B: Fine-grained token (more restrictive)

1. Click **Generate new token**.
2. Name: `docNerd docs repo`.
3. Expiration: choose a duration.
4. **Repository access:** Select **Only select repositories** and choose your docs repo.
5. **Permissions:**
   - **Contents:** Read and write
   - **Metadata:** Read-only
   - **Pull requests:** Read and write
6. Click **Generate token**.
7. Copy the token.

### Add it as a secret

1. In your source repo: **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret**.
3. Name: `DOCNERD_TARGET_REPO_TOKEN`
4. Value: Paste the token.

### If using org-level secrets

For org-wide use:

1. Go to your org: **Settings** → **Secrets and variables** → **Actions**.
2. Add the same secrets there.
3. Choose which repos can use them (e.g. all repos or a selected list).

---

## Step 5: Verify Your Workflow Configuration

Your workflow should pass the secrets to the action:

```yaml
- uses: beamable/docnerd@v1
  with:
    target-owner: ${{ secrets.DOCNERD_TARGET_OWNER }}
    target-name: ${{ secrets.DOCNERD_TARGET_NAME }}
    github-token: ${{ secrets.GITHUB_TOKEN }}
    target-repo-token: ${{ secrets.DOCNERD_TARGET_REPO_TOKEN }}
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

**Notes:**

- If you did **not** create `DOCNERD_TARGET_REPO_TOKEN`, you can either:
  - Omit the `target-repo-token` input, or
  - Pass an empty string: `target-repo-token: ''`
- The action will fall back to `GITHUB_TOKEN` when `target-repo-token` is empty.

---

## Troubleshooting

### "Resource not accessible by integration"

- The token lacks permission to the docs repo.
- Fix: Create `DOCNERD_TARGET_REPO_TOKEN` with `repo` (classic) or Contents + Pull requests (fine-grained) for the docs repo.

### "I couldn't find that branch"

- The requested branch does not exist in the docs repo.
- Fix: Ensure the branch (e.g. `core/v7.1`) exists in the docs repo before commenting.

### "LLM API key is not configured"

- `ANTHROPIC_API_KEY` is missing or not passed correctly.
- Fix: Add the secret and ensure it is passed as `anthropic-api-key` in the workflow.

### "Target docs repository is not configured"

- `DOCNERD_TARGET_OWNER` or `DOCNERD_TARGET_NAME` is missing or wrong.
- Fix: Add both secrets and double-check spelling and casing.

### Workflow runs but docNerd doesn't respond

- The comment may not match the trigger.
- Fix: Use exactly `@docNerd, doc for <branch>` (e.g. `@docNerd, doc for core/v7.1`).

---

## Security Best Practices

1. **Never commit secrets** — Use GitHub Secrets only.
2. **Rotate keys periodically** — Especially PATs and API keys.
3. **Use fine-grained tokens** when possible — Limit scope to the docs repo.
4. **Review org access** — If using org secrets, restrict which repos can use them.
5. **Monitor usage** — Check Anthropic usage and set limits if needed.
