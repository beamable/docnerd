# Controlling docNerd's Documentation Output

docNerd is designed to **integrate with existing docs** rather than create new files. This guide explains how to control what it does.

## Default behavior (`doc_generation.mode: phased`)

1. **Analyze the source PR** — Same as before: file list, patches, optional full file bodies (`pr_analysis`).

2. **PR narrative (Claude)** — One call writes a **standalone PR change document** in Markdown and saves it under `.docnerd/pr_change_narrative.md` in the working directory. Later steps use **only this narrative** (not the full PR payload) when touching each doc file.

3. **One Claude call per `.md` file** — For **every** markdown path under `docs_dir`, docNerd loads the file from the target branch and asks Claude: given the narrative, either output **`NO_EDIT`** or a single **`docnerd:path`** block with the full new file. Unrelated pages stay unchanged. Calls run with limited **parallelism** (`phased.max_parallel_doc_calls`, default 4).

4. **Adequacy check (Claude)** — Summarizes all proposed edits and asks whether they **cover** the narrative for users. If not, an **expansion** call can add detail or (when `allow_new_files: true`) propose new files.

5. **Reviewer loop (Claude)** — Operates on **proposed edits only**: narrative + before/after snippets per changed file → `satisfied` or revision questions → a small refine writer pass. This replaces the old “load every page into the reviewer” style for phased mode.

### Legacy mode (`doc_generation.mode: legacy`)

Single large writer call with **all** priority/secondary-loaded docs in one prompt, plus the previous full-tree reviewer loop. Use if you need the old behavior.

### Fetch tiers (`docs_fetcher`)

Still used to discover **all** `.md` paths and to load **priority/secondary** bodies for legacy mode. In **phased** mode, docNerd **re-fetches full text** for every listed path (up to `per_doc_max_content_chars` per file) for the per-file passes.

## Configuration

### `config.yaml`

```yaml
# Only edit existing docs; never create new files
allow_new_files: false

# Reviewer loop (default on): second agent reviews drafts for public-user clarity
doc_review_loop:
  enabled: true
  max_wall_seconds: 600   # stop entire loop after 10 minutes
  max_rounds: 5         # max review + refine cycles

# Docs fetcher limits (when loading from target repo)
docs_fetcher:
  max_priority_files: 100   # alias: max_files (deprecated)
  max_content_per_file: 6000
  max_secondary_files: 200   # more paths as short previews (context only; not editable bodies)
  secondary_content_per_file: 2000

# Phased pipeline (default) vs single-shot legacy writer
doc_generation:
  mode: phased   # or legacy
  phased:
    max_parallel_doc_calls: 4
    per_doc_max_content_chars: 80000
    narrative_max_tokens: 8192
    adequacy_max_tokens: 4096
    expansion_max_tokens: 24000
```

- **`docs_fetcher`** — Lists **all** `.md` paths. In **legacy** mode, priority/secondary tiers cap how much body text loads into the monolithic prompt. In **phased** mode, paths drive the per-file loop; bodies are loaded separately up to `per_doc_max_content_chars`.

- **`allow_new_files`** — When `false`, docNerd will only edit existing files. Any new-file suggestions from Claude are filtered out. Default: `true`.

- **`doc_review_loop`** — **Phased:** reviewer sees the **PR narrative** and **before/after** for each changed file only, then optional refine rounds. **Legacy:** reviewer sees every loaded page, `file_assessments` per path, etc. Set `enabled: false` to skip review entirely.

- **Context limits** — Anthropic’s ~200k-token window applies to **input + max output**. docNerd estimates token usage, **lowers `max_tokens` when needed**, and **progressively shrinks** loaded doc bodies in the prompt (with a notice) until the writer and reviewer calls fit. If you still hit limits, reduce `docs_fetcher` file counts or `max_content_per_file`.

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

## How it works (summary)

**Phased (default):** narrative → N per-file calls → adequacy (+ expansion if needed) → edit-based review/refine → PR on docs repo.

**Legacy:** one writer call with batched docs + full-tree review → PR.

If `allow_new_files: false`, new-file edits are stripped before opening the docs PR.

## Tips

- **Put rules in your source repo** — Add `rules/doc_generation.yaml` to customize behavior per project.

- **Use `allow_new_files: false`** — For strict control, disable new files and rely on edits only.

- **Check the rules** — The `rules/` directory is loaded from your source repo. If missing, docnerd falls back to built-in defaults.
