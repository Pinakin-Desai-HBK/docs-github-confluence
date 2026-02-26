"""Unit tests for sync_to_confluence.py."""

import base64
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch, mock_open

# Make the parent directory importable when running tests directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_to_confluence import (
    ConfluenceClient,
    get_github_file_content,
    load_config,
    markdown_to_confluence,
    sync_document,
)


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig(unittest.TestCase):
    def test_loads_valid_yaml(self):
        yaml_content = (
            "confluence:\n"
            "  url: https://example.atlassian.net\n"
            "  username: user@example.com\n"
            "sync:\n"
            "  - github_repo: org/repo\n"
            "    confluence_space: DOC\n"
            "    documents: []\n"
        )
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            config = load_config("config.yml")

        self.assertEqual(config["confluence"]["url"], "https://example.atlassian.net")
        self.assertEqual(config["sync"][0]["github_repo"], "org/repo")

    def test_returns_dict(self):
        yaml_content = "confluence:\n  url: https://x.atlassian.net\n"
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            config = load_config()
        self.assertIsInstance(config, dict)


# ---------------------------------------------------------------------------
# get_github_file_content
# ---------------------------------------------------------------------------

class TestGetGithubFileContent(unittest.TestCase):
    def _make_response(self, text: str) -> MagicMock:
        encoded = base64.b64encode(text.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded + "\n"}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @patch("sync_to_confluence.requests.get")
    def test_returns_decoded_content(self, mock_get):
        mock_get.return_value = self._make_response("# Hello World\n")
        result = get_github_file_content("token", "org/repo", "README.md", "main")
        self.assertEqual(result, "# Hello World\n")

    @patch("sync_to_confluence.requests.get")
    def test_uses_correct_url_and_headers(self, mock_get):
        mock_get.return_value = self._make_response("content")
        get_github_file_content("mytoken", "org/repo", "docs/file.md", "develop")
        call_args = mock_get.call_args
        url = call_args[0][0]
        self.assertIn("org/repo", url)
        self.assertIn("docs/file.md", url)
        self.assertIn("develop", url)
        self.assertEqual(
            call_args[1]["headers"]["Authorization"], "token mytoken"
        )

    @patch("sync_to_confluence.requests.get")
    def test_raises_on_http_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("404")
        mock_get.return_value = mock_resp
        with self.assertRaises(Exception):
            get_github_file_content("token", "org/repo", "missing.md")


# ---------------------------------------------------------------------------
# markdown_to_confluence
# ---------------------------------------------------------------------------

class TestMarkdownToConfluence(unittest.TestCase):
    def test_h1_heading(self):
        result = markdown_to_confluence("# Title")
        self.assertIn("<h1>Title</h1>", result)

    def test_h3_heading(self):
        result = markdown_to_confluence("### Section")
        self.assertIn("<h3>Section</h3>", result)

    def test_bold_asterisks(self):
        result = markdown_to_confluence("**bold text**")
        self.assertIn("<strong>bold text</strong>", result)

    def test_bold_underscores(self):
        result = markdown_to_confluence("__bold text__")
        self.assertIn("<strong>bold text</strong>", result)

    def test_italic_asterisk(self):
        result = markdown_to_confluence("*italic*")
        self.assertIn("<em>italic</em>", result)

    def test_italic_underscore(self):
        result = markdown_to_confluence("_italic_")
        self.assertIn("<em>italic</em>", result)

    def test_inline_code(self):
        result = markdown_to_confluence("`code`")
        self.assertIn("<code>code</code>", result)

    def test_link(self):
        result = markdown_to_confluence("[GitHub](https://github.com)")
        self.assertIn('<a href="https://github.com">GitHub</a>', result)

    def test_unordered_list(self):
        md = "- item one\n- item two\n"
        result = markdown_to_confluence(md)
        self.assertIn("<ul>", result)
        self.assertIn("<li>item one</li>", result)
        self.assertIn("<li>item two</li>", result)
        self.assertIn("</ul>", result)

    def test_fenced_code_block(self):
        md = "```python\nprint('hello')\n```"
        result = markdown_to_confluence(md)
        self.assertIn('ac:name="code"', result)
        self.assertIn("python", result)
        self.assertIn("print('hello')", result)

    def test_fenced_code_block_no_language(self):
        md = "```\nsome code\n```"
        result = markdown_to_confluence(md)
        self.assertIn('ac:name="code"', result)
        self.assertIn("some code", result)

    def test_plain_paragraph_wrapped(self):
        result = markdown_to_confluence("Just a paragraph.")
        self.assertIn("<p>Just a paragraph.</p>", result)

    def test_empty_string(self):
        result = markdown_to_confluence("")
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# ConfluenceClient
# ---------------------------------------------------------------------------

