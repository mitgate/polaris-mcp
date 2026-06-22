# Polaris MCP

**Browser automation for AI agents — Map First architecture.**

Polaris gives the AI a complete mental map of any website before writing a single automation step.
Named after the North Star: a fixed reference that sailors used to orient themselves before crossing
any sea. Polaris gives the AI that same fixed point — a full structural map of selectors, API calls,
and hidden elements — so it acts with knowledge instead of trial and error.

---

## Architecture

```
KNOWLEDGE   → understand the site first
  browser_map_site            BFS crawl → selector inventory across all pages
  browser_explore_page        Click triggers to reveal hidden dropdowns / modals / tabs
  browser_intercept_network   Capture live XHR/fetch: endpoints, payloads, responses
  browser_accessibility_tree  ARIA tree — works on any site without data-qa attributes

EXECUTION   → act with real selectors
  browser_run_playwright      Run Python Playwright code directly (no LLM in the loop)
  browser_execute_sequence    Typed JSON action sequence: goto / click / fill / wait_for / ...
  browser_run_task            LLM-driven natural language automation (fallback / exploration)

VERIFICATION → confirm what happened
  browser_diff_pages          Structural diff between two page states (before / after)
  browser_capture_console     Browser console output: errors, warnings, logs
  browser_get_storage         Read localStorage, sessionStorage, cookies

AUTHENTICATION
  browser_login               One-off login — saves session to a file path you specify
  browser_session_save        Named login — saves under a friendly name for reuse
  browser_session_check       Verify a named session is still active
  browser_session_list        List all saved sessions

UTILITIES
  browser_screenshot          Capture page as base64 PNG
  browser_get_page_content    Visible page text (no HTML), up to 20,000 characters
  browser_get_help            Return full documentation as a string
```

---

## How it works

```
1. Map the site     →  know every selector before writing a single line
2. Explore pages    →  discover dropdowns / modals hidden behind interactions
3. Intercept APIs   →  see every endpoint the frontend calls
4. Execute          →  use real selectors from the map (precise, deterministic)
5. Verify           →  diff the UI state before and after each action
```

When any AI agent connects to Polaris it receives a full capability briefing automatically
via the FastMCP `instructions` parameter — no manual setup required.

---

## System Requirements

| Requirement | Minimum |
|-------------|---------|
| Python | 3.11 or higher |
| Operating system | Linux or macOS |
| Internet access | Required (Playwright downloads Chromium) |
| LLM API key | At least one of: OpenAI or Anthropic |

---

## Python Dependencies

```
browser-use>=0.2.0
playwright>=1.44.0
mcp[server]>=1.0.0
```

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-username/polaris-mcp.git
cd polaris-mcp
```

### 2. Create and activate a Python 3.11+ virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\activate           # Windows
```

### 3. Install Python packages

```bash
pip install --upgrade pip
pip install browser-use playwright mcp
```

### 4. Install the Chromium browser

```bash
playwright install chromium
```

### 5. Set environment variables

Create a `.env` file in the project root (it is git-ignored):

```bash
# Required: at least one LLM key
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Optional overrides (these are the defaults)
BROWSER_USE_MODEL=gpt-4o-mini       # LLM for browser_run_task
BROWSER_HEADLESS=true               # false = show the browser window
POLARIS_SESSIONS_DIR=/tmp/polaris_sessions
MCP_HOST=127.0.0.1
MCP_PORT=8016
MCP_TRANSPORT=streamable-http
```

### 6. Start the server

```bash
./start_browser_python_mcp.sh
```

Or directly:

```bash
source .venv/bin/activate
source .env  # or export the variables manually
python browser_python_mcp.py
```

The server listens on `http://127.0.0.1:8016/mcp` by default.

---

## Connecting an AI Client

### Claude Code / Cline / Zed (streamable-http)

Add to your MCP settings:

```json
{
  "polaris": {
    "type": "streamable-http",
    "url": "http://127.0.0.1:8016/mcp"
  }
}
```

### Goose (streamable_http with uri)

