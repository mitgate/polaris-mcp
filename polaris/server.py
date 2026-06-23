"""FastMCP server instance and capability instructions for Polaris MCP."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from polaris.config import MCP_HOST, MCP_PORT

_INSTRUCTIONS = """
You are connected to Polaris MCP — a browser automation server built on the Map First philosophy.

CORE PRINCIPLE: Always map a site before automating it. Call browser_map_site first to get the
complete selector inventory, then use browser_explore_page to discover hidden elements, then
browser_intercept_network to understand the API layer. Only after that should you write automation.

════════════════════════════════════════════════════════════════
 LAYER 1 — KNOWLEDGE  (always start here)
════════════════════════════════════════════════════════════════

browser_map_site(url, session_file, max_pages, wait_seconds, follow_links)
  BFS crawl of the entire site. Returns a JSON map with:
  • data_qa_elements per page — tag, count, visible texts
  • forms and inputs — id, name, type, placeholder
  • internal navigation links discovered
  • interactive_triggers (buttons, tabs, dropdowns)
  • selector_index — cross-page index of all [data-qa] attributes
  USE: Before writing any automation on a new site or page.

browser_explore_page(url, session_file, trigger_interactions, max_triggers, wait_seconds)
  Deep inspection of a single page. Clicks each interactive trigger and records
  which new [data-qa] elements appear — revealing dropdowns, modals, tabs, and
  context menus that are invisible in the static snapshot.
  USE: When map_site shows a trigger but you need to know what it reveals.

browser_intercept_network(url, session_file, actions_code, resource_types, filter_url_contains)
  Captures all XHR/fetch requests during page load and optional actions.
  Returns: method, URL, status, request body, response body (truncated).
  USE: To discover API endpoints the frontend calls — map the API layer automatically.

browser_accessibility_tree(url, session_file, interesting_only, max_depth)
  Extracts the full ARIA accessibility tree. Works on any site regardless of
  data-qa conventions. Returns flat + nested tree with roles and names.
  USE: On sites without data-qa, or to navigate by semantic meaning.

browser_get_external_resources(url, session_file, wait_seconds, include_requests)
  Collects every external origin a page contacts — via DOM scan (<script>,
  <link>, <img>, <iframe>, <a href>) and live network interception.
  Returns external_origins grouped by hostname with a category label
  (analytics, cdn, api, font, social, ads, other) and full URL list.
  USE: Discover the real API base URL, audit third-party tracking, or map
  the backend before using browser_intercept_network.

════════════════════════════════════════════════════════════════
 LAYER 2 — EXECUTION  (after you know the selectors)
════════════════════════════════════════════════════════════════

browser_inject_js(code, url, session_file, persistent, wait_seconds)
  Inject and execute arbitrary JavaScript in the page context. Returns the
  result (must be JSON-serializable). With persistent=True the script is
  re-executed on every navigation within the session (addInitScript) —
  useful for interceptors, helpers, or globals that survive SPA route changes.
  USE: Extract data buried in JS state, reconstruct split auth tokens from
  cookies, override window functions, set up event listeners.

browser_run_playwright(code, session_file, start_url, timeout_seconds)
  Executes Python Playwright code directly — no LLM in the loop.
  The code receives `page`, `context`, `asyncio`. Use `return {...}` to return data.
  USE: For precise, deterministic automation with selectors from the map.

browser_execute_sequence(steps_json, session_file, start_url, stop_on_error)
  Runs a typed JSON action sequence. Each step: {action, ...params}.
  Actions: goto · click · fill · select · press · hover · scroll ·
           wait_for · snapshot · screenshot · evaluate
  USE: Safer than run_playwright for predictable linear flows.

browser_auto_sequence(goal, url, session_file, model, explore, dry_run)
  Map First em uma única chamada: mapeia a página, explora triggers escondidos,
  gera automaticamente uma sequência de steps via LLM usando o mapa como contexto,
  e executa.
  Diferente de browser_run_task (LLM navega cegamente): aqui o LLM vê o mapa
  completo ANTES de planejar — gera a sequência toda de uma vez e executa de
  forma determinística.
  Com dry_run=True retorna apenas os steps gerados sem executar.
  USE: When you know the goal but not the selectors — Polaris figures out the how.
  ADVANTAGE OVER browser_run_task: the LLM sees the full selector map before
  planning, not just the current DOM; generates the entire sequence at once.

browser_run_task(task, start_url, model, max_steps, sensitive_data, session_file)
  LLM-driven automation in natural language (browser-use agent).
  USE: Fallback for unstructured exploration when selectors are not yet known.

════════════════════════════════════════════════════════════════
 LAYER 3 — VERIFICATION  (confirm what happened)
