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
    list_github_docs,
    derive_confluence_title,
    ensure_folder_page,
    load_config,
    markdown_to_confluence,
    sync_document,
    sync_docs_tree,
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

    def test_bold_italic_asterisks(self):
        result = markdown_to_confluence("***Bold and italic text***")
        self.assertIn("<strong><em>Bold and italic text</em></strong>", result)
        # Must not contain mis-nested close tags
        self.assertNotIn("</strong></em>", result)

    def test_bold_italic_underscores(self):
        result = markdown_to_confluence("___Bold and italic text___")
        self.assertIn("<strong><em>Bold and italic text</em></strong>", result)
        self.assertNotIn("</strong></em>", result)

    def test_bold_italic_list_item(self):
        md = "- ***Bold and italic text***\n"
        result = markdown_to_confluence(md)
        self.assertIn("<li><strong><em>Bold and italic text</em></strong></li>", result)
        self.assertNotIn("</strong></em>", result)

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

    def test_br_tag_normalized_to_self_closing(self):
        result = markdown_to_confluence("Line 1<br>Line 2")
        self.assertIn("Line 1<br/>Line 2", result)
        self.assertNotIn("<br>", result)

    def test_br_tag_uppercase_normalized(self):
        result = markdown_to_confluence("Line 1<BR>Line 2")
        self.assertIn("Line 1<br/>Line 2", result)
        self.assertNotIn("<BR>", result)

    def test_br_tag_with_space_normalized(self):
        result = markdown_to_confluence("Line 1<br >Line 2")
        self.assertIn("Line 1<br/>Line 2", result)
        self.assertNotIn("<br >", result)

    def test_br_self_closing_preserved(self):
        result = markdown_to_confluence("Line 1<br/>Line 2")
        self.assertIn("Line 1<br/>Line 2", result)

    def test_br_normalization_does_not_affect_code_macro(self):
        md = "```python\nfoo<br>bar\n```"
        result = markdown_to_confluence(md)
        self.assertIn("ac:structured-macro", result)
        self.assertIn("foo<br>bar", result)
        self.assertIn("<![CDATA[foo<br>bar", result)

    def test_raw_xml_tags_are_escaped(self):
        result = markdown_to_confluence("Here is xml: <xml><a>1</a></xml>")
        self.assertIn("&lt;xml&gt;", result)
        self.assertIn("&lt;/xml&gt;", result)
        self.assertNotIn("<xml>", result)
        self.assertNotIn("</xml>", result)

    def test_raw_xml_tags_in_paragraph_are_escaped(self):
        result = markdown_to_confluence("Here is xml: <xml><a>1</a></xml>")
        self.assertIn("<p>Here is xml: &lt;xml&gt;&lt;a&gt;1&lt;/a&gt;&lt;/xml&gt;</p>", result)

    def test_raw_html_does_not_produce_unbalanced_tags(self):
        result = markdown_to_confluence("Some text <unknown> more text")
        self.assertNotIn("<unknown>", result)
        self.assertIn("&lt;unknown&gt;", result)

    def test_raw_xml_in_code_block_is_preserved_verbatim(self):
        md = "```xml\n<xml><a>1</a></xml>\n```"
        result = markdown_to_confluence(md)
        self.assertIn("ac:structured-macro", result)
        self.assertIn("<xml><a>1</a></xml>", result)
        self.assertNotIn("&lt;xml&gt;", result)

    def test_br_normalization_still_works_after_escaping(self):
        result = markdown_to_confluence("Line 1<br>Line 2")
        self.assertIn("Line 1<br/>Line 2", result)
        self.assertNotIn("<br>", result)
        self.assertNotIn("&lt;br", result)


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
        mock_resp.text = json.dumps(data)
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

    @patch("sync_to_confluence.requests.get")
    def test_get_page_by_title_404_invalid_space_key_raises_valueerror(self, mock_get):
        payload = {
            "statusCode": 404,
            "data": {"authorized": False, "valid": True, "successful": False},
            "message": "No space with key : DOC",
            "reason": "Not Found",
        }
        mock_get.return_value = self._mock_response(payload, status=404)
        with self.assertRaises(ValueError) as ctx:
            self.client.get_page_by_title("DOC", "Hello")
        self.assertIn("confluence_space", str(ctx.exception))
        self.assertIn("No space with key", str(ctx.exception))

    @patch("sync_to_confluence.requests.get")
    def test_get_page_by_title_404_other_returns_none(self, mock_get):
        payload = {
            "statusCode": 404,
            "message": "Not Found",
            "reason": "Not Found",
        }
        mock_get.return_value = self._mock_response(payload, status=404)
        result = self.client.get_page_by_title("DOC", "Hello")
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

    @patch("sync_to_confluence.logger")
    @patch("sync_to_confluence.requests.post")
    def test_create_page_logs_error_on_failure(self, mock_post, mock_logger):
        """create_page logs status code and body snippet on non-2xx before raising."""
        import requests as req_lib
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 400
        mock_resp.text = '{"message":"Title is required"}'
        http_err = req_lib.exceptions.HTTPError("400 Client Error")
        mock_resp.raise_for_status.side_effect = http_err
        mock_post.return_value = mock_resp

        with self.assertRaises(req_lib.exceptions.HTTPError):
            self.client.create_page("DOC", "Folder", "<p></p>", parent_id="10")

        mock_logger.error.assert_called_once()
        log_args = mock_logger.error.call_args[0]
        self.assertIn(400, log_args)
        self.assertNotIn("apitoken", str(log_args))

    @patch("sync_to_confluence.logger")
    @patch("sync_to_confluence.requests.put")
    def test_update_page_logs_error_on_failure(self, mock_put, mock_logger):
        """update_page logs status code and body snippet on non-2xx before raising."""
        import requests as req_lib
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 409
        mock_resp.text = '{"message":"Version conflict"}'
        mock_resp.raise_for_status.side_effect = req_lib.exceptions.HTTPError("409 Client Error")
        mock_put.return_value = mock_resp

        with self.assertRaises(req_lib.exceptions.HTTPError):
            self.client.update_page("42", "Title", "<p>x</p>", 1)

        mock_logger.error.assert_called_once()
        log_args = mock_logger.error.call_args[0]
        self.assertIn(409, log_args)
        self.assertNotIn("apitoken", str(log_args))

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


