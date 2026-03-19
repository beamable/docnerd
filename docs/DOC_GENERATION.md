# Controlling docNerd's Documentation Output

docNerd is designed to **integrate with existing docs** rather than create new files. This guide explains how to control what it does.

## Default Behavior

1. **Fetch existing docs** — docNerd loads all `.md` files from your docs repo (from `mkdocs.yml`'s `docs_dir`, default `docs/`).

2. **Check for invalidation** — It analyzes the PR to find docs that are now wrong (API changes, config changes, deprecations, etc.).

3. **Add context to existing docs** — If the PR adds info that belongs in an existing page, it adds it there.

4. **Create new files rarely** — New files are only suggested when the PR introduces a major new feature with no existing doc to add to.

## Configuration

### `config.yaml`

```yaml
# Only edit existing docs; never create new files
allow_new_files: false

# Docs fetcher limits (when loading from target repo)
docs_fetcher:
  max_files: 50
  max_content_per_file: 8000
```

- **`allow_new_files`** — When `false`, docNerd will only edit existing files. Any new-file suggestions from Claude are filtered out. Default: `true`.

### Rules: `rules/doc_generation.yaml`

Customize doc generation behavior:

```yaml
# Priority order (Claude follows this)
priority:
  1: "Fix invalidated docs"
  2: "Add context to existing docs"
  3: "Create new files only when necessary"

# When to create new files
create_new_files:
  allowed: false
  exceptions:
    - "PR introduces a major new API with no existing doc"
  require_justification: true

# What PR changes should trigger doc updates
invalidation_triggers:
  - "API signature changes"
  - "Config option changes"
  - "Behavior changes"
  - "Deprecations or removals"
```

Edit this file in your **source repo** (in `rules/`) to match your docs workflow.

## How It Works

1. docNerd fetches `mkdocs.yml` and all `.md` files from the target branch.

2. It passes the PR diff, existing doc content, and nav structure to Claude.

3. The system prompt instructs Claude to:
   - First fix invalidated docs
   - Then add context to existing docs
   - Only create new files when strictly justified

4. If `allow_new_files: false`, any new-file edits are removed before the PR is created.

## Tips

- **Put rules in your source repo** — Add `rules/doc_generation.yaml` to customize behavior per project.

- **Use `allow_new_files: false`** — For strict control, disable new files and rely on edits only.

- **Check the rules** — The `rules/` directory is loaded from your source repo. If missing, docnerd falls back to built-in defaults.
