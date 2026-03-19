# docNerd

Generate documentation PRs from source PR comments using Claude. Comment `@docNerd, doc for core/v7.1` on a PR and docNerd uses Claude to generate or update docs, then opens a PR on the specified branch in your MkDocs + Mike docs repository.

## How It Works

1. You comment on a source PR: `@docNerd, doc for core/v7.1`
2. docNerd validates the branch exists in the docs repo
3. docNerd replies "yes, working on it" or "I couldn't find that branch"
4. Claude analyzes the PR and generates/edits documentation
5. docNerd opens a PR in the docs repo targeting the specified branch
6. docNerd replies with "Here is the link to the Doc changes" + PR link

## Setup

### 1. Add the workflow to your source repo

Create `.github/workflows/docnerd.yml` in your **source** repository:

```yaml
name: docNerd

on:
  issue_comment:
    types: [created]

jobs:
  docnerd:
    if: github.event.issue.pull_request != null && contains(github.event.comment.body, '@docNerd')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: beamable/docnerd@v1
        with:
          target-owner: ${{ secrets.DOCNERD_TARGET_OWNER }}
          target-name: ${{ secrets.DOCNERD_TARGET_NAME }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
          target-repo-token: ${{ secrets.DOCNERD_TARGET_REPO_TOKEN }}
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

Replace `beamable/docnerd` with your repo path if different.

### 2. Configure secrets

In your source repo: **Settings → Secrets and variables → Actions**

| Secret | Required | Description |
|--------|----------|-------------|
| `DOCNERD_TARGET_OWNER` | Yes | Docs repo owner (e.g. `your-org`) |
| `DOCNERD_TARGET_NAME` | Yes | Docs repo name (e.g. `your-docs-repo`) |
| `GITHUB_TOKEN` | Yes | Auto-provided; pass to action for API access |
| `DOCNERD_TARGET_REPO_TOKEN` | No | Token for docs repo if different from `GITHUB_TOKEN` |
| `ANTHROPIC_API_KEY` | Yes | Your Anthropic API key for Claude |

### 3. Add config and rules (optional)

Add to your source repo root:

- **`config.yaml`** — Override defaults (target repo, rules path, trigger phrase, etc.)
- **`rules/`** — YAML files for documentation style, structure, and terminology

If omitted, docNerd uses built-in defaults. See `config.example.yaml` and `rules/` in this repo.

## Usage

1. Open a PR in your source repo
2. Comment: `@docNerd, doc for core/v7.1` (use your Mike version branch name)
3. docNerd validates the branch, generates docs, and opens a PR in your docs repo

## Action inputs

| Input | Required | Description |
|-------|----------|-------------|
| `target-owner` | Yes | Docs repository owner |
| `target-name` | Yes | Docs repository name |
| `github-token` | Yes | Pass `secrets.GITHUB_TOKEN` |
| `target-repo-token` | No | Token for docs repo (if different) |
| `anthropic-api-key` | Yes | Anthropic API key |

## Requirements

- Source repo with PRs
- Target MkDocs + Mike docs repo with version branches
- Anthropic API key
