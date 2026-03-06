# docs-github-confluence

Sync GitHub-hosted Markdown documentation into Confluence.

## How it works

1. A YAML configuration file (`config.yml`) defines what to sync (GitHub repo/branch/docs_root)
   and where to sync it in Confluence (space key + parent page id).
2. `sync_to_confluence.py` reads that configuration, fetches each document from
   the GitHub API, converts the Markdown to Confluence storage format, and then
   creates or updates the corresponding Confluence page.
3. A GitHub Actions workflow (`.github/workflows/sync.yml`) runs the script on
   every push to `main` that changes anything under `Docs/**` or `config.yml`,
   and on demand via `workflow_dispatch`.

---

## Configuration (`config.yml`)

`config.yml` is still required, but it is intentionally **credentials-free**.

- Put **sync mappings** in `config.yml` (what to sync + Confluence destination details).
- Put **Confluence URL + credentials** in GitHub Actions **Secrets** (see below), which the
  workflow exports as environment variables at runtime.

Example:

```yaml
sync:
  - github_repo: your-org/your-repo
    github_branch: main
    confluence_space: DOC # space key (not name)
    confluence_parent_id: "405152300" # root landing page ID
    docs_root: Docs # mirror everything under Docs/ into Confluence
```

Notes on `confluence_space`:

- This must be the **space key**, not the space name.
- In Confluence Data Center, **personal spaces** commonly use keys like `~username`
  (example: `~PDESAI`).
- If the space key is invalid or inaccessible, Confluence may return HTTP 404 with a
  message like `No space with key : DOC`.

### Tree-sync mode (recommended)

Set `docs_root` to the folder you want to mirror. The script will discover all
Markdown files recursively and recreate the directory structure as Confluence pages
nested under the page identified by `confluence_parent_id`.

- Page titles are the **filename without extension** (e.g. `Installation.md` → _Installation_).
- `README.md` is special: it updates the _folder page_ itself rather than creating a
  new child page. `Docs/README.md` updates the root page identified by
  `confluence_parent_id`; `Docs/<folder>/README.md` updates the `<folder>` page.

### Legacy mode (explicit document list)

If you prefer specifying documents explicitly, use `documents` instead of `docs_root`:

```yaml
sync:
  - github_repo: your-org/your-repo
    github_branch: main
    confluence_space: DOC
    confluence_parent_id: "405152300"
    documents:
      - github_path: Docs/SomeDoc.md
        confluence_title: SomeDoc
```

---

## GitHub Actions secrets (Hosted vs Cloud)

This repo supports syncing to **two different Confluence deployments** with **separate**
GitHub Actions secrets:

- **Hosted (default)**: Confluence Data Center / Server (typically authenticated via Bearer token)
- **Cloud**: Confluence Cloud (authenticated via Basic auth: email + API token)

The workflow selects which one to use and maps the chosen secret set into the runtime
environment variables that `sync_to_confluence.py` reads:

- `CONFLUENCE_URL`
- `CONFLUENCE_USERNAME`
- `CONFLUENCE_API_TOKEN`
- `CONFLUENCE_BEARER_TOKEN`

### Required secrets

**Hosted (default)**

- `CONFLUENCE_HOSTED_URL`
- `CONFLUENCE_HOSTED_BEARER_TOKEN`

**Cloud**

- `CONFLUENCE_CLOUD_URL` (e.g. `https://your-domain.atlassian.net`)
- `CONFLUENCE_CLOUD_USERNAME` (your Atlassian email)
- `CONFLUENCE_CLOUD_API_TOKEN` (Atlassian API token)

**Common**

- `GITHUB_TOKEN` (provided automatically by GitHub Actions as `${{ secrets.GITHUB_TOKEN }}`)

### Selecting the target (workflow_dispatch)

From the GitHub Actions tab, run **Sync GitHub Docs to Confluence** manually and choose:

- `hosted` (default), or
- `cloud`

On normal pushes to `main`, the workflow defaults to **hosted**.

---

## Local runs (optional)

You can run the sync script locally by setting:

- `GITHUB_TOKEN`
- `CONFLUENCE_URL`
- and either:
  - `CONFLUENCE_BEARER_TOKEN` (hosted), **or**
  - `CONFLUENCE_USERNAME` + `CONFLUENCE_API_TOKEN` (cloud)

Then run:

```bash
python sync_to_confluence.py
```