# ---------------------------------------------------------------------------
# derive_confluence_title
# ---------------------------------------------------------------------------

class TestDeriveConfluenceTitle(unittest.TestCase):
    def test_readme_md_returns_none(self):
        self.assertIsNone(derive_confluence_title("README.md"))

    def test_readme_case_insensitive(self):
        self.assertIsNone(derive_confluence_title("readme.md"))
        self.assertIsNone(derive_confluence_title("Readme.MD"))
        self.assertIsNone(derive_confluence_title("README.MD"))

    def test_normal_md_strips_extension(self):
        self.assertEqual(derive_confluence_title("Installation.md"), "Installation")
        self.assertEqual(derive_confluence_title("my-doc.md"), "my-doc")

    def test_nested_name_uses_basename(self):
        # derive_confluence_title works on filename only, not full paths
        self.assertEqual(derive_confluence_title("Setup.md"), "Setup")

    def test_no_extension_returns_name(self):
        self.assertEqual(derive_confluence_title("somefile"), "somefile")


# ---------------------------------------------------------------------------
# list_github_docs
# ---------------------------------------------------------------------------

class TestListGithubDocs(unittest.TestCase):
    def _make_tree_response(self, items: list) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @patch("sync_to_confluence.requests.get")
    def test_returns_md_files_under_docs(self, mock_get):
        tree = [
            {"type": "blob", "path": "Docs/README.md"},
            {"type": "blob", "path": "Docs/HowTo/Test.md"},
            {"type": "blob", "path": "Docs/HowTo/README.md"},
            {"type": "blob", "path": "other/file.md"},   # outside Docs/
            {"type": "tree", "path": "Docs/HowTo"},       # directory entry
        ]
        mock_get.return_value = self._make_tree_response(tree)
        result = list_github_docs("token", "org/repo", "main", "Docs")
        self.assertIn("Docs/README.md", result)
        self.assertIn("Docs/HowTo/Test.md", result)
        self.assertIn("Docs/HowTo/README.md", result)
        self.assertNotIn("other/file.md", result)
        self.assertNotIn("Docs/HowTo", result)

    @patch("sync_to_confluence.requests.get")
    def test_excludes_non_md_files(self, mock_get):
        tree = [
            {"type": "blob", "path": "Docs/image.png"},
            {"type": "blob", "path": "Docs/guide.md"},
        ]
        mock_get.return_value = self._make_tree_response(tree)
        result = list_github_docs("token", "org/repo", "main", "Docs")
        self.assertEqual(result, ["Docs/guide.md"])

    @patch("sync_to_confluence.requests.get")
    def test_uses_correct_github_api_url(self, mock_get):
        mock_get.return_value = self._make_tree_response([])
        list_github_docs("mytoken", "org/repo", "develop", "Docs")
        url = mock_get.call_args[0][0]
        self.assertIn("org/repo", url)
        self.assertIn("develop", url)
        self.assertIn("recursive=1", url)


