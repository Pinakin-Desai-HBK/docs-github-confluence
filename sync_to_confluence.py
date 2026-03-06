#!/usr/bin/env python3
"""
Sync GitHub documents to Confluence.

Reads a config.yml file that maps GitHub repository paths to Confluence pages,
then fetches each document from GitHub and creates or updates the corresponding
Confluence page so the two stay in sync.
"""

import base64
import logging
import os
import re
import sys
from typing import Optional

import bleach
import requests
import yaml
from lxml import etree, html  # ensure html is imported
from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(config_path: str = "config.yml") -> dict:
    """Load configuration from a YAML file."""
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def get_github_file_content(
    github_token: str, repo: str, file_path: str, branch: str = "main"
) -> str:
    """Fetch the text content of a file from a GitHub repository."""
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{repo}/contents/{file_path}?ref={branch}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    return base64.b64decode(data["content"]).decode("utf-8")


def list_github_docs(
    github_token: str, repo: str, branch: str = "main", docs_root: str = "Docs"
) -> list[str]:
    """Return all .md file paths under *docs_root* in the given repo/branch.

    Uses the GitHub Git Trees API with ``recursive=1`` so a single request
    retrieves the entire directory tree.
    """
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    prefix = docs_root.rstrip("/") + "/"
    return [
        item["path"]
        for item in data.get("tree", [])
        if item["type"] == "blob"
        and item["path"].startswith(prefix)
        and item["path"].lower().endswith(".md")
    ]


def derive_confluence_title(filename: str) -> Optional[str]:
    """Return the Confluence page title for *filename*.

    Returns ``None`` for ``README.md`` (any case) — the caller should treat
    this as a signal to update the *parent/folder* page rather than creating a
    new child page.  For every other file the extension is stripped.
    """
    if filename.lower() == "readme.md":
        return None
    name, _ = os.path.splitext(filename)
    return name


# ---------------------------------------------------------------------------
# Markdown → Confluence storage-format conversion
# ---------------------------------------------------------------------------

# Markdown-it instance: GFM-like preset (tables + strikethrough built-in)
# with raw-HTML pass-through disabled so arbitrary inline HTML is escaped.
_MARKDOWN_IT = MarkdownIt(
    "gfm-like", {"html": False, "xhtmlOut": True, "linkify": False}
).use(tasklists_plugin)

# Bleach allowlist – tags and attributes acceptable in Confluence storage XHTML.
_BLEACH_ALLOWED_TAGS = frozenset([
    "p", "br", "hr", "strong", "em", "s", "del", "code", "pre",
    "a", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "th", "td",
    "blockquote",
])
_BLEACH_ALLOWED_ATTRS: dict[str, list[str]] = {
    "a": ["href", "title"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}
_BLEACH_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

# Placeholder tokens used during pre/post processing.  Pure ASCII so they
# pass through markdown-it and bleach unmodified.
_BR_PLACEHOLDER = "XCONFLUENCEBRX"
_CODE_PLACEHOLDER_FMT = "XCONFLUENCECODE{idx}X"
_BR_RE = re.compile(r"<[Bb][Rr]\s*/?>")
_FENCED_CODE_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)


def markdown_to_confluence(markdown_content: str) -> str:
    """Convert markdown text to Confluence storage format (XHTML subset)."""
    macros: list[str] = []

    def _extract_code_block(match: re.Match) -> str:
        lang = match.group(1) or ""
        body = match.group(2)
        macro = (
            f'<ac:structured-macro ac:name="code">'
            f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
            f"<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body>"
            f"</ac:structured-macro>"
        )
        idx = len(macros)
        macros.append(macro)
        return _CODE_PLACEHOLDER_FMT.format(idx=idx)

    # Step 1 – extract fenced code blocks before any other processing.
    content = _FENCED_CODE_RE.sub(_extract_code_block, markdown_content)

    # Step 2 – stash <br> variants so html=False doesn't escape them.
    content = _BR_RE.sub(_BR_PLACEHOLDER, content)

    # Step 3 – render markdown → HTML (raw HTML in source is escaped).
    rendered = _MARKDOWN_IT.render(content)

    # Step 4 – sanitize: escape tags not in the allowlist.
    cleaned = bleach.clean(
        rendered,
        tags=_BLEACH_ALLOWED_TAGS,
        attributes=_BLEACH_ALLOWED_ATTRS,
        protocols=_BLEACH_ALLOWED_PROTOCOLS,
        strip=False,
    )

    # Step 5 – normalize to well-formed XHTML (self-closing void elements, etc.).
    root = html.fragment_fromstring(cleaned, create_parent=True)  # creates a <div> parent
    serialized = etree.tostring(root, encoding="unicode", method="xml")
    result = serialized[5:-6]  # strip outer <div>…</div>

    # Step 6 – restore placeholders.
    result = result.replace(_BR_PLACEHOLDER, "<br/>")
    for idx, macro in enumerate(macros):
        result = result.replace(_CODE_PLACEHOLDER_FMT.format(idx=idx), macro)

    return result


