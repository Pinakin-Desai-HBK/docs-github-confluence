# TEMP DEBUG: dump storage XHTML before sending to Confluence
if page_id == "405152490":
    with open("debug-405152490.xhtml", "w", encoding="utf-8") as f:
        f.write(storage_content)
    logger.error("Wrote debug-405152490.xhtml (len=%d)", len(storage_content))