# ---------------------------------------------------------------------------
# ConfluenceClient.get_page_by_title_under_parent / get_page_by_id
# ---------------------------------------------------------------------------

class TestConfluenceClientExtended(unittest.TestCase):
    def setUp(self):
        self.client = ConfluenceClient(
            "https://example.atlassian.net", "user@example.com", "apitoken"
        )

    def _mock_response(self, data: dict, status: int = 200) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.json.return_value = data
        mock_resp.status_code = status
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.text = json.dumps(data)
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @patch("sync_to_confluence.requests.get")
    def test_get_page_by_title_under_parent_found(self, mock_get):
        page = {"id": "77", "version": {"number": 2}}
        mock_get.return_value = self._mock_response({"results": [page]})
        result = self.client.get_page_by_title_under_parent("DOC", "HowTo", "100")
        self.assertEqual(result["id"], "77")
        # CQL should include the parent constraint
        params = mock_get.call_args.kwargs["params"]
        self.assertIn("parent = 100", params["cql"])
        self.assertIn("HowTo", params["cql"])

    @patch("sync_to_confluence.requests.get")
    def test_get_page_by_title_under_parent_not_found(self, mock_get):
        mock_get.return_value = self._mock_response({"results": []})
        result = self.client.get_page_by_title_under_parent("DOC", "Missing", "100")
        self.assertIsNone(result)

    @patch("sync_to_confluence.requests.get")
    def test_get_page_by_id_found(self, mock_get):
        page = {"id": "42", "title": "HowTo", "version": {"number": 3}}
        mock_get.return_value = self._mock_response(page)
        result = self.client.get_page_by_id("42")
        self.assertEqual(result["id"], "42")
        self.assertEqual(result["title"], "HowTo")

    @patch("sync_to_confluence.requests.get")
    def test_get_page_by_id_not_found_returns_none(self, mock_get):
        mock_get.return_value = self._mock_response({}, status=404)
        result = self.client.get_page_by_id("999")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# ensure_folder_page
# ---------------------------------------------------------------------------

class TestEnsureFolderPage(unittest.TestCase):
    def _make_client(self):
        return MagicMock(spec=ConfluenceClient)

    def test_returns_existing_page_id(self):
        client = self._make_client()
        client.get_page_by_title_under_parent.return_value = {"id": "55", "version": {"number": 1}}
        result = ensure_folder_page(client, "DOC", "HowTo", "10")
        self.assertEqual(result, "55")
        client.create_page.assert_not_called()

    def test_creates_page_when_not_found(self):
        client = self._make_client()
        client.get_page_by_title_under_parent.return_value = None
        client.create_page.return_value = {"id": "66"}
        result = ensure_folder_page(client, "DOC", "HowTo", "10")
        self.assertEqual(result, "66")
        client.create_page.assert_called_once_with("DOC", "HowTo", "<p></p>", parent_id="10")

    def test_creates_page_with_non_empty_body(self):
        """Folder pages must be created with a non-empty body to avoid HTTP 400."""
        client = self._make_client()
        client.get_page_by_title_under_parent.return_value = None
        client.create_page.return_value = {"id": "66"}
        ensure_folder_page(client, "DOC", "HowTo", "10")
        _, _, body = client.create_page.call_args[0]
        self.assertTrue(body, "Folder page body must not be empty")


