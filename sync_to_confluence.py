#!/usr/bin/env python3
"""
Sync GitHub documents to Confluence.

Supports:
- Full tree sync of Docs/ (or another docs_root)
- Changed-only sync via CHANGED_DOCS
- Deletion of Confluence pages when docs are removed via REMOVED_DOCS
- Separate hosted/cloud targets via config.yml entries with `target: hosted|cloud`
  and workflow-controlled CONFLUENCE_DEPLOYMENT.
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
from lxml import etree, html
from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.yml") -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def get_github_file_content(
    github_token: str, repo: str, file_path: str, branch: str = "main"
) -> str:
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
    if filename.lower() == "readme.md":
        return None
    name, _ = os.path.splitext(filename)
    return name


def _read_paths_env(name: str) -> list[str]:
    raw = (os.environ.get(name) or "").strip("\n")
    if not raw.strip():
        return []
    paths = [line.strip() for line in raw.splitlines() if line.strip()]
    seen: set[str] = set()
    uniq: list[str] = []
    for p in paths:
        if p not in seen:
            uniq.append(p)
            seen.add(p)
    return uniq


def read_changed_docs_from_env() -> list[str]:
    return _read_paths_env("CHANGED_DOCS")


def read_removed_docs_from_env() -> list[str]:
    return _read_paths_env("REMOVED_DOCS")


_MARKDOWN_IT = MarkdownIt(
    "gfm-like", {"html": False, "xhtmlOut": True, "linkify": False}
).use(tasklists_plugin)

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

_BR_PLACEHOLDER = "XCONFLUENCEBRX"
_CODE_PLACEHOLDER_FMT = "XCONFLUENCECODE{idx}X"
_BR_RE = re.compile(r"<[Bb][Rr]\s*/?>")
_FENCED_CODE_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)


def markdown_to_confluence(markdown_content: str) -> str:
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

    content = _FENCED_CODE_RE.sub(_extract_code_block, markdown_content)
    content = _BR_RE.sub(_BR_PLACEHOLDER, content)

    rendered = _MARKDOWN_IT.render(content)

    cleaned = bleach.clean(
        rendered,
        tags=_BLEACH_ALLOWED_TAGS,
        attributes=_BLEACH_ALLOWED_ATTRS,
        protocols=_BLEACH_ALLOWED_PROTOCOLS,
        strip=False,
    )

    root = html.fragment_fromstring(cleaned, create_parent=True)
    serialized = etree.tostring(root, encoding="unicode", method="xml")
    result = serialized[5:-6]

    result = result.replace(_BR_PLACEHOLDER, "<br/>")
    for idx, macro in enumerate(macros):
        result = result.replace(_CODE_PLACEHOLDER_FMT.format(idx=idx), macro)

    return result


class ConfluenceClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        api_token: str,
        bearer_token: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")

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
        content_type = response.headers.get("Content-Type", "")
        body_prefix = response.text[:500]
        if "application/json" not in content_type:
            raise ValueError(
                f"Confluence returned a non-JSON response "
                f"[{method} {url} -> HTTP {response.status_code}, "
                f"Content-Type: {content_type!r}]. "
                f"Body prefix: {body_prefix!r}."
            )
        return response.json()

    def get_page_by_title_under_parent(
        self, space_key: str, title: str, parent_id: str
    ) -> Optional[dict]:
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
        path = f"/rest/api/content/{page_id}"
        params = {"expand": "version"}
        response = self._request("GET", path, params=params)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            return None
        return self._parse_json_response(response, "GET", f"{self.base_url}{path}")

    def create_page(
        self,
        space_key: str,
        title: str,
        content: str,
        parent_id: Optional[str] = None,
    ) -> dict:
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
        response.raise_for_status()
        return self._parse_json_response(response, "POST", f"{self.base_url}{path}")

    def update_page(
        self, page_id: str, title: str, content: str, current_version: int
    ) -> dict:
        path = f"/rest/api/content/{page_id}"
        body = {
            "type": "page",
            "title": title,
            "version": {"number": current_version + 1},
            "body": {"storage": {"value": content, "representation": "storage"}},
        }
        response = self._request("PUT", path, json=body)
        response.raise_for_status()
        return self._parse_json_response(response, "PUT", f"{self.base_url}{path}")

    def delete_page(self, page_id: str) -> None:
        path = f"/rest/api/content/{page_id}"
        response = self._request("DELETE", path)
        if response.status_code == 404:
            return
        response.raise_for_status()


def normalize_github_repo(repo: str) -> str:
    repo = repo.strip()
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
    existing = confluence.get_page_by_title_under_parent(space_key, folder_name, parent_id)
    if existing:
        return existing["id"]
    result = confluence.create_page(space_key, folder_name, "<p></p>", parent_id=parent_id)
    logger.info("Created folder page '%s' (id=%s)", folder_name, result["id"])
    return result["id"]


def _compute_folder_ids_for_paths(
    confluence: ConfluenceClient,
    space_key: str,
    root_parent_id: str,
    docs_root: str,
    paths: list[str],
) -> dict[str, str]:
    folder_ids: dict[str, str] = {docs_root: root_parent_id}

    for file_path in paths:
        parts = file_path.split("/")
        current_path = docs_root
        for folder in parts[1:-1]:
            next_path = f"{current_path}/{folder}"
            if next_path not in folder_ids:
                folder_ids[next_path] = ensure_folder_page(
                    confluence, space_key, folder, folder_ids[current_path]
                )
            current_path = next_path

    return folder_ids


def sync_docs_tree(
    confluence: ConfluenceClient,
    github_token: str,
    github_repo: str,
    github_branch: str,
    space_key: str,
    root_parent_id: str,
    docs_root: str = "Docs",
    changed_paths: Optional[list[str]] = None,
    removed_paths: Optional[list[str]] = None,
) -> None:
    parent_page = confluence.get_page_by_id(root_parent_id)
    if parent_page is None:
        raise ValueError(
            f"The configured confluence_parent_id={root_parent_id!r} does not exist or is not "
            f"accessible. Confluence base URL: {confluence.base_url!r}. "
            f"Confluence space: {space_key!r}."
        )

    prefix = docs_root.rstrip("/") + "/"

    changed = [p for p in (changed_paths or []) if p.startswith(prefix) and p.lower().endswith(".md")]
    removed = [p for p in (removed_paths or []) if p.startswith(prefix) and p.lower().endswith(".md")]

    if changed or removed:
        logger.info("Delta mode: %d changed, %d removed.", len(changed), len(removed))
        md_files = changed
    else:
        md_files = list_github_docs(github_token, github_repo, github_branch, docs_root)
        logger.info("Full-scan mode: syncing %d markdown file(s).", len(md_files))

    # Ensure folder pages exist for anything we might touch (changed or removed paths).
    folder_ids = _compute_folder_ids_for_paths(
        confluence,
        space_key,
        root_parent_id,
        docs_root,
        md_files + removed,
    )

    # ---- Sync changed/added files ----
    for file_path in sorted(md_files):
        parts = file_path.split("/")
        filename = parts[-1]
        dir_path = "/".join(parts[:-1])

        folder_page_id = folder_ids[dir_path]
        title = derive_confluence_title(filename)

        logger.info("Syncing '%s' from %s@%s", file_path, github_repo, github_branch)
        try:
            markdown = get_github_file_content(github_token, github_repo, file_path, github_branch)
            storage_content = markdown_to_confluence(markdown)

            if title is None:
                # README.md updates the folder page itself
                page = confluence.get_page_by_id(folder_page_id)
                if not page:
                    logger.warning("Folder page id=%s not found; skipping README '%s'", folder_page_id, file_path)
                    continue
                confluence.update_page(
                    folder_page_id,
                    page["title"],
                    storage_content,
                    page["version"]["number"],
                )
            else:
                existing = confluence.get_page_by_title_under_parent(space_key, title, folder_page_id)
                if existing:
                    confluence.update_page(
                        existing["id"],
                        title,
                        storage_content,
                        existing["version"]["number"],
                    )
                else:
                    confluence.create_page(
                        space_key, title, storage_content, parent_id=folder_page_id
                    )
        except Exception as exc:
            logger.error("Failed to sync '%s': %s", file_path, exc)

    # ---- Delete removed files ----
    if not removed:
        return

    logger.info("Processing %d removed markdown file(s) for deletion.", len(removed))

    for file_path in sorted(removed):
        parts = file_path.split("/")
        filename = parts[-1]
        dir_path = "/".join(parts[:-1])
        folder_page_id = folder_ids.get(dir_path)

        if not folder_page_id:
            logger.warning("No folder page id computed for removed path '%s'; skipping.", file_path)
            continue

        title = derive_confluence_title(filename)

        if title is None:
            # README.md removal: do NOT delete folder pages; just clear content to avoid staleness.
            page = confluence.get_page_by_id(folder_page_id)
            if not page:
                logger.warning("Folder page id=%s not found; cannot clear README removal for '%s'", folder_page_id, file_path)
                continue

            logger.info("README removed: clearing content of folder page '%s' (id=%s).", page["title"], folder_page_id)
            try:
                confluence.update_page(
                    folder_page_id,
                    page["title"],
                    "<p></p>",
                    page["version"]["number"],
                )
            except Exception as exc:
                logger.error("Failed to clear folder page content for '%s': %s", file_path, exc)
            continue

        # Normal markdown file: delete the child page if it exists under the folder page.
        try:
            existing = confluence.get_page_by_title_under_parent(space_key, title, folder_page_id)
            if not existing:
                logger.info("Removed doc '%s' has no matching Confluence child page '%s' under parent %s; skipping.",
                            file_path, title, folder_page_id)
                continue

            logger.info("Deleting Confluence page '%s' (id=%s) for removed doc '%s'.", title, existing["id"], file_path)
            confluence.delete_page(existing["id"])
        except Exception as exc:
            logger.error("Failed to delete Confluence page for removed doc '%s': %s", file_path, exc)


def should_run_sync_entry(sync_entry: dict, deployment: str) -> bool:
    target = str(sync_entry.get("target", "") or "").strip().lower()
    if not target:
        return True
    return target == deployment


def main(config_path: str = "config.yml") -> None:
    config = load_config(config_path)

    github_token = os.environ.get("GITHUB_TOKEN")
    confluence_url = os.environ.get("CONFLUENCE_URL")
    confluence_username = os.environ.get("CONFLUENCE_USERNAME")
    confluence_api_token = os.environ.get("CONFLUENCE_API_TOKEN")
    confluence_bearer_token = os.environ.get("CONFLUENCE_BEARER_TOKEN")

    confluence_deployment = (os.environ.get("CONFLUENCE_DEPLOYMENT") or "cloud").strip().lower()
    if confluence_deployment not in ("hosted", "cloud"):
        logger.error("Invalid CONFLUENCE_DEPLOYMENT=%r (expected 'hosted' or 'cloud').", confluence_deployment)
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
        logger.error("Missing required env vars: %s", ", ".join(missing))
        sys.exit(1)

    confluence = ConfluenceClient(
        confluence_url,
        confluence_username or "",
        confluence_api_token or "",
        bearer_token=confluence_bearer_token,
    )

    changed_docs = read_changed_docs_from_env()
    removed_docs = read_removed_docs_from_env()

    if changed_docs:
        logger.info("CHANGED_DOCS provided (%d path(s)).", len(changed_docs))
    if removed_docs:
        logger.info("REMOVED_DOCS provided (%d path(s)).", len(removed_docs))

    for sync_entry in config.get("sync", []):
        if not should_run_sync_entry(sync_entry, confluence_deployment):
            continue

        github_repo = normalize_github_repo(sync_entry["github_repo"])
        github_branch = sync_entry.get("github_branch", "main")
        space_key = sync_entry["confluence_space"]
        parent_id = sync_entry.get("confluence_parent_id")
        docs_root = sync_entry.get("docs_root")

        if docs_root is None:
            logger.warning("Delete support is implemented for docs_root mode only; skipping non-docs_root entry.")
            continue
        if not parent_id:
            logger.error("confluence_parent_id is required when using docs_root; skipping entry.")
            continue

        sync_docs_tree(
            confluence,
            github_token,
            github_repo,
            github_branch,
            space_key,
            parent_id,
            docs_root,
            changed_paths=changed_docs,
            removed_paths=removed_docs,
        )


if __name__ == "__main__":
    main()