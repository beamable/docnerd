# docNerd — Requirements Document

## 1. Overview

### 1.1 Purpose

docNerd is a system that monitors pull requests (PRs) in a **source repository** and creates corresponding documentation PRs in a **target documentation repository** when invoked via comment. A user comments on a source PR (e.g., `docNerd, doc for core/v7.1`, without `@` to avoid pinging the GitHub user docNerd) to request documentation; docNerd uses Claude to generate or update docs, then opens a PR on the specified branch in the docs repo and replies with the link.

### 1.2 Goals

- **Automation**: Reduce manual effort of keeping docs in sync with code changes
- **Traceability**: Link documentation updates to the source PRs that triggered them
- **Control**: Give the user full control over documentation style, structure, and rules
- **Reliability**: Produce consistent, high-quality documentation that follows configurable conventions

---

## 2. Scope

### 2.1 In Scope

- Detecting comment triggers on source PRs (e.g., `docNerd, doc for &lt;branch&gt;`)
- Validating that the requested docs branch exists in the target repository
- Analyzing PR content (files changed, commit messages, descriptions) to determine documentation impact
- Generating and **editing** documentation using Claude (LLM), guided by user-defined rules
- Creating new docs and modifying existing docs to reflect source changes
- Opening PRs in a target MkDocs + Mike documentation repository on the correct version branch
- Replying to the source PR with status updates and the docs PR link
- Configurable documentation rules and conventions

### 2.2 Out of Scope (Initial Version)

- Multi-repo source → single docs repo (could be added later)
- Real-time collaboration or live doc preview
- Approval workflows or gating logic beyond basic PR creation

---

## 3. Comment Trigger Workflow

### 3.1 Invocation

A user invokes docNerd by commenting on a source PR with the trigger phrase and branch specification (no leading `@`, so GitHub does not notify the unrelated user account docNerd):

```
docNerd, doc for core/v7.1
```

The format is: `docNerd, doc for <branch>`, where `<branch>` is the target branch in the docs repository (e.g., a Mike version branch like `core/v7.1`).

### 3.2 Response Flow

| Step | Action | docNerd Response |
|------|--------|------------------|
| 1 | User comments `docNerd, doc for &lt;branch&gt;` | — |
| 2 | docNerd validates that `<branch>` exists in the docs repo | **If branch not found**: Reply with *"I couldn't find that branch"* |
| 3 | docNerd accepts the request | Reply with *"yes, working on it"* |
| 4 | docNerd analyzes PR, uses Claude to generate/update docs, opens docs PR | — |
| 5 | docNerd completes | Reply with *"Here is the link to the Doc changes"* + link to the docs PR |

### 3.3 Branch Validation

- docNerd must check that the requested branch exists in the target docs repository before proceeding
- If the branch does not exist, docNerd replies immediately with *"I couldn't find that branch"* and does not create a docs PR

---

## 4. Functional Requirements

### 4.1 Source Repository Integration

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1 | System must connect to a configurable source repository (e.g., GitHub) | Must |
| FR-2 | System must detect comments on PRs that use the docNerd trigger phrase (plain text, not `@docNerd`) | Must |
| FR-3 | System must parse the comment to extract the target docs branch (e.g., `core/v7.1`) | Must |
| FR-4 | System must extract PR metadata: title, description, files changed, commits, labels | Must |
| FR-5 | System must post replies as comments on the source PR | Must |

### 4.2 Change Analysis

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-6 | System must analyze PR diff to identify what changed (APIs, config, behavior) | Must |
| FR-7 | System must determine whether a PR warrants documentation updates | Must |
| FR-8 | System must support user-defined heuristics for "docs-relevant" changes | Should |
| FR-9 | System must extract or infer documentation topics from PR content | Must |