# ---------------------------------------------------------------------------
# sync_docs_tree
# ---------------------------------------------------------------------------

class TestSyncDocsTree(unittest.TestCase):
    def _make_client(self):
        return MagicMock(spec=ConfluenceClient)

    @patch("sync_to_confluence.get_github_file_content")
    @patch("sync_to_confluence.list_github_docs")
    def test_readme_at_root_updates_parent_page(self, mock_list, mock_fetch):
        """Docs/README.md should update the configured root parent page."""
        client = self._make_client()
        mock_list.return_value = ["Docs/README.md"]
        mock_fetch.return_value = "# Root"
        client.get_page_by_id.return_value = {"id": "100", "title": "Root", "version": {"number": 1}}

        sync_docs_tree(client, "tok", "org/repo", "main", "DOC", "100", "Docs")

        client.get_page_by_id.assert_called_with("100")
        client.update_page.assert_called_once()
        args = client.update_page.call_args[0]
        self.assertEqual(args[0], "100")   # page id
        self.assertEqual(args[1], "Root")  # title unchanged

    @patch("sync_to_confluence.get_github_file_content")
    @patch("sync_to_confluence.list_github_docs")
    def test_readme_in_subfolder_updates_folder_page(self, mock_list, mock_fetch):
        """Docs/HowTo/README.md should update the HowTo folder page."""
        client = self._make_client()
        mock_list.return_value = ["Docs/HowTo/README.md"]
        mock_fetch.return_value = "# HowTo"
        # ensure_folder_page will find existing HowTo page
        client.get_page_by_title_under_parent.return_value = {"id": "200", "version": {"number": 1}}
        client.get_page_by_id.return_value = {"id": "200", "title": "HowTo", "version": {"number": 1}}

        sync_docs_tree(client, "tok", "org/repo", "main", "DOC", "100", "Docs")

        client.update_page.assert_called_once()
        args = client.update_page.call_args[0]
        self.assertEqual(args[0], "200")    # HowTo page id
        self.assertEqual(args[1], "HowTo")  # title unchanged

    @patch("sync_to_confluence.get_github_file_content")
    @patch("sync_to_confluence.list_github_docs")
    def test_normal_file_creates_child_page(self, mock_list, mock_fetch):
        """Docs/HowTo/Test.md should create a 'Test' page under the HowTo folder."""
        client = self._make_client()
        mock_list.return_value = ["Docs/HowTo/Test.md"]
        mock_fetch.return_value = "# Test"
        # ensure_folder_page finds HowTo
        client.get_page_by_title_under_parent.side_effect = [
            {"id": "200", "version": {"number": 1}},  # ensure_folder_page lookup
            None,                                       # normal file lookup
        ]
        client.create_page.return_value = {"id": "300"}

        sync_docs_tree(client, "tok", "org/repo", "main", "DOC", "100", "Docs")

        client.create_page.assert_called_once()
        self.assertEqual(client.create_page.call_args.args[1], "Test")   # title without extension

    @patch("sync_to_confluence.get_github_file_content")
    @patch("sync_to_confluence.list_github_docs")
    def test_normal_file_updates_existing_page(self, mock_list, mock_fetch):
        """Existing child pages are updated, not re-created."""
        client = self._make_client()
        mock_list.return_value = ["Docs/HowTo/Test.md"]
        mock_fetch.return_value = "# Test"
        client.get_page_by_title_under_parent.side_effect = [
            {"id": "200", "version": {"number": 1}},   # ensure_folder_page
            {"id": "300", "version": {"number": 2}},   # existing Test page
        ]

        sync_docs_tree(client, "tok", "org/repo", "main", "DOC", "100", "Docs")

        client.update_page.assert_called_once()
        update_args = client.update_page.call_args.args
        self.assertEqual(update_args[0], "300")
        self.assertEqual(update_args[1], "Test")
        self.assertEqual(update_args[3], 2)   # current version passed through