class TestConfluenceClient(unittest.TestCase):
    def setUp(self):
        self.client = ConfluenceClient(
            "https://example.atlassian.net", "user@example.com", "apitoken"
        )

    def _mock_response(self, data: dict, status: int = 200) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.json.return_value = data
        mock_resp.status_code = status
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @patch("sync_to_confluence.requests.get")
    def test_get_page_by_title_found(self, mock_get):
        page = {"id": "42", "version": {"number": 3}}
        mock_get.return_value = self._mock_response({"results": [page]})
        result = self.client.get_page_by_title("DOC", "My Page")
        self.assertEqual(result["id"], "42")

    @patch("sync_to_confluence.requests.get")
    def test_get_page_by_title_not_found(self, mock_get):
        mock_get.return_value = self._mock_response({"results": []})
        result = self.client.get_page_by_title("DOC", "Nonexistent")
        self.assertIsNone(result)

    @patch("sync_to_confluence.requests.post")
    def test_create_page(self, mock_post):
        mock_post.return_value = self._mock_response({"id": "99", "title": "New Page"})
        result = self.client.create_page("DOC", "New Page", "<p>content</p>")
        self.assertEqual(result["id"], "99")
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["title"], "New Page")
        self.assertEqual(payload["space"]["key"], "DOC")

    @patch("sync_to_confluence.requests.post")
    def test_create_page_with_parent(self, mock_post):
        mock_post.return_value = self._mock_response({"id": "100"})
        self.client.create_page("DOC", "Child Page", "<p>x</p>", parent_id="50")
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["ancestors"], [{"id": "50"}])

    @patch("sync_to_confluence.requests.put")
    def test_update_page(self, mock_put):
        mock_put.return_value = self._mock_response({"id": "42"})
        result = self.client.update_page("42", "Updated Title", "<p>new</p>", 5)
        self.assertEqual(result["id"], "42")
        payload = mock_put.call_args[1]["json"]
        self.assertEqual(payload["version"]["number"], 6)  # current + 1

    @patch("sync_to_confluence.requests.put")
    def test_update_page_increments_version(self, mock_put):
        mock_put.return_value = self._mock_response({})
        self.client.update_page("1", "Title", "content", current_version=10)
        payload = mock_put.call_args[1]["json"]
        self.assertEqual(payload["version"]["number"], 11)

    @patch("sync_to_confluence.requests.get")
    def test_get_page_raises_on_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("401")
        mock_get.return_value = mock_resp
        with self.assertRaises(Exception):
            self.client.get_page_by_title("DOC", "Page")

    # ------------------------------------------------------------------
    # _parse_json_response — non-JSON / SSO HTML response handling
    # ------------------------------------------------------------------

    def _make_html_response(self, status: int = 200) -> MagicMock:
        """Simulate an SSO login page returned with text/html Content-Type."""
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.headers = {"Content-Type": "text/html;charset=UTF-8"}
        mock_resp.text = "<html><body>Please log in</body></html>"
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @patch("sync_to_confluence.requests.get")
    def test_get_page_html_response_raises_valueerror(self, mock_get):
        mock_get.return_value = self._make_html_response()
        with self.assertRaises(ValueError) as ctx:
            self.client.get_page_by_title("DOC", "Page")
        msg = str(ctx.exception)
        self.assertIn("non-JSON", msg)
        self.assertIn("text/html", msg)
        self.assertNotIn("Authorization", msg)
        self.assertNotIn("Bearer", msg)

    @patch("sync_to_confluence.requests.post")
    def test_create_page_html_response_raises_valueerror(self, mock_post):
        mock_post.return_value = self._make_html_response()
        with self.assertRaises(ValueError) as ctx:
            self.client.create_page("DOC", "New Page", "<p>x</p>")
        msg = str(ctx.exception)
        self.assertIn("non-JSON", msg)
        self.assertIn("text/html", msg)

    @patch("sync_to_confluence.requests.put")
    def test_update_page_html_response_raises_valueerror(self, mock_put):
        mock_put.return_value = self._make_html_response()
        with self.assertRaises(ValueError) as ctx:
            self.client.update_page("42", "Title", "<p>x</p>", 1)
        msg = str(ctx.exception)
        self.assertIn("non-JSON", msg)
        self.assertIn("text/html", msg)

    def test_parse_json_response_valid_json(self):
        """Valid JSON responses are returned without error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json;charset=UTF-8"}
        mock_resp.json.return_value = {"results": [{"id": "1"}]}
        result = self.client._parse_json_response(mock_resp, "GET", "http://example.com")
        self.assertEqual(result["results"][0]["id"], "1")

    def test_parse_json_response_invalid_json_raises_valueerror(self):
        """application/json Content-Type but malformed body raises ValueError."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.text = "not-json"
        mock_resp.json.side_effect = ValueError("No JSON")
        with self.assertRaises(ValueError) as ctx:
            self.client._parse_json_response(mock_resp, "GET", "http://example.com")
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_parse_json_response_error_includes_body_prefix(self):
        """Error message includes a prefix of the response body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.text = "<html>" + "x" * 600
        with self.assertRaises(ValueError) as ctx:
            self.client._parse_json_response(mock_resp, "GET", "http://example.com")
        msg = str(ctx.exception)
        # Body prefix capped at 500 chars — the 600-char padding should NOT appear in full
        self.assertIn("<html>", msg)
        self.assertNotIn("x" * 501, msg)

    def test_parse_json_response_no_auth_in_error(self):
        """Authorization header value must not appear in the error message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.text = "<html>login</html>"
        with self.assertRaises(ValueError) as ctx:
            self.client._parse_json_response(mock_resp, "GET", "http://example.com")
        # The Bearer token stored in self.client.headers must not leak
        self.assertNotIn("apitoken", str(ctx.exception))