### 4.3 LLM (Claude) Integration

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-10 | System must use Claude to generate and edit documentation content | Must |
| FR-11 | User must provide configurable connection strings/API keys for the LLM | Must |
| FR-12 | System must pass PR context and user-defined rules to Claude as prompts | Must |
| FR-13 | Claude output must be used to create new docs and modify existing docs | Must |

### 4.4 Documentation Generation

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-14 | Documentation output must follow user-defined rules and templates | Must |
| FR-15 | System must support creating new documentation files | Must |
| FR-16 | System must support editing existing documentation files to reflect changes | Must |
| FR-17 | Generated/edited docs must be linkable/traceable back to source PR | Must |

### 4.5 Target Repository Integration (MkDocs + Mike)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-18 | Target docs repo is an MkDocs site with Mike for versioned deployment | Must |
| FR-19 | Mike uses multiple branches for versions (e.g., `core/v7.1`, `core/v8.0`) | Must |
| FR-20 | System must open the docs PR targeting the branch specified in the comment | Must |
| FR-21 | System must validate that the requested branch exists before proceeding | Must |
| FR-22 | System must create a branch from the target branch and open a PR into it | Must |
| FR-23 | System must include appropriate PR title, description, and labels | Must |
| FR-24 | System must avoid duplicate PRs for the same source PR + branch combo (idempotency) | Must |

### 4.6 Documentation Rules Engine

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-25 | User must be able to define documentation style rules (voice, tone, formatting) | Must |
| FR-26 | User must be able to define structural rules (headings, sections, ordering) | Must |
| FR-27 | User must be able to define vocabulary/terminology constraints | Should |
| FR-28 | Rules must be stored in a version-controlled, human-editable format | Must |
| FR-29 | Rules must be passed to Claude to guide generation (e.g., in system prompt) | Must |

---

## 5. Non-Functional Requirements

### 5.1 Performance

- PR analysis and doc generation should complete within a reasonable time (e.g., &lt; 5 minutes for typical PRs, accounting for LLM latency)
- Comment detection and API usage should not overwhelm source/target/LLM rate limits

### 5.2 Security

- Credentials (tokens, keys, LLM connection strings) must not be hardcoded; use env vars or secure secret management
- LLM API keys must be stored securely and never exposed in logs or PR content
- System must support least-privilege access (e.g., read source, write target)

### 5.3 Reliability

- System should handle API failures gracefully (retries, backoff)
- System should log actions for debugging and audit
- LLM failures should result in a clear comment reply to the user (e.g., "I ran into an error generating docs")

### 5.4 Maintainability

- Documentation rules should be easy to edit without code changes
- Architecture should allow swapping LLM provider if needed (e.g., different Claude endpoint)

---

## 6. Documentation Rules & Conventions (User Control)

### 6.1 Design Principle

The user must have **full control** over how documentation is written. The system provides a rules engine; the user defines the rules.

### 6.2 Rule Categories

1. **Style Rules**
   - Voice (e.g., second person, imperative)
   - Tone (e.g., concise, formal, friendly)
   - Sentence length, paragraph structure
   - Use of passive vs active voice

2. **Structural Rules**
   - Required sections (e.g., Overview, Prerequisites, Steps, Examples)
   - Heading hierarchy (H1 → H2 → H3)
   - Ordering of content blocks

3. **Formatting Rules**
   - Markdown conventions (bold, code blocks, lists)
   - Code block language tags
   - Link format and style

4. **Terminology Rules**
   - Preferred terms and banned terms
   - Acronym handling (spell out first use, etc.)
   - Product/feature naming consistency

5. **Content Rules**
   - What to include for different change types (API, config, UI, etc.)
   - Example formats
   - Deprecation/breaking change documentation requirements

### 6.3 Rule Storage Format

- **Proposed**: YAML or JSON config files in the project
- **Alternative**: Dedicated rules directory (e.g., `rules/`) with modular rule files
- Rules should be human-readable and diff-friendly

### 6.4 Rule Application