# ---------------------------------------------------------------------------
# sync_docs_tree — preflight validation
# ---------------------------------------------------------------------------

class TestSyncDocsTreePreflight(unittest.TestCase):
    def _make_client(self):
        return MagicMock(spec=ConfluenceClient)

    def test_raises_valueerror_when_parent_id_not_found(self):
        """sync_docs_tree must raise ValueError before touching any pages when the
        configured root parent page does not exist."""
        client = self._make_client()
        client.base_url = "https://example.atlassian.net"
        client.get_page_by_id.return_value = None

        with self.assertRaises(ValueError) as ctx:
            sync_docs_tree(client, "tok", "org/repo", "main", "DOC", "999999", "Docs")

        msg = str(ctx.exception)
        self.assertIn("999999", msg)
        self.assertIn("confluence_parent_id", msg)
        self.assertIn("DOC", msg)
        # No pages should have been created or updated
        client.create_page.assert_not_called()
        client.update_page.assert_not_called()

    def test_error_includes_base_url(self):
        """The ValueError must mention the base URL to help diagnose wrong-site issues."""
        client = self._make_client()
        client.base_url = "https://example.atlassian.net"
        client.get_page_by_id.return_value = None

        with self.assertRaises(ValueError) as ctx:
            sync_docs_tree(client, "tok", "org/repo", "main", "DOC", "123", "Docs")

        self.assertIn("https://example.atlassian.net", str(ctx.exception))

    @patch("sync_to_confluence.logger")
    def test_warns_on_space_mismatch(self, mock_logger):
        """A warning must be logged when the root parent page belongs to a different space."""
        client = self._make_client()
        client.get_page_by_id.return_value = {
            "id": "100",
            "title": "Root",
            "space": {"key": "OTHER"},
            "version": {"number": 1},
        }
        client.list_github_docs = MagicMock(return_value=[])

        with patch("sync_to_confluence.list_github_docs", return_value=[]):
            sync_docs_tree(client, "tok", "org/repo", "main", "DOC", "100", "Docs")

        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        self.assertTrue(
            any("OTHER" in c and "DOC" in c for c in warning_calls),
            f"Expected space-mismatch warning in: {warning_calls}",
        )

    @patch("sync_to_confluence.list_github_docs")
    def test_no_warning_when_spaces_match(self, mock_list):
        """No warning when the root parent page is in the expected space."""
        client = self._make_client()
        client.get_page_by_id.return_value = {
            "id": "100",
            "title": "Root",
            "space": {"key": "DOC"},
            "version": {"number": 1},
        }
        mock_list.return_value = []

        with patch("sync_to_confluence.logger") as mock_logger:
            sync_docs_tree(client, "tok", "org/repo", "main", "DOC", "100", "Docs")

        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        self.assertFalse(
            any("space" in c.lower() and "mismatch" in c.lower() or "OTHER" in c for c in warning_calls),
        )

    @patch("sync_to_confluence.get_github_file_content")
    @patch("sync_to_confluence.list_github_docs")
    def test_valid_config_proceeds_normally(self, mock_list, mock_fetch):
        """When the parent page exists, sync proceeds as before (no regression)."""
        client = self._make_client()
        client.get_page_by_id.return_value = {
            "id": "100",
            "title": "Root",
            "space": {"key": "DOC"},
            "version": {"number": 1},
        }
        mock_list.return_value = ["Docs/README.md"]
        mock_fetch.return_value = "# Root"

        sync_docs_tree(client, "tok", "org/repo", "main", "DOC", "100", "Docs")

        # update_page called for the README (updating root parent)
        client.update_page.assert_called_once()


if __name__ == "__main__":
    unittest.main()