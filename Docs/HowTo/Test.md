## Markdown Markup Showcase

This page demonstrates many different types of **Markdown** markup that can be synced between GitHub and Confluencex.

---

### 1. Headings

# H1 Heading

## H2 Heading

### H3 Heading

#### H4 Heading

##### H5 Heading

###### H6 Heading

---

### 2. Emphasis

- **Bold text**
- _Italic text_
- **_Bold and italic text_**
- ~~Strikethrough text~~
- Subscript: H~2~O
- Superscript: 10^6^

---

### 3. Paragraphs and Line Breaks

This is a normal paragraph. It has multiple sentences to show how text wraps in GitHub and in Confluence after sync.

This is another paragraph separated by a blank line.
Line with a manual break at the end.  
This line appears directly under the previous one.

---

### 4. Lists

#### 4.1 Unordered list

- Item one
  - Nested item one A
  - Nested item one B
- Item two
- Item three

#### 4.2 Ordered list

1. First item
2. Second item
   1. Second item nested A
   2. Second item nested B
3. Third item

#### 4.3 Task list

- [x] Completed task
- [ ] Pending task
- [ ] Another item to complete

---

### 5. Tables

#### 5.1 Simple table

| Name  | Role        | Active |
| ----- | ----------- | :----: |
| Alice | Developer   |   ✅   |
| Bob   | QA Engineer |   ❌   |
| Carol | Manager     |   ✅   |

#### 5.2 Alignment and formatting

| Column Left        |  Column Center   | Column Right |
| :----------------- | :--------------: | -----------: |
| Plain text         | **Bold center**  |        12345 |
| _Italic left_      |  `inline code`   |        98.76 |
| Multi-line content | Line 1<br>Line 2 |    $1,234.56 |

#### 5.3 Table with links

| Environment | URL                         | Notes                |
| ----------- | --------------------------- | -------------------- |
| Dev         | https://dev.example.com     | Internal only        |
| Staging     | https://staging.example.com | UAT environment      |
| Production  | https://www.example.com     | Customer-facing site |

---

### 6. Code

#### 6.1 Inline code

Use `sync_to_confluence.py` to run the synchronization job.

#### 6.2 Fenced code block (no language)

```
echo "Syncing docs from GitHub to Confluence..."
python sync_to_confluence.py --config config.yml
```

#### 6.3 Fenced code block (Python)

```python
def sync_docs(source_dir: str, space_key: str) -> None:
	 """Synchronize Markdown documents to Confluence."""
	 print(f"Syncing from {source_dir} to space {space_key}...")

	 # TODO: Call the real sync implementation
	 # This is just example markup for the documentation test file.

if __name__ == "__main__":
	 sync_docs("./Docs", "DOCS")
```

#### 6.4 Fenced code block (JSON)

```json
{
  "spaceKey": "DOCS",
  "rootPageTitle": "Docs Home",
  "attachments": true
}
```

---

### 7. Blockquotes

> This is a simple blockquote that might represent
> a note or an important callout in the documentation.

> #### Nested content
>
> - Bullet one
> - Bullet two

---

### 8. Links and Images

- External link: [GitHub](https://github.com)
- Relative link to another doc (example): [Release Notes](../Releases/Release1.md)

Image example (URL placeholder):

![Sample diagram](https://via.placeholder.com/400x200 "Sample diagram placeholder")

---

### 9. Horizontal Rules

Above and below this section there are horizontal rules
created with `---`.

---

### 10. Escaped Characters and Literals

- Display a literal asterisk: \*
- Display a literal underscore: \_
- Display Markdown characters without formatting: \*not bold\*

Literal backticks:

`` `literal backticks` ``

---

### 11. Definition List (GitHub-style)

Term 1
: Definition for term 1.

Term 2
: First line of definition
: Second line of definition

---

### 12. Mixed Content Example

1. Step one: run the sync script.

   ```bash
   python sync_to_confluence.py --dry-run
   ```

2. Step two: verify that the following table matches what appears in Confluence:

   | ID  | Status  |
   | --- | ------- |
   | 1   | Synced  |
   | 2   | Pending |

3. Step three: update this document with additional markup if you need to test new scenarios.

---

End of Markdown markup showcase.