════════════════════════════════════════════════════════════════

browser_diff_pages(url_a, url_b, actions_code, session_file, wait_seconds)
  Structural diff between two page states.
  Returns: added_qa · removed_qa · changed_texts · changed_counts.
  Modes: compare two URLs, or before/after an action on the same URL.
  USE: To assert that an action produced the expected UI change.

browser_capture_console(url, session_file, actions_code, levels)
  Captures browser console output (log, warn, error, pageerror) during navigation.
  USE: To diagnose silent frontend failures — errors always appear here first.

browser_get_storage(url, session_file, wait_seconds)
  Reads localStorage, sessionStorage, and cookies for a URL.
  USE: To inspect auth tokens, SPA state, cached preferences.

════════════════════════════════════════════════════════════════
 AUTHENTICATION
════════════════════════════════════════════════════════════════

browser_login(login_url, username_value, password_value, session_file, ...)
  One-off login via Playwright. Saves session to a file path you specify.

browser_session_save(name, login_url, username_value, password_value, ...)
  Named login — saves session to POLARIS_SESSIONS_DIR/{name}.json.
  Preferred over browser_login for reusable sessions.

browser_session_check(name, check_url, login_redirect_patterns)
  Verifies a named session is still active (not expired or redirected to login).
  Call before using a session that may have aged out.

browser_session_list()
  Lists all saved named sessions with metadata and last-valid status.

════════════════════════════════════════════════════════════════
 UTILITIES
════════════════════════════════════════════════════════════════

browser_screenshot(url, session_file, wait_seconds, full_page)
  Returns a base64 PNG screenshot wrapped in JSON.

browser_get_page_content(url, session_file, wait_seconds)
  Returns visible page text (no HTML), truncated at 20,000 characters.

browser_get_help()
  Returns this documentation as a string.

════════════════════════════════════════════════════════════════
 _polaris TELEMETRY (present in every tool response)
════════════════════════════════════════════════════════════════

Every tool response includes a `_polaris` block with observability data:

  _polaris.tool              — tool name that was called
  _polaris.duration_ms       — total wall-clock time in milliseconds
  _polaris.browser           — browser state at end of execution:
    .final_url               — where the browser ended up
    .title                   — page title at end
    .headless                — whether browser ran headless
    .session_used            — whether a session file was loaded
    .redirect_detected       — True if final_url looks like a login/auth redirect
    .console_errors          — count of JS errors during execution
    .performance             — page_load_ms and dom_ready_ms from the Performance API
  _polaris.effective_params  — key parameters actually resolved and used
  _polaris.warnings          — list of non-fatal issues detected during execution

Use _polaris to reason about:
  • Performance: page_load_ms high → site under load or misconfigured
  • Session health: redirect_detected=True → re-authenticate before retrying
  • Selector reliability: warnings about match counts → re-map the page
  • Step bottlenecks: per-step duration_ms in execute_sequence results

Per-step telemetry in browser_execute_sequence results:
  { step, action, success, duration_ms, selector_match_count, result, error }
  selector_match_count = 0 → broken selector
  selector_match_count > 1 → ambiguous selector, first element was used

════════════════════════════════════════════════════════════════
 RECOMMENDED WORKFLOW FOR A NEW SITE
════════════════════════════════════════════════════════════════

1. browser_session_save("myapp", "https://app.com/login", "user", "pass")
2. browser_map_site("https://app.com", session_file=".../myapp.json")
   → read selector_index to know every [data-qa] and where it lives
3. browser_explore_page("https://app.com/dashboard", trigger_interactions=True)
   → discover what dropdowns and modals are hidden behind triggers
4. browser_get_external_resources("https://app.com/dashboard", ...)
   → find the real API base URL and all external origins
5. browser_intercept_network("https://app.com/dashboard", filter_url_contains="api.")
   → capture exact endpoint calls and payloads
6. browser_inject_js("document.cookie", url="https://app.com/dashboard", ...)
   → extract JS-accessible data (tokens, state) directly from the page

Option A — precise, selector-based:
7. browser_execute_sequence('[{"action":"click","selector":"[data-qa=X]"}]', ...)
   → act with precision using real selectors
8. browser_diff_pages("https://app.com/dashboard", actions_code="...")
   → confirm the UI changed as expected

Option B — goal-driven (when you know what, not how):
7. browser_auto_sequence("fill the search form with 'Paris' and submit", url="https://app.com/dashboard")
   → Polaris maps the page, generates the steps via LLM, and executes
"""

mcp = FastMCP(
    "Polaris",
    host=MCP_HOST,
    port=MCP_PORT,
    instructions=_INSTRUCTIONS,
)