```json
{
  "polaris": {
    "type": "streamable_http",
    "uri": "http://127.0.0.1:8016/mcp"
  }
}
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Required for OpenAI models (e.g. `gpt-4o-mini`) |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic models (e.g. `claude-sonnet-4-6`) |
| `BROWSER_USE_MODEL` | `gpt-4o-mini` | LLM used by `browser_run_task` |
| `BROWSER_HEADLESS` | `true` | Set `false` to show the browser window |
| `POLARIS_SESSIONS_DIR` | `/tmp/polaris_sessions` | Directory for named session files |
| `MCP_HOST` | `127.0.0.1` | Server bind address |
| `MCP_PORT` | `8016` | Server port |
| `MCP_TRANSPORT` | `streamable-http` | MCP transport protocol |

---

## Quick Start Example

```python
# 1. Save a named session
browser_session_save("myapp", "https://app.example.com/login", "user@x.com", "password")

# 2. Map the entire site — get all selectors before writing any automation
browser_map_site("https://app.example.com", session_file="/tmp/polaris_sessions/myapp.json")
# → selector_index: {"AddButton": {"count_total": 1, "pages_found": ["/dashboard"]}, ...}

# 3. Deep-inspect a page to discover hidden dropdowns and modals
browser_explore_page("https://app.example.com/dashboard", trigger_interactions=True)

# 4. Map the API layer — discover every endpoint the page calls
browser_intercept_network("https://app.example.com/dashboard")

# 5. Execute with real selectors from the map
browser_execute_sequence('[
  {"action": "goto", "url": "https://app.example.com/dashboard"},
  {"action": "click", "selector": "[data-qa=AddButton]"},
  {"action": "fill",  "selector": "[data-qa=NameInput]", "value": "New Item"},
  {"action": "click", "selector": "[data-qa=SaveButton]"}
]', session_file="/tmp/polaris_sessions/myapp.json")

# 6. Verify the UI changed as expected
browser_diff_pages(
  "https://app.example.com/dashboard",
  actions_code='await page.click("[data-qa=AddButton]")'
)
```

---

## Tool Reference

### KNOWLEDGE

**`browser_map_site`** — BFS crawl, up to `max_pages` pages.
Returns: `{ pages_mapped, pages: [...], selector_index: {...} }`

**`browser_explore_page`** — Static snapshot + click-triggered discovery.
Returns: `{ static_elements, revealed_after_interactions: [{trigger_qa, new_elements}] }`

**`browser_intercept_network`** — Live XHR/fetch capture during load and optional actions.
Returns: `{ requests_captured, entries: [{method, url, status, request_body, response_body}] }`

**`browser_accessibility_tree`** — Full ARIA tree, flat + nested.
Returns: `{ node_count, flat: [{depth, role, name}], tree }`

### EXECUTION

**`browser_run_playwright`** — Execute Python Playwright code. Receives `page`, `context`, `asyncio`.
Use `return {...}` to pass data back. Always use selectors from `browser_map_site`.

**`browser_execute_sequence`** — Typed JSON action sequence.
Actions: `goto` · `click` · `fill` · `select` · `press` · `hover` · `scroll` · `wait_for` · `snapshot` · `screenshot` · `evaluate`

**`browser_run_task`** — Natural language task via an LLM agent (browser-use).
Fallback for unstructured exploration when selectors are not yet known.

### VERIFICATION

**`browser_diff_pages`** — Compares two URLs, or before/after an action.
Returns: `{ added_qa, removed_qa, changed_texts, changed_counts }`

**`browser_capture_console`** — Console output during load and actions.
Returns: `{ errors, warnings, info, all_messages }`

**`browser_get_storage`** — localStorage, sessionStorage, cookies for a URL.

### AUTHENTICATION

**`browser_login`** — One-off login, saves session to a file path.

**`browser_session_save`** — Named login saved under `POLARIS_SESSIONS_DIR/{name}.json`.

**`browser_session_check`** — Verifies a named session is still active.

**`browser_session_list`** — Lists all saved sessions with metadata.

### UTILITIES

**`browser_screenshot`** — Returns `data:image/png;base64,...`

**`browser_get_page_content`** — Visible text up to 20,000 characters.

**`browser_get_help`** — Returns full documentation as a string.

---

## License

MIT