- Rules are passed to Claude (e.g., in system prompt or context) to guide generation
- Optional: validation step to check generated docs against rules before PR creation
- Optional: lint/check command for manual rule validation

---

## 7. System Architecture (Conceptual)

```
┌─────────────────┐     ┌──────────────────────────────────────────┐     ┌─────────────────┐
│  Source Repo    │     │  docNerd                                  │     │  Target Docs    │
│  (PR + comment  │────▶│  - Comment Watcher (detect docNerd)        │────▶│  Repo           │
│   docNerd)      │     │  - Branch Validator (check docs repo)      │     │  (MkDocs+Mike)  │
│                 │◀────│  - Analyzer (PR diff, metadata)            │     │  (Doc PRs on    │
│  (reply: link)  │     │  - Claude (LLM doc generation/editing)    │     │   version       │
└─────────────────┘     │  - Rules Engine (user rules → prompt)     │     │   branches)     │
                        │  - PR Creator (branch from target, open PR)│     └─────────────────┘
                        └──────────────────────────────────────────┘
```

### 7.1 Components

1. **Comment Watcher**: Detects `docNerd, doc for <branch>` comments on source PRs
2. **Branch Validator**: Verifies the requested branch exists in the docs repo
3. **Analyzer**: Parses PR content, determines docs relevance, extracts topics
4. **Claude (LLM)**: Generates new docs and edits existing docs, guided by rules
5. **Rules Engine**: Loads user rules, injects them into Claude prompts
6. **PR Creator**: Creates branch from target version branch, opens PR, replies with link

---

## 8. Configuration

### 8.1 Required Configuration

- Source repository URL and auth
- Target repository URL and auth (MkDocs + Mike docs repo)
- **LLM (Claude) connection strings / API keys** (e.g., Anthropic API key, or custom endpoint URL + key)
- Path to documentation rules

### 8.2 Optional Configuration

- Comment trigger phrase (default: `docNerd, doc for`)
- Branch naming convention for docNerd's working branches (e.g., `docnerd/<source-pr-number>-<branch>`)
- Mapping: source repo paths → target doc paths
- Claude model variant (e.g., claude-3-5-sonnet)

---

## 9. Open Questions

1. **Comment detection**: Webhook on issue/PR comments, or polling for new comments?
2. **Duplicate handling**: If user comments `docNerd, doc for core/v7.1` twice, should docNerd update the existing docs PR or create a new one?
3. **Conflict handling**: If target branch has changed since docNerd branched, how to handle merge conflicts?
4. **Review workflow**: Should docs PRs be draft by default, or ready for review?
5. **MkDocs structure**: Does docNerd need to understand mkdocs.yml / nav structure to place new pages correctly?

---

## 10. Success Criteria

- [ ] Detects `docNerd, doc for <branch>` comments on source PRs
- [ ] Validates requested branch exists in docs repo; replies "I couldn't find that branch" when not found
- [ ] Replies "yes, working on it" when request is accepted
- [ ] Uses Claude to generate and edit docs according to user-defined rules
- [ ] Opens a docs PR on the correct version branch (e.g., `core/v7.1`)
- [ ] Replies "Here is the link to the Doc changes" with the PR link when complete
- [ ] User can provide LLM connection strings via configuration
- [ ] No duplicate docs PRs for the same source PR + branch combo

---

## 11. Appendix: Example Rule Snippet (Illustrative)

```yaml
# rules/style.yaml (example)
voice: second_person  # "You can configure..."
tone: concise
sentence_max_words: 25
use_active_voice: true

# rules/structure.yaml (example)
required_sections:
  - overview
  - prerequisites
  - steps
  - examples
heading_style: sentence_case

# rules/terminology.yaml (example)
preferred_terms:
  "config" -> "configuration"
  "API" -> "API"  # (no change, but explicit)
banned_terms:
  - "simply"
  - "just"
```

---

*Document version: 1.1*  
*Last updated: March 19, 2025*
