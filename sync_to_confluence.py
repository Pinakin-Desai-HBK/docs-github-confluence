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

import requests
import yaml

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


# ---------------------------------------------------------------------------
# Markdown → Confluence storage-format conversion
# ---------------------------------------------------------------------------

def markdown_to_confluence(markdown_content: str) -> str:
    """
    Convert markdown text to Confluence storage format (XHTML subset).

    Handles the most common markdown constructs:
    headings, bold/italic, fenced code blocks, inline code, links, and lists.
    """
    content = markdown_content

    # Fenced code blocks (must come before inline-code replacement)
    def replace_code_block(match: re.Match) -> str:
        lang = match.group(1) or ""
        body = match.group(2)
        return (
            f'<ac:structured-macro ac:name="code">'
            f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
            f"<ac:plain-text-body><![CDATA[{body}]]></ac:plain-text-body>"
            f"</ac:structured-macro>"
        )

    content = re.sub(r"```(\w+)?\n(.*?)```", replace_code_block, content, flags=re.DOTALL)

    # Headings (h6 → h1, processed largest-first to avoid double substitution)
    for level in range(6, 0, -1):
        content = re.sub(
            rf"^{'#' * level} (.+)$",
            rf"<h{level}>\1</h{level}>",
            content,
            flags=re.MULTILINE,
        )

    # Bold (** and __)
    content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
    content = re.sub(r"__(.+?)__", r"<strong>\1</strong>", content)

    # Italic (* and _)
    content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
    content = re.sub(r"_(.+?)_", r"<em>\1</em>", content)

    # Inline code
    content = re.sub(r"`(.+?)`", r"<code>\1</code>", content)

    # Links
    content = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', content)

    # Unordered lists
    lines = content.split("\n")
    result_lines: list[str] = []
    in_list = False
    for line in lines:
        if re.match(r"^[-*+] (.+)$", line):
            if not in_list:
                result_lines.append("<ul>")
                in_list = True
            item = re.sub(r"^[-*+] (.+)$", r"<li>\1</li>", line)
            result_lines.append(item)
        else:
            if in_list:
                result_lines.append("</ul>")
                in_list = False
            result_lines.append(line)
    if in_list:
        result_lines.append("</ul>")
    content = "\n".join(result_lines)

    # Wrap plain-text paragraphs (separated by blank lines) in <p> tags
    paragraphs = re.split(r"\n\n+", content)
    wrapped: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if para and not para.startswith("<"):
            para = f"<p>{para}</p>"
        wrapped.append(para)
    return "\n".join(wrapped)


# ---------------------------------------------------------------------------
# Confluence REST API client
# ---------------------------------------------------------------------------

class ConfluenceClient:
    """Thin wrapper around the Confluence REST API."""

    def __init__(self, base_url: str, username: str, api_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = None
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_token}",
        }

    def get_page_by_title(self, space_key: str, title: str) -> Optional[dict]:
        """Return a page dict (including version) if *title* exists in *space_key*, else None."""
        url = f"{self.base_url}/rest/api/content"
        params = {"spaceKey": space_key, "title": title, "expand": "version"}
        response = requests.get(
            url, auth=self.auth, headers=self.headers, params=params, timeout=30
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        return results[0] if results else None

    def create_page(
        self,
        space_key: str,
        title: str,
        content: str,
        parent_id: Optional[str] = None,
    ) -> dict:
        """Create a new page and return the response JSON."""
        url = f"{self.base_url}/rest/api/content"
        body: dict = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": content, "representation": "storage"}},
        }
        if parent_id:
            body["ancestors"] = [{"id": parent_id}]
        response = requests.post(
            url, auth=self.auth, headers=self.headers, json=body, timeout=30
        )
        response.raise_for_status()
        return response.json()

    def update_page(
        self, page_id: str, title: str, content: str, current_version: int
    ) -> dict:
        """Update an existing page to *current_version + 1* and return the response JSON."""
        url = f"{self.base_url}/rest/api/content/{page_id}"
        body = {
            "type": "page",
            "title": title,
            "version": {"number": current_version + 1},
            "body": {"storage": {"value": content, "representation": "storage"}},
        }
        response = requests.put(
            url, auth=self.auth, headers=self.headers, json=body, timeout=30
        )
        response.raise_for_status()
        return response.json()


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

    missing = [
        name
        for name, val in [
            ("GITHUB_TOKEN", github_token),
            ("CONFLUENCE_URL", confluence_url),
            ("CONFLUENCE_USERNAME", confluence_username),
            ("CONFLUENCE_API_TOKEN", confluence_api_token),
        ]
        if not val
    ]
    if missing:
        logger.error(
            "Missing required credentials: %s. "
            "Set them as environment variables (or url/username in config.yml).",
            ", ".join(missing),
        )
        sys.exit(1)

    confluence = ConfluenceClient(confluence_url, confluence_username, confluence_api_token)

    for sync_entry in config.get("sync", []):
        github_repo = normalize_github_repo(sync_entry["github_repo"])
        github_branch = sync_entry.get("github_branch", "main")
        space_key = sync_entry["confluence_space"]
        parent_id = sync_entry.get("confluence_parent_id")

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