# ---------------------------------------------------------------------------
# Confluence REST API client
# ---------------------------------------------------------------------------

class ConfluenceClient:
    """Thin wrapper around the Confluence REST API."""

    def __init__(
        self,
        base_url: str,
        username: str,
        api_token: str,
        bearer_token: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")

        # Auth:
        # - Confluence Cloud: Basic auth (email + API token)
        # - Some Data Center/Server setups: Bearer token
        # Auto: use bearer if provided, else basic.
        self._bearer_token = (bearer_token or "").strip() or None
        self._basic_auth = (username, api_token) if (username and api_token) else None

        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._bearer_token:
            self.headers["Authorization"] = f"Bearer {self._bearer_token}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        auth = None if self._bearer_token else self._basic_auth
        return requests.request(
            method,
            url,
            headers=self.headers,
            params=params,
            json=json,
            auth=auth,
            timeout=30,
        )

    def _parse_json_response(self, response: requests.Response, method: str, url: str) -> dict:
        """Parse a Confluence API response as JSON with descriptive errors."""
        content_type = response.headers.get("Content-Type", "")
        body_prefix = response.text[:500]
        if "application/json" not in content_type:
            raise ValueError(
                f"Confluence returned a non-JSON response "
                f"[{method} {url} -> HTTP {response.status_code}, "
                f"Content-Type: {content_type!r}]. "
                f"Body prefix: {body_prefix!r}. "
                f"This may indicate an SSO/login redirect or wrong auth method. "
                f"If using Confluence Cloud, ensure Basic auth via CONFLUENCE_USERNAME (email) "
                f"+ CONFLUENCE_API_TOKEN. If using Bearer, set CONFLUENCE_BEARER_TOKEN."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ValueError(
                f"Confluence returned invalid JSON "
                f"[{method} {url} -> HTTP {response.status_code}, "
                f"Content-Type: {content_type!r}]. "
                f"Body prefix: {body_prefix!r}."
            ) from exc

    def get_page_by_title(self, space_key: str, title: str) -> Optional[dict]:
        """Return a page dict (including version) if *title* exists in *space_key*, else None."""
        path = "/rest/api/content"
        params = {"spaceKey": space_key, "title": title, "expand": "version"}
        response = self._request("GET", path, params=params)

        data = self._parse_json_response(response, "GET", f"{self.base_url}{path}")

        if response.status_code == 404:
            message = str(data.get("message", "") or "")
            if "no space with key" in message.lower():
                raise ValueError(
                    "Confluence space key is invalid or not accessible. "
                    f"Configured confluence_space={space_key!r}. "
                    "Verify the space key exists and that the token user has access "
                    "(personal spaces are often like '~username'). "
                    f"Confluence message: {message}"
                )
            return None

        if response.status_code >= 400:
            message = str(data.get("message", "") or "")
            raise ValueError(
                "Confluence request failed "
                f"[GET {self.base_url}{path} -> HTTP {response.status_code}]. "
                f"Message: {message}"
            )

        results = data.get("results", [])
        return results[0] if results else None

    def get_page_by_title_under_parent(
        self, space_key: str, title: str, parent_id: str
    ) -> Optional[dict]:
        """Return a page dict if *title* exists as a direct child of *parent_id*, else None."""
        path = "/rest/api/content/search"
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_space = space_key.replace("\\", "\\\\").replace('"', '\\"')
        cql = (
            f'title = "{safe_title}" AND parent = {parent_id}'
            f' AND space = "{safe_space}" AND type = page'
        )
        params = {"cql": cql, "expand": "version"}
        response = self._request("GET", path, params=params)
        data = self._parse_json_response(response, "GET", f"{self.base_url}{path}")
        if response.status_code >= 400:
            return None
        results = data.get("results", [])
        return results[0] if results else None

    def get_page_by_id(self, page_id: str) -> Optional[dict]:
        """Return a page dict (with version and title) for *page_id*, or None if not found."""
        path = f"/rest/api/content/{page_id}"
        params = {"expand": "version"}
        response = self._request("GET", path, params=params)
        if response.status_code == 404:
            return None
        data = self._parse_json_response(response, "GET", f"{self.base_url}{path}")
        if response.status_code >= 400:
            return None
        return data

    def create_page(
        self,
        space_key: str,
        title: str,
        content: str,
        parent_id: Optional[str] = None,
    ) -> dict:
        """Create a new page and return the response JSON."""
        path = "/rest/api/content"
        body: dict = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": content, "representation": "storage"}},
        }
        if parent_id:
            body["ancestors"] = [{"id": parent_id}]
        response = self._request("POST", path, json=body)
        if not response.ok:
            logger.error(
                "Confluence API error [POST %s] status=%d title=%r parent_id=%r body=%s",
                f"{self.base_url}{path}", response.status_code, title, parent_id, response.text[:500],
            )
        response.raise_for_status()
        return self._parse_json_response(response, "POST", f"{self.base_url}{path}")

    def update_page(
        self, page_id: str, title: str, content: str, current_version: int
    ) -> dict:
        """Update an existing page to *current_version + 1* and return the response JSON."""
        path = f"/rest/api/content/{page_id}"
        body = {
            "type": "page",
            "title": title,
            "version": {"number": current_version + 1},
            "body": {"storage": {"value": content, "representation": "storage"}},
        }
        response = self._request("PUT", path, json=body)
        if not response.ok:
            logger.error(
                "Confluence API error [PUT %s] status=%d title=%r page_id=%r body=%s",
                f"{self.base_url}{path}", response.status_code, title, page_id, response.text[:500],
            )
        response.raise_for_status()
        return self._parse_json_response(response, "PUT", f"{self.base_url}{path}")


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def sync_document(
    confluence: ConfluenceClient,
    space_key: str,
    title: str,
    content: str,
    parent_id: Optional[str] = None,
) -> None:
    """Create a new Confluence page or update it if it already exists."""
    existing = confluence.get_page_by_title(space_key, title)
    if existing:
        page_id = existing["id"]
        version = existing["version"]["number"]
        logger.info("Updating page '%s' (id=%s, version=%d)", title, page_id, version)
        confluence.update_page(page_id, title, content, version)
        logger.info("Updated page '%s'", title)
    else:
        logger.info("Creating page '%s'", title)
        result = confluence.create_page(space_key, title, content, parent_id)
        logger.info("Created page '%s' (id=%s)", title, result["id"])


def normalize_github_repo(repo: str) -> str:
    repo = repo.strip()

    # Accept full GitHub URLs like https://github.com/owner/name(.git)
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", repo)
    if m:
        return f"{m.group(1)}/{m.group(2)}"

    return repo


def ensure_folder_page(
    confluence: ConfluenceClient,
    space_key: str,
    folder_name: str,
    parent_id: str,
) -> str:
    """Find or create a Confluence page named *folder_name* under *parent_id*.

    Returns the Confluence page ID (string).
    """
    existing = confluence.get_page_by_title_under_parent(space_key, folder_name, parent_id)
    if existing:
        return existing["id"]
    result = confluence.create_page(space_key, folder_name, "<p></p>", parent_id=parent_id)
    logger.info("Created folder page '%s' (id=%s)", folder_name, result["id"])
    return result["id"]


def sync_docs_tree(
    confluence: ConfluenceClient,
    github_token: str,
    github_repo: str,
    github_branch: str,
    space_key: str,
    root_parent_id: str,
    docs_root: str = "Docs",
) -> None:
    """Mirror all Markdown files under *docs_root* into Confluence."""
    parent_page = confluence.get_page_by_id(root_parent_id)
    if parent_page is None:
        raise ValueError(
            f"The configured confluence_parent_id={root_parent_id!r} does not exist or is not "
            f"accessible. Confluence base URL: {confluence.base_url!r}. "
            f"Confluence space: {space_key!r}. "
            "Common causes: wrong page ID, deleted page, wrong Confluence site/base_url, "
            "or insufficient permissions."
        )

    page_space_key = parent_page.get("space", {}).get("key", "")
    if page_space_key and page_space_key != space_key:
        logger.warning(
            "Root parent page (id=%s) is in Confluence space %r but confluence_space is "
            "configured as %r. This may cause sync issues.",
            root_parent_id,
            page_space_key,
            space_key,
        )

    md_files = list_github_docs(github_token, github_repo, github_branch, docs_root)

    folder_ids: dict[str, str] = {docs_root: root_parent_id}

    for file_path in sorted(md_files):
        parts = file_path.split("/")
        filename = parts[-1]
        dir_path = "/".join(parts[:-1])

        current_path = docs_root
        for folder in parts[1:-1]:
            next_path = f"{current_path}/{folder}"
            if next_path not in folder_ids:
                folder_ids[next_path] = ensure_folder_page(
                    confluence, space_key, folder, folder_ids[current_path]
                )
            current_path = next_path

        folder_page_id = folder_ids[dir_path]
        title = derive_confluence_title(filename)

        logger.info("Syncing '%s' from %s@%s", file_path, github_repo, github_branch)
        try:
            markdown = get_github_file_content(
                github_token, github_repo, file_path, github_branch
            )
            storage_content = markdown_to_confluence(markdown)

            if title is None:
                page = confluence.get_page_by_id(folder_page_id)
                if page:
                    logger.info(
                        "Updating folder page '%s' (id=%s) with README content",
                        page["title"],
                        folder_page_id,
                    )
                    confluence.update_page(
                        folder_page_id,
                        page["title"],
                        storage_content,
                        page["version"]["number"],
                    )
                else:
                    logger.warning(
                        "Folder page id=%s not found; skipping '%s'",
                        folder_page_id,
                        file_path,
                    )
            else:
                existing = confluence.get_page_by_title_under_parent(
                    space_key, title, folder_page_id
                )
                if existing:
                    logger.info("Updating page '%s' (id=%s)", title, existing["id"])
                    confluence.update_page(
                        existing["id"],
                        title,
                        storage_content,
                        existing["version"]["number"],
                    )
                else:
                    logger.info("Creating page '%s' under id=%s", title, folder_page_id)
                    result = confluence.create_page(
                        space_key, title, storage_content, parent_id=folder_page_id
                    )
                    logger.info("Created page '%s' (id=%s)", title, result["id"])
        except Exception as exc:
            logger.error("Failed to sync '%s': %s", file_path, exc)


def should_run_sync_entry(sync_entry: dict, deployment: str) -> bool:
    """Return True if sync_entry should run for this deployment.

    If sync_entry has no 'target', it runs for all deployments (backwards compatible).
    """
    target = str(sync_entry.get("target", "") or "").strip().lower()
    if not target:
        return True
    return target == deployment


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(config_path: str = "config.yml") -> None:
    """Main entry point: load config, iterate over sync entries, and sync each doc."""
    config = load_config(config_path)

    github_token = os.environ.get("GITHUB_TOKEN")
    confluence_url = os.environ.get("CONFLUENCE_URL") or config.get("confluence", {}).get("url")
    confluence_username = os.environ.get("CONFLUENCE_USERNAME") or config.get("confluence", {}).get("username")
    confluence_api_token = os.environ.get("CONFLUENCE_API_TOKEN")
    confluence_bearer_token = os.environ.get("CONFLUENCE_BEARER_TOKEN")

    confluence_deployment = (os.environ.get("CONFLUENCE_DEPLOYMENT") or "hosted").strip().lower()
    if confluence_deployment not in ("hosted", "cloud"):
        logger.error(
            "Invalid CONFLUENCE_DEPLOYMENT=%r (expected 'hosted' or 'cloud').",
            confluence_deployment,
        )
        sys.exit(1)

    has_bearer = bool((confluence_bearer_token or "").strip())
    has_basic = bool(confluence_username and confluence_api_token)

    missing = [
        name
        for name, val in [
            ("GITHUB_TOKEN", github_token),
            ("CONFLUENCE_URL", confluence_url),
        ]
        if not val
    ]
    if not (has_bearer or has_basic):
        missing.append("CONFLUENCE_BEARER_TOKEN or (CONFLUENCE_USERNAME + CONFLUENCE_API_TOKEN)")

    if missing:
        logger.error(
            "Missing required credentials: %s. "
            "Set them as environment variables (or url/username in config.yml).",
            ", ".join(missing),
        )
        sys.exit(1)

    confluence = ConfluenceClient(
        confluence_url,
        confluence_username or "",
        confluence_api_token or "",
        bearer_token=confluence_bearer_token,
    )

    for sync_entry in config.get("sync", []):
        if not should_run_sync_entry(sync_entry, confluence_deployment):
            continue

        github_repo = normalize_github_repo(sync_entry["github_repo"])
        github_branch = sync_entry.get("github_branch", "main")
        space_key = sync_entry["confluence_space"]
        parent_id = sync_entry.get("confluence_parent_id")

        docs_root = sync_entry.get("docs_root")
        if docs_root is not None:
            if not parent_id:
                logger.error("confluence_parent_id is required when using docs_root; skipping entry.")
                continue

            logger.info(
                "Syncing Docs tree '%s' from %s@%s → Confluence space %s (parent %s)",
                docs_root,
                github_repo,
                github_branch,
                space_key,
                parent_id,
            )
            sync_docs_tree(
                confluence,
                github_token,
                github_repo,
                github_branch,
                space_key,
                parent_id,
                docs_root,
            )
            continue

        for doc in sync_entry.get("documents", []):
            github_path = doc["github_path"]
            confluence_title = doc["confluence_title"]

            logger.info(
                "Syncing '%s' from %s@%s → Confluence '%s'",
                github_path,
                github_repo,
                github_branch,
                confluence_title,
            )
            try:
                markdown = get_github_file_content(
                    github_token, github_repo, github_path, github_branch
                )
                storage_content = markdown_to_confluence(markdown)
                sync_document(confluence, space_key, confluence_title, storage_content, parent_id)
            except Exception as exc:
                logger.error("Failed to sync '%s': %s", github_path, exc)


if __name__ == "__main__":
    main()