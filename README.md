# docs-github-confluence

Automatically copy Markdown documents hosted on GitHub to Confluence and keep
them up-to-date at regular intervals.

## How it works

1. A YAML configuration file (`config.yml`) maps GitHub repository paths to
   Confluence pages.
2. `sync_to_confluence.py` reads that configuration, fetches each document from
   the GitHub API, converts the Markdown to Confluence storage format, and then
   creates or updates the corresponding Confluence page.
3. A GitHub Actions workflow (`.github/workflows/sync.yml`) runs the script on
   a **cron schedule every 6 hours**, on every push to `main` that changes
   `config.yml`, and on demand via `workflow_dispatch`.

## Setup

### 1. Configure `config.yml`

Edit `config.yml` to point to your repositories and Confluence space:

```yaml
confluence:
  url: https://your-domain.atlassian.net
  username: your-email@example.com

sync:
  - github_repo: your-org/your-repo
    github_branch: main
    confluence_space: DOC
    confluence_parent_id: "123456"   # optional parent page ID
    documents:
      - github_path: docs/README.md
        confluence_title: "Project Overview"
      - github_path: docs/installation.md
        confluence_title: "Installation Guide"
```

### 2. Add repository secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `CONFLUENCE_URL` | Your Atlassian base URL, e.g. `https://your-domain.atlassian.net` |
| `CONFLUENCE_USERNAME` | Your Atlassian account email |
| `CONFLUENCE_API_TOKEN` | An [Atlassian API token](https://id.atlassian.com/manage-profile/security/api-tokens) |

`GITHUB_TOKEN` is provided automatically by GitHub Actions.

### 3. Run manually (optional)

```bash
pip install -r requirements.txt

export GITHUB_TOKEN=...
export CONFLUENCE_URL=https://your-domain.atlassian.net
export CONFLUENCE_USERNAME=your-email@example.com
export CONFLUENCE_API_TOKEN=...

python sync_to_confluence.py
```

## Running the tests

```bash
pip install -r requirements.txt pytest
python -m pytest tests/ -v
```

## File overview

| File | Purpose |
|---|---|
| `sync_to_confluence.py` | Main sync script |
| `config.yml` | Configuration (repos, branches, space keys, page titles) |
| `requirements.txt` | Python dependencies |
| `.github/workflows/sync.yml` | Scheduled GitHub Actions workflow |
| `tests/test_sync.py` | Unit tests |
