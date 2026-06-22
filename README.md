# Polaris MCP

**Browser automation MCP with Map First architecture.**

Polaris orients the AI before it navigates. Just as the North Star gave sailors a fixed reference before crossing any sea, Polaris gives the AI a complete mental map of any website before writing a single line of automation.

---

## Architecture: Map First → Execute → Verify

```
KNOWLEDGE
  browser_map_site          BFS crawl → selector_index with all [data-qa] per page
  browser_explore_page      Deep inspection: clicks triggers to reveal hidden elements
  browser_intercept_network Captures XHR/fetch live: endpoints, payloads, responses
  browser_accessibility_tree ARIA tree — works on any site without data-qa

EXECUTION
  browser_run_playwright    Runs AI-generated Playwright code directly (no LLM loop)
  browser_execute_sequence  Typed action sequence: goto/click/fill/wait_for/snapshot/...
  browser_run_task          LLM fallback (browser-use) for unstructured exploration

VERIFICATION
  browser_diff_pages        Structural diff between two page states
  browser_capture_console   Captures browser console: errors, warnings, logs
  browser_get_storage       Reads localStorage, sessionStorage, cookies

AUTHENTICATION
  browser_login             One-off login, saves session to file
  browser_session_save      Named persistent session
  browser_session_check     Validates session is still active
  browser_session_list      Lists all saved sessions

UTILITIES
  browser_screenshot        Screenshot as base64 PNG
  browser_get_page_content  Visible page text
  browser_get_help          Full documentation
```

---

## Typical flow for a new site

```python
# 1. Authenticate
browser_session_save("myaccount", "https://app.com/login", "user@x.com", "pass")

# 2. Map the site — know the selectors before writing automation
browser_map_site("https://app.com", session_file=".../myaccount.json")
# → selector_index: {"AddButton": {"pages": ["/dashboard"]}, "ItemCard": {...}}

# 3. Deep-inspect a page — discover hidden dropdowns and modals
browser_explore_page("https://app.com/dashboard", trigger_interactions=True)

# 4. Map the API layer
browser_intercept_network("https://app.com/dashboard", actions_code="...")

# 5. Execute with real selectors
browser_execute_sequence('[{"action":"click","selector":"[data-qa=AddButton]"}]')

# 6. Verify the result
browser_diff_pages("https://app.com/dashboard", actions_code="...")
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BROWSER_HEADLESS` | `true` | Run browser headlessly |
| `BROWSER_USE_MODEL` | `gpt-4o-mini` | LLM for browser_run_task |
| `BROWSER_MCP_SESSIONS_DIR` | `/tmp/browser_mcp_sessions` | Named sessions directory |
| `MCP_HOST` | `127.0.0.1` | MCP server host |
| `MCP_PORT` | `8016` | MCP server port |
| `MCP_TRANSPORT` | `streamable-http` | Transport protocol |
| `OPENAI_API_KEY` | — | For GPT models |
| `ANTHROPIC_API_KEY` | — | For Claude models |

## Requirements

- Python 3.11+
- `browser-use`
- `playwright`
- `mcp[server]`

```bash
pip install browser-use playwright mcp
playwright install chromium
```

## Start

```bash
./start_browser_python_mcp.sh
```