# ---------------------------------------------------------------------------
# sync_document
# ---------------------------------------------------------------------------

class TestSyncDocument(unittest.TestCase):
    def _make_client(self):
        return MagicMock(spec=ConfluenceClient)

    def test_creates_page_when_not_exists(self):
        client = self._make_client()
        client.get_page_by_title.return_value = None
        client.create_page.return_value = {"id": "77"}

        sync_document(client, "DOC", "New Doc", "<p>hello</p>")

        client.create_page.assert_called_once_with("DOC", "New Doc", "<p>hello</p>", None)
        client.update_page.assert_not_called()

    def test_creates_page_with_parent(self):
        client = self._make_client()
        client.get_page_by_title.return_value = None
        client.create_page.return_value = {"id": "77"}

        sync_document(client, "DOC", "Child", "<p>child</p>", parent_id="10")

        client.create_page.assert_called_once_with("DOC", "Child", "<p>child</p>", "10")

    def test_updates_page_when_exists(self):
        client = self._make_client()
        client.get_page_by_title.return_value = {"id": "42", "version": {"number": 7}}

        sync_document(client, "DOC", "Existing Doc", "<p>updated</p>")

        client.update_page.assert_called_once_with("42", "Existing Doc", "<p>updated</p>", 7)
        client.create_page.assert_not_called()

    def test_error_from_confluence_propagates(self):
        client = self._make_client()
        client.get_page_by_title.side_effect = Exception("network error")

        with self.assertRaises(Exception):
            sync_document(client, "DOC", "Doc", "content")


if __name__ == "__main__":
    unittest.main()
