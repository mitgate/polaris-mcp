"""Polaris MCP — Browser automation with Map First architecture.

Map First philosophy: always orient before you navigate.
Polaris maps any website completely before writing a single line of automation —
discovering selectors, API calls, hidden elements, and storage state, so the AI
acts with full knowledge instead of trial-and-error.

Architecture layers:
  KNOWLEDGE   → map_site, explore_page, intercept_network, accessibility_tree
  EXECUTION   → run_playwright, execute_sequence, run_task
  VERIFICATION → diff_pages, capture_console, get_storage
  AUTH         → login, session_save, session_check, session_list
  UTILITIES    → screenshot, get_page_content, get_help
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
import textwrap
import time as _time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

from browser_use import Agent
from browser_use.browser import BrowserProfile
from browser_use.llm.anthropic.chat import ChatAnthropic
from browser_use.llm.openai.chat import ChatOpenAI
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# ── configuration ─────────────────────────────────────────────────────────────

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8016"))
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
DEFAULT_MODEL = os.getenv("BROWSER_USE_MODEL", "gpt-4o-mini")
HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() not in ("false", "0", "no")
SESSIONS_DIR = os.getenv("POLARIS_SESSIONS_DIR", "/tmp/polaris_sessions")

_AUTH_PATTERNS = {"login", "auth", "keycloak", "signin", "sso", "realms"}

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("polaris-mcp")

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

════════════════════════════════════════════════════════════════
 LAYER 2 — EXECUTION  (after you know the selectors)
════════════════════════════════════════════════════════════════

browser_run_playwright(code, session_file, start_url, timeout_seconds)
  Executes Python Playwright code directly — no LLM in the loop.
  The code receives `page`, `context`, `asyncio`. Use `return {...}` to return data.
  USE: For precise, deterministic automation with selectors from the map.

browser_execute_sequence(steps_json, session_file, start_url, stop_on_error)
  Runs a typed JSON action sequence. Each step: {action, ...params}.
  Actions: goto · click · fill · select · press · hover · scroll ·
           wait_for · snapshot · screenshot · evaluate
  USE: Safer than run_playwright for predictable linear flows.

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
4. browser_intercept_network("https://app.com/dashboard", ...)
   → map every API endpoint the page calls
5. browser_execute_sequence('[{"action":"click","selector":"[data-qa=X]"}]', ...)
   → act with precision using real selectors
6. browser_diff_pages("https://app.com/dashboard", actions_code="...")
   → confirm the UI changed as expected
"""

mcp = FastMCP(
    "Polaris",
    host=MCP_HOST,
    port=MCP_PORT,
    instructions=_INSTRUCTIONS,
)


# ── telemetry helpers ─────────────────────────────────────────────────────────

def _start() -> float:
    return _time.monotonic()


def _elapsed_ms(t0: float) -> int:
    return round((_time.monotonic() - t0) * 1000)


async def _page_perf(page: Page) -> dict:
    try:
        return await page.evaluate("""() => {
            const nav = performance.getEntriesByType('navigation')[0];
            if (!nav) return {};
            const load = Math.round(nav.loadEventEnd - nav.startTime);
            const dom  = Math.round(nav.domContentLoadedEventEnd - nav.startTime);
            return load > 0 ? { page_load_ms: load, dom_ready_ms: dom } : {};
        }""")
    except Exception:
        return {}


async def _fetch_title(page: Page) -> str:
    try:
        return await page.title()
    except Exception:
        return ""


def _browser_state(
    page: Page,
    session_file: Optional[str],
    console_errors: int,
    perf: dict,
) -> dict:
    final_url = page.url
    state: dict = {
        "final_url": final_url,
        "title": "",
        "headless": HEADLESS,
        "session_used": bool(session_file and os.path.exists(session_file or "")),
        "redirect_detected": any(p in final_url for p in _AUTH_PATTERNS),
        "console_errors": console_errors,
    }
    if perf:
        state["performance"] = perf
    return state


def _polaris(
    tool: str,
    t0: float,
    browser: Optional[dict] = None,
    params: Optional[dict] = None,
    warnings: Optional[list] = None,
) -> dict:
    block: dict = {"tool": tool, "duration_ms": _elapsed_ms(t0)}
    if browser:
        block["browser"] = browser
    if params:
        block["effective_params"] = params
    if warnings:
        block["warnings"] = warnings
    return block


def _wrap(data: dict, polaris_block: dict) -> str:
    data["_polaris"] = polaris_block
    return json.dumps(data, ensure_ascii=False, indent=2)


def _attach_console_listener(page: Page) -> list:
    errors: list = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


# ── internal helpers ──────────────────────────────────────────────────────────

def _get_llm(model: str):
    if model.startswith("claude"):
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        return ChatAnthropic(model=model, api_key=ANTHROPIC_API_KEY)
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    return ChatOpenAI(model=model, api_key=OPENAI_API_KEY)


def _make_profile(headless: bool, storage_state: Optional[str] = None) -> BrowserProfile:
    return BrowserProfile(
        headless=headless,
        minimum_wait_page_load_time=1.0,
        wait_for_network_idle_page_load_time=3.0,
        wait_between_actions=0.5,
        storage_state=storage_state,
    )


async def _make_context(p, session_file: Optional[str]) -> tuple[Browser, BrowserContext]:
    browser = await p.chromium.launch(headless=HEADLESS)
    kwargs: dict = {"viewport": {"width": 1440, "height": 900}}
    if session_file and os.path.exists(session_file):
        kwargs["storage_state"] = session_file
    ctx = await browser.new_context(**kwargs)
    return browser, ctx


async def _exec_actions_code(page: Page, ctx: BrowserContext, code: str) -> None:
    cleaned = textwrap.dedent(code.rstrip())
    indented = "\n".join(f"    {line}" for line in cleaned.splitlines())
    fn_src = f"async def _actions(page, context, asyncio):\n{indented}\n"
    ns: dict = {"asyncio": asyncio, "json": json}
    exec(fn_src, ns)  # noqa: S102
    await ns["_actions"](page, ctx, asyncio)


_INVENTORY_JS = """
() => {
    const qaMap = {};
    document.querySelectorAll('[data-qa]').forEach(el => {
        const qa = el.getAttribute('data-qa');
        if (!qaMap[qa]) {
            qaMap[qa] = { qa, tag: el.tagName, count: 0, texts: [],
                          type: el.getAttribute('type') || null };
        }
        qaMap[qa].count++;
        const t = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
        if (t && !qaMap[qa].texts.includes(t)) qaMap[qa].texts.push(t);
    });

    const forms = [];
    document.querySelectorAll('form,[role=form]').forEach(form => {
        const inputs = [...form.querySelectorAll('input,textarea,select')].map(inp => ({
            tag: inp.tagName, id: inp.id || null, name: inp.name || null,
            type: inp.type || null, placeholder: inp.placeholder || null,
            data_qa: inp.getAttribute('data-qa') || null, required: inp.required,
        }));
        if (inputs.length) forms.push({ inputs });
    });
    const looseInputs = [...document.querySelectorAll(
        'input:not(form input),textarea:not(form textarea)'
    )].map(inp => ({
        tag: inp.tagName, id: inp.id || null, name: inp.name || null,
        type: inp.type || null, placeholder: inp.placeholder || null,
        data_qa: inp.getAttribute('data-qa') || null,
    }));
    if (looseInputs.length) forms.push({ loose: true, inputs: looseInputs });

    const links = [];
    document.querySelectorAll('a[href]').forEach(a => {
        try {
            const href = new URL(a.href, window.location.href);
            if (href.hostname === window.location.hostname) {
                const p = href.pathname + (href.search || '');
                if (!links.includes(p) && p !== window.location.pathname) links.push(p);
            }
        } catch (e) {}
    });

    const buttons = [];
    document.querySelectorAll('button:not([data-qa]),[role=button]:not([data-qa])').forEach(btn => {
        const t = (btn.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
        if (t && !buttons.find(b => b.text === t)) buttons.push({ text: t, tag: btn.tagName });
    });

    const seen = new Set();
    const triggers = [];
    document.querySelectorAll('[data-qa]').forEach(el => {
        const qa = el.getAttribute('data-qa');
        if (seen.has(qa)) return;
        const ql = qa.toLowerCase();
        const interactive = (
            ql.includes('trigger') || ql.includes('button') || ql.includes('toggle') ||
            ql.includes('tab') || ql.includes('menu') || ql.includes('dropdown') ||
            el.tagName === 'BUTTON' || el.getAttribute('role') === 'button' ||
            el.hasAttribute('aria-expanded') || el.hasAttribute('aria-haspopup')
        );
        if (interactive) { seen.add(qa); triggers.push({ qa, tag: el.tagName }); }
    });

    return {
        url: window.location.href,
        path: window.location.pathname + (window.location.search || ''),
        title: document.title,
        data_qa_elements: Object.values(qaMap),
        forms,
        navigation_links: links,
        buttons_no_qa: buttons,
        interactive_triggers: triggers,
    };
}
"""


async def _snapshot(page: Page) -> dict:
    return await page.evaluate(_INVENTORY_JS)


def _selector_index(pages: list[dict]) -> dict:
    index: dict = {}
    for pg in pages:
        for el in pg.get("data_qa_elements", []):
            qa = el["qa"]
            if qa not in index:
                index[qa] = {"count_total": 0, "pages_found": []}
            index[qa]["count_total"] += el["count"]
            if pg["path"] not in index[qa]["pages_found"]:
                index[qa]["pages_found"].append(pg["path"])
    return index


# ── browser_login ─────────────────────────────────────────────────────────────

@mcp.tool()
async def browser_login(
    login_url: str,
    username_value: str,
    password_value: str,
    username_selector: str = "input[type=email],input[name=username],#username",
    password_selector: str = "input[type=password],input[name=password],#password",
    submit_selector: str = "input[type=submit],button[type=submit],#kc-login",
    session_file: str = "/tmp/polaris_session.json",
    wait_after_login: float = 5.0,
) -> str:
    """Log in to a site via Playwright and save the session for later use.

    Recommended for OAuth/SSO sites where redirect flows make login tricky.
    After logging in, pass session_file to any tool that accepts it.

    For reusable named sessions prefer browser_session_save instead.

    Args:
        login_url: URL of the login page.
        username_value: Username or email to enter.
        password_value: Password to enter.
        username_selector: CSS selector(s) for the username field (comma = candidates).
        password_selector: CSS selector(s) for the password field.
        submit_selector: CSS selector(s) for the submit button.
        session_file: Path to save cookies and storage state.
        wait_after_login: Seconds to wait after submit before saving the session.

    Returns:
        JSON with final_url, title, session_file, and _polaris metadata.
    """
    t0 = _start()
    warnings: list[str] = []
    used_username_sel = username_selector.split(",")[0]
    used_password_sel = password_selector.split(",")[0]
    used_submit_sel = submit_selector.split(",")[0]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        await page.goto(login_url, wait_until="networkidle", timeout=30_000)

        matched_user = False
        for sel in username_selector.split(","):
            try:
                await page.fill(sel.strip(), username_value, timeout=3_000)
                used_username_sel = sel.strip()
                matched_user = True
                break
            except Exception:
                continue
        if not matched_user:
            warnings.append("No username selector matched — login may have failed")

        for sel in password_selector.split(","):
            try:
                await page.fill(sel.strip(), password_value, timeout=3_000)
                used_password_sel = sel.strip()
                break
            except Exception:
                continue

        for sel in submit_selector.split(","):
            try:
                await page.click(sel.strip(), timeout=3_000)
                used_submit_sel = sel.strip()
                break
            except Exception:
                continue

        await page.wait_for_load_state("networkidle", timeout=30_000)
        await asyncio.sleep(wait_after_login)

        perf = await _page_perf(page)
        title = await _fetch_title(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = title

        if bstate["redirect_detected"]:
            warnings.append("Final URL contains auth pattern — login may not have completed")

        await ctx.storage_state(path=session_file)
        await browser.close()

    return _wrap(
        {"session_file": session_file, "final_url": bstate["final_url"], "title": title},
        _polaris("browser_login", t0, browser=bstate,
                 params={"username_selector": used_username_sel,
                         "password_selector": used_password_sel,
                         "submit_selector": used_submit_sel,
                         "session_file": session_file,
                         "wait_after_login": wait_after_login},
                 warnings=warnings or None),
    )


# ── browser_map_site ──────────────────────────────────────────────────────────

@mcp.tool()
async def browser_map_site(
    url: str,
    session_file: Optional[str] = None,
    max_pages: int = 10,
    wait_seconds: float = 5.0,
    follow_links: bool = True,
) -> str:
    """Crawl a site via BFS and return a complete structured mental map as JSON.

    For each page the map includes:
    • data_qa_elements — every [data-qa] with tag, count, and visible texts
    • forms — all inputs with id, name, type, placeholder, required
    • navigation_links — internal links discovered (used for BFS)
    • buttons_no_qa — visible button text for elements without data-qa
    • interactive_triggers — elements likely to open dropdowns/modals/tabs

    The top-level selector_index cross-references every [data-qa] attribute
    found across the entire site with total counts and which pages it appears on.

    Call this BEFORE writing any automation on a new site.

    Args:
        url: Entry point URL for the crawl.
        session_file: Session file from browser_login or browser_session_save.
        max_pages: Maximum pages to crawl via BFS (default: 10).
        wait_seconds: Seconds to wait after loading each page (default: 5.0).
        follow_links: If False, maps only the entry URL without following links.

    Returns:
        JSON: { base_url, pages_mapped, pages, selector_index, _polaris }
    """
    t0 = _start()
    parsed = urlparse(url)
    base_origin = f"{parsed.scheme}://{parsed.hostname}"

    visited: set[str] = set()
    queue: list[str] = [url]
    pages: list[dict] = []
    warnings: list[str] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        while queue and len(pages) < max_pages:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            try:
                await page.goto(current, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(wait_seconds)

                if urlparse(page.url).hostname != parsed.hostname:
                    warnings.append(f"Redirected off-domain to {page.url} — crawl stopped")
                    break

                snap = await _snapshot(page)
                pages.append(snap)

                if follow_links:
                    for link in snap.get("navigation_links", []):
                        full = urljoin(base_origin, link)
                        if full not in visited and full not in queue:
                            queue.append(full)

            except Exception as e:
                warnings.append(f"Error mapping {current}: {e}")

        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    idx = _selector_index(pages)
    return _wrap(
        {
            "base_url": url,
            "pages_mapped": len(pages),
            "pages": pages,
            "selector_index": idx,
        },
        _polaris("browser_map_site", t0, browser=bstate,
                 params={"max_pages": max_pages, "follow_links": follow_links,
                         "wait_seconds": wait_seconds,
                         "session_used": bool(session_file and os.path.exists(session_file or ""))},
                 warnings=warnings or None),
    )


# ── browser_explore_page ──────────────────────────────────────────────────────

@mcp.tool()
async def browser_explore_page(
    url: str,
    session_file: Optional[str] = None,
    trigger_interactions: bool = True,
    max_triggers: int = 8,
    wait_seconds: float = 5.0,
) -> str:
    """Deep-inspect a single page, including elements only visible after interactions.

    Takes a static snapshot first, then (if trigger_interactions=True) clicks each
    interactive trigger, records which new [data-qa] elements appear, and closes
    the trigger before testing the next one.

    This reveals dropdowns, context menus, modals, and tab panels that are invisible
    in the static DOM — giving you the complete selector set for a page.

    Args:
        url: Page to inspect.
        session_file: Session file for authenticated sites.
        trigger_interactions: Click triggers to reveal hidden elements (default: True).
        max_triggers: Maximum triggers to test per page (default: 8).
        wait_seconds: Seconds to wait after loading (default: 5.0).

    Returns:
        JSON: { static_elements, revealed_after_interactions, total_revealed_qa, _polaris }
    """
    t0 = _start()
    warnings: list[str] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        static = await _snapshot(page)
        static_qa = {el["qa"] for el in static["data_qa_elements"]}
        revealed: list[dict] = []
        triggers_tested = 0

        if trigger_interactions:
            origin_url = page.url
            for trigger in static.get("interactive_triggers", [])[:max_triggers]:
                qa = trigger["qa"]
                ts = _start()
                try:
                    el = page.locator(f'[data-qa="{qa}"]').first
                    if not await el.is_visible(timeout=2_000):
                        continue

                    await el.click(force=True)
                    await asyncio.sleep(1.5)
                    triggers_tested += 1

                    if page.url != origin_url:
                        await page.goto(origin_url, wait_until="domcontentloaded", timeout=20_000)
                        await asyncio.sleep(wait_seconds)
                        continue

                    after = await _snapshot(page)
                    new_qa = {e["qa"] for e in after["data_qa_elements"]} - static_qa
                    if new_qa:
                        revealed.append({
                            "trigger_qa": qa,
                            "new_elements": [
                                e for e in after["data_qa_elements"] if e["qa"] in new_qa
                            ],
                            "trigger_duration_ms": _elapsed_ms(ts),
                        })

                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.8)
                except Exception as e:
                    warnings.append(f"Trigger '{qa}' could not be activated: {e}")
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass

        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    return _wrap(
        {
            "url": url,
            "path": static["path"],
            "title": static["title"],
            "static_elements": static,
            "revealed_after_interactions": revealed,
            "total_revealed_qa": sum(len(r["new_elements"]) for r in revealed),
        },
        _polaris("browser_explore_page", t0, browser=bstate,
                 params={"trigger_interactions": trigger_interactions,
                         "triggers_tested": triggers_tested,
                         "max_triggers": max_triggers},
                 warnings=warnings or None),
    )


# ── browser_intercept_network ─────────────────────────────────────────────────

@mcp.tool()
async def browser_intercept_network(
    url: str,
    session_file: Optional[str] = None,
    actions_code: Optional[str] = None,
    wait_seconds: float = 5.0,
    resource_types: str = "fetch,xhr",
    filter_url_contains: Optional[str] = None,
    max_body_chars: int = 2000,
) -> str:
    """Capture all network requests made during page load and optional actions.

    Intercepts XHR/fetch in real time — revealing which endpoints the frontend
    calls, with full request and response payloads. Use this to automatically
    discover and document the API layer of any web application.

    Args:
        url: Starting URL.
        session_file: Session file for authenticated sites.
        actions_code: Optional async Python code (receives `page`) to execute after
                      the page loads. Captures API calls triggered by actions.
        wait_seconds: Seconds to wait after page load (default: 5.0).
        resource_types: Comma-separated resource types to capture (default: "fetch,xhr").
        filter_url_contains: Only capture requests whose URL contains this string.
        max_body_chars: Truncate request/response bodies at this length (default: 2000).

    Returns:
        JSON: { requests_captured, entries, _polaris }
    """
    t0 = _start()
    target_types = {t.strip() for t in resource_types.split(",")}
    entries: list[dict] = []
    lock = asyncio.Lock()
    warnings: list[str] = []

    async def on_finished(request):
        if request.resource_type not in target_types:
            return
        if filter_url_contains and filter_url_contains not in request.url:
            return

        entry: dict = {
            "method": request.method,
            "url": request.url,
            "resource_type": request.resource_type,
            "request_body": None,
            "status": None,
            "response_body": None,
        }

        try:
            if request.post_data:
                try:
                    entry["request_body"] = json.loads(request.post_data)
                except Exception:
                    entry["request_body"] = request.post_data[:max_body_chars]
        except Exception:
            pass

        try:
            response = await request.response()
            if response:
                entry["status"] = response.status
                try:
                    body = await response.text()
                    try:
                        entry["response_body"] = json.loads(body)
                    except Exception:
                        entry["response_body"] = body[:max_body_chars]
                except Exception:
                    pass
        except Exception:
            pass

        async with lock:
            entries.append(entry)

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        page.on("requestfinished", on_finished)

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        if actions_code:
            await _exec_actions_code(page, ctx, actions_code)

        await asyncio.sleep(1.5)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    if not entries:
        warnings.append(
            f"No {resource_types} requests captured — "
            "check resource_types or filter_url_contains"
        )

    return _wrap(
        {"url": url, "requests_captured": len(entries), "entries": entries},
        _polaris("browser_intercept_network", t0, browser=bstate,
                 params={"resource_types": resource_types,
                         "filter_url_contains": filter_url_contains},
                 warnings=warnings or None),
    )


# ── browser_capture_console ───────────────────────────────────────────────────

@mcp.tool()
async def browser_capture_console(
    url: str,
    session_file: Optional[str] = None,
    actions_code: Optional[str] = None,
    wait_seconds: float = 5.0,
    levels: str = "log,warn,error,info",
) -> str:
    """Capture all browser console output during page load and optional actions.

    Collects console.log / warn / error / info messages and uncaught page errors.
    When something fails silently in the UI, the root cause is almost always visible
    here first.

    Args:
        url: Starting URL.
        session_file: Session file for authenticated sites.
        actions_code: Optional async Python code to execute after load (receives `page`).
        wait_seconds: Seconds to wait after page load (default: 5.0).
        levels: Comma-separated console levels to capture (default: "log,warn,error,info").

    Returns:
        JSON: { errors, warnings_console, info, all_messages, messages_captured, _polaris }
    """
    t0 = _start()
    target_levels = {lv.strip() for lv in levels.split(",")}
    messages: list[dict] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()

        def on_console(msg):
            if msg.type not in target_levels:
                return
            loc = msg.location or {}
            messages.append({
                "type": msg.type,
                "text": msg.text,
                "location": f"{loc.get('url', '')}:{loc.get('lineNumber', '')}",
            })

        def on_page_error(error):
            messages.append({"type": "pageerror", "text": str(error), "location": ""})

        page.on("console", on_console)
        page.on("pageerror", on_page_error)

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        if actions_code:
            await _exec_actions_code(page, ctx, actions_code)
            await asyncio.sleep(1.0)

        perf = await _page_perf(page)
        error_count = sum(1 for m in messages if m["type"] in ("error", "pageerror"))
        bstate = _browser_state(page, session_file, error_count, perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    return _wrap(
        {
            "url": url,
            "messages_captured": len(messages),
            "errors": [m for m in messages if m["type"] in ("error", "pageerror")],
            "warnings_console": [m for m in messages if m["type"] == "warn"],
            "info": [m for m in messages if m["type"] in ("log", "info")],
            "all_messages": messages,
        },
        _polaris("browser_capture_console", t0, browser=bstate,
                 params={"levels": levels}),
    )


# ── session manager ───────────────────────────────────────────────────────────

@mcp.tool()
async def browser_session_save(
    name: str,
    login_url: str,
    username_value: str,
    password_value: str,
    username_selector: str = "input[type=email],input[name=username],#username",
    password_selector: str = "input[type=password],input[name=password],#password",
    submit_selector: str = "input[type=submit],button[type=submit],#kc-login",
    wait_after_login: float = 5.0,
) -> str:
    """Log in and save the session under a friendly name for later reuse.

    Creates two files in POLARIS_SESSIONS_DIR (default: /tmp/polaris_sessions):
    • {name}.json       — Playwright storage state (cookies + localStorage)
    • {name}.meta.json  — metadata (username, login URL, timestamps)

    Pass the session file path from browser_session_list to any tool that
    accepts session_file.

    Args:
        name: Friendly session name (e.g. "admin", "qa-user", "read-only").
        login_url: URL of the login page.
        username_value: Username or email.
        password_value: Password.
        username_selector / password_selector / submit_selector: CSS selectors.
        wait_after_login: Seconds to wait after submit.

    Returns:
        JSON with name, session_file, login result, and _polaris metadata.
    """
    t0 = _start()
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    session_file = os.path.join(SESSIONS_DIR, f"{name}.json")
    meta_file = os.path.join(SESSIONS_DIR, f"{name}.meta.json")

    login_result_raw = await browser_login(
        login_url=login_url,
        username_value=username_value,
        password_value=password_value,
        username_selector=username_selector,
        password_selector=password_selector,
        submit_selector=submit_selector,
        session_file=session_file,
        wait_after_login=wait_after_login,
    )
    login_data = json.loads(login_result_raw)

    meta = {
        "name": name,
        "login_url": login_url,
        "username": username_value,
        "session_file": session_file,
        "created_at": datetime.now().isoformat(),
        "last_checked": None,
        "last_valid": True,
    }
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return _wrap(
        {"name": name, "session_file": session_file,
         "login_final_url": login_data.get("final_url"),
         "login_title": login_data.get("title")},
        _polaris("browser_session_save", t0,
                 params={"name": name, "login_url": login_url, "session_file": session_file}),
    )


@mcp.tool()
async def browser_session_check(
    name: str,
    check_url: str,
    login_redirect_patterns: str = "login,auth,keycloak,signin,sso",
) -> str:
    """Verify that a named session is still active (not expired or redirected to login).

    Navigates to check_url with the saved session. If the final URL contains any
    of the redirect patterns, the session is considered expired.

    Args:
        name: Session name (created by browser_session_save).
        check_url: A protected URL that requires authentication.
        login_redirect_patterns: Comma-separated URL substrings that indicate a
                                 redirect to the login page.

    Returns:
        JSON: { name, valid, final_url, reason, session_file, _polaris }
    """
    t0 = _start()
    session_file = os.path.join(SESSIONS_DIR, f"{name}.json")
    meta_file = os.path.join(SESSIONS_DIR, f"{name}.meta.json")

    if not os.path.exists(session_file):
        return _wrap(
            {"name": name, "valid": False,
             "reason": f"Session '{name}' not found in {SESSIONS_DIR}"},
            _polaris("browser_session_check", t0,
                     warnings=[f"Session file not found: {session_file}"]),
        )

    patterns = [pt.strip() for pt in login_redirect_patterns.split(",")]

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(check_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        final_url = page.url
        await browser.close()

    redirected = any(pt in final_url for pt in patterns)
    valid = not redirected

    if os.path.exists(meta_file):
        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)
        meta["last_checked"] = datetime.now().isoformat()
        meta["last_valid"] = valid
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    return _wrap(
        {
            "name": name,
            "valid": valid,
            "final_url": final_url,
            "session_file": session_file,
            "reason": "Redirected to login — session expired" if redirected else "Session is active",
        },
        _polaris("browser_session_check", t0, browser=bstate,
                 params={"name": name, "check_url": check_url}),
    )


@mcp.tool()
def browser_session_list() -> str:
    """List all saved named sessions with their metadata and last-known validity.

    Returns:
        JSON: { sessions, count, _polaris }
    """
    t0 = _start()
    sessions = []

    if os.path.exists(SESSIONS_DIR):
        for fname in sorted(os.listdir(SESSIONS_DIR)):
            if not fname.endswith(".meta.json"):
                continue
            try:
                with open(os.path.join(SESSIONS_DIR, fname), encoding="utf-8") as f:
                    meta = json.load(f)
                meta["session_file_exists"] = os.path.exists(meta.get("session_file", ""))
                sessions.append(meta)
            except Exception:
                continue

    return _wrap(
        {"sessions": sessions, "count": len(sessions)},
        _polaris("browser_session_list", t0,
                 params={"sessions_dir": SESSIONS_DIR}),
    )


# ── browser_diff_pages ────────────────────────────────────────────────────────

@mcp.tool()
async def browser_diff_pages(
    url_a: str,
    url_b: Optional[str] = None,
    actions_code: Optional[str] = None,
    session_file: Optional[str] = None,
    wait_seconds: float = 5.0,
) -> str:
    """Compare two page states and return a structured diff of UI elements.

    Two modes:
    • url_b provided   — compares two different URLs side by side.
    • actions_code provided — compares url_a before and after the actions run.

    The diff reports:
    • added_qa     — [data-qa] elements that appeared in state B
    • removed_qa   — [data-qa] elements that disappeared from state A
    • changed_texts  — elements whose visible text changed
    • changed_counts — elements whose count changed (e.g. a list grew)

    Args:
        url_a: First URL (initial state).
        url_b: Second URL for cross-page comparison (optional).
        actions_code: Async Python code to transition from state A to B (optional).
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading each state.

    Returns:
        JSON: { added_qa, removed_qa, changed_texts, changed_counts, summary, _polaris }
    """
    t0 = _start()
    warnings: list[str] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        await page.goto(url_a, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        snap_a = await _snapshot(page)

        if url_b:
            await page.goto(url_b, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(wait_seconds)
        elif actions_code:
            await _exec_actions_code(page, ctx, actions_code)
            await asyncio.sleep(1.5)
        else:
            warnings.append("Neither url_b nor actions_code provided — diff will be empty")

        snap_b = await _snapshot(page)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    qa_a = {el["qa"]: el for el in snap_a["data_qa_elements"]}
    qa_b = {el["qa"]: el for el in snap_b["data_qa_elements"]}

    added = [qa_b[qa] for qa in qa_b if qa not in qa_a]
    removed = [qa_a[qa] for qa in qa_a if qa not in qa_b]

    changed_texts, changed_counts = [], []
    for qa in qa_a:
        if qa not in qa_b:
            continue
        t_a = set(qa_a[qa].get("texts", []))
        t_b = set(qa_b[qa].get("texts", []))
        if t_a != t_b:
            changed_texts.append({"qa": qa, "before": list(t_a), "after": list(t_b)})
        c_a, c_b = qa_a[qa].get("count", 0), qa_b[qa].get("count", 0)
        if c_a != c_b:
            changed_counts.append({"qa": qa, "before": c_a, "after": c_b})

    if not added and not removed and not changed_texts and not changed_counts:
        warnings.append("No differences detected between the two states")

    return _wrap(
        {
            "snapshot_a": {"url": snap_a["url"], "title": snap_a["title"]},
            "snapshot_b": {"url": snap_b["url"], "title": snap_b["title"]},
            "added_qa": added,
            "removed_qa": removed,
            "changed_texts": changed_texts,
            "changed_counts": changed_counts,
            "summary": {
                "added": len(added),
                "removed": len(removed),
                "text_changes": len(changed_texts),
                "count_changes": len(changed_counts),
            },
        },
        _polaris("browser_diff_pages", t0, browser=bstate,
                 params={"url_a": url_a, "url_b": url_b},
                 warnings=warnings or None),
    )


# ── browser_accessibility_tree ────────────────────────────────────────────────

@mcp.tool()
async def browser_accessibility_tree(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 5.0,
    interesting_only: bool = True,
    max_depth: int = 6,
) -> str:
    """Extract the ARIA accessibility tree of a page.

    Works on any site regardless of data-qa conventions. Returns semantic roles
    and names that let you navigate by meaning rather than by CSS selector.
    Particularly useful for sites built with standard HTML semantics or
    accessible component libraries.

    Args:
        url: Page to inspect.
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading (default: 5.0).
        interesting_only: Filter out nodes with no role or name (default: True).
        max_depth: Maximum tree depth in the flat representation (default: 6).

    Returns:
        JSON: { node_count, flat, tree, _polaris }
    """
    t0 = _start()

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        tree = await page.accessibility.snapshot(interesting_only=interesting_only)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    def flatten(node: Optional[dict], depth: int = 0, acc: Optional[list] = None) -> list:
        if acc is None:
            acc = []
        if not node or depth > max_depth:
            return acc
        entry = {k: v for k, v in {
            "depth": depth,
            "role": node.get("role"),
            "name": node.get("name"),
            "value": node.get("value"),
            "description": node.get("description"),
            "checked": node.get("checked"),
            "expanded": node.get("expanded"),
            "disabled": node.get("disabled"),
        }.items() if v is not None and v != ""}
        acc.append(entry)
        for child in node.get("children", []):
            flatten(child, depth + 1, acc)
        return acc

    flat = flatten(tree)
    return _wrap(
        {"url": url, "node_count": len(flat), "flat": flat, "tree": tree},
        _polaris("browser_accessibility_tree", t0, browser=bstate,
                 params={"interesting_only": interesting_only, "max_depth": max_depth}),
    )


# ── browser_execute_sequence ──────────────────────────────────────────────────

@mcp.tool()
async def browser_execute_sequence(
    steps_json: str,
    session_file: Optional[str] = None,
    start_url: Optional[str] = None,
    stop_on_error: bool = True,
) -> str:
    """Execute a typed JSON action sequence in the browser.

    Safer than browser_run_playwright for predictable linear flows: each action
    has a declared type, built-in error handling, and a per-step result record.

    Available actions and their required fields:
      { "action": "goto",       "url": "https://..." }
      { "action": "click",      "selector": "[data-qa=X]", "force": false,
                                "index": 0, "wait_after": 1.0, "timeout": 10000 }
      { "action": "fill",       "selector": "#email", "value": "text" }
      { "action": "select",     "selector": "select", "value": "option" }
      { "action": "press",      "key": "Enter" }
      { "action": "hover",      "selector": "[data-qa=X]" }
      { "action": "scroll",     "x": 0, "y": 500 }
      { "action": "wait_for",   "selector": "[data-qa=X]", "state": "visible" }
      { "action": "wait_for",   "text": "Success", "timeout": 5000 }
      { "action": "wait_for",   "seconds": 2 }
      { "action": "snapshot" }
      { "action": "screenshot", "full_page": false }
      { "action": "evaluate",   "expression": "document.title" }

    Each step result includes duration_ms and selector_match_count (for click/fill/
    hover/wait_for steps) so the AI can reason about performance and selector quality.

    Args:
        steps_json: JSON array of step objects.
        session_file: Session file for authenticated sites.
        start_url: URL to navigate to before running the sequence.
        stop_on_error: Stop on the first failed step (default: True).

    Returns:
        JSON: { steps_total, steps_succeeded, final_url, results, _polaris }
    """
    t0 = _start()
    try:
        steps: list[dict] = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return _wrap(
            {"success": False, "error": f"Invalid JSON in steps_json: {e}"},
            _polaris("browser_execute_sequence", t0),
        )

    results: list[dict] = []
    seq_warnings: list[str] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        if start_url:
            await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)

        for i, step in enumerate(steps):
            action = step.get("action", "")
            st = _start()
            sr: dict = {
                "step": i + 1,
                "action": action,
                "success": False,
                "duration_ms": 0,
                "selector_match_count": None,
                "result": None,
                "error": None,
            }

            try:
                if action == "goto":
                    await page.goto(step["url"], wait_until="domcontentloaded", timeout=30_000)
                    sr["result"] = {"url": page.url}

                elif action in ("click", "fill", "hover"):
                    sel = step["selector"]
                    count = await page.locator(sel).count()
                    sr["selector_match_count"] = count
                    if count == 0:
                        seq_warnings.append(
                            f"Step {i+1} ({action}): selector '{sel}' matched 0 elements"
                        )
                        raise ValueError(f"Selector '{sel}' matched 0 elements")
                    if count > 1:
                        seq_warnings.append(
                            f"Step {i+1} ({action}): selector '{sel}' matched {count} elements, used first"
                        )

                    if action == "click":
                        el = page.locator(sel)
                        if step.get("index"):
                            el = el.nth(step["index"])
                        await el.click(force=step.get("force", False),
                                       timeout=step.get("timeout", 10_000))
                        await asyncio.sleep(step.get("wait_after", 1.0))
                        sr["result"] = {"url": page.url}
                    elif action == "fill":
                        el = page.locator(sel)
                        await el.wait_for(state="visible", timeout=step.get("timeout", 10_000))
                        await el.clear()
                        await el.fill(step["value"])
                    elif action == "hover":
                        await page.locator(sel).hover()

                elif action == "select":
                    await page.locator(step["selector"]).select_option(step["value"])

                elif action == "press":
                    await page.keyboard.press(step["key"])

                elif action == "scroll":
                    x, y = step.get("x", 0), step.get("y", 500)
                    await page.evaluate(f"window.scrollBy({x}, {y})")

                elif action == "wait_for":
                    timeout = step.get("timeout", 10_000)
                    if sel := step.get("selector"):
                        count = await page.locator(sel).count()
                        sr["selector_match_count"] = count
                        await page.locator(sel).wait_for(
                            state=step.get("state", "visible"), timeout=timeout
                        )
                    elif txt := step.get("text"):
                        await page.get_by_text(txt).wait_for(state="visible", timeout=timeout)
                    elif secs := step.get("seconds"):
                        await asyncio.sleep(secs)

                elif action == "snapshot":
                    sr["result"] = await _snapshot(page)

                elif action == "screenshot":
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                        tmp = f.name
                    await page.screenshot(path=tmp, full_page=step.get("full_page", False))
                    with open(tmp, "rb") as f:
                        sr["result"] = (
                            f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
                        )
                    os.unlink(tmp)

                elif action == "evaluate":
                    sr["result"] = await page.evaluate(step["expression"])

                else:
                    raise ValueError(f"Unknown action: '{action}'")

                sr["success"] = True

            except Exception as e:
                sr["error"] = str(e)
                sr["duration_ms"] = _elapsed_ms(st)
                results.append(sr)
                if stop_on_error:
                    break
                continue

            sr["duration_ms"] = _elapsed_ms(st)
            results.append(sr)

        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        final_url = page.url
        await browser.close()

    return _wrap(
        {
            "steps_total": len(steps),
            "steps_executed": len(results),
            "steps_succeeded": sum(1 for r in results if r["success"]),
            "final_url": final_url,
            "results": results,
        },
        _polaris("browser_execute_sequence", t0, browser=bstate,
                 params={"steps_total": len(steps), "stop_on_error": stop_on_error,
                         "session_used": bool(session_file and os.path.exists(session_file or ""))},
                 warnings=seq_warnings or None),
    )


# ── browser_get_storage ───────────────────────────────────────────────────────

@mcp.tool()
async def browser_get_storage(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 3.0,
) -> str:
    """Read localStorage, sessionStorage, and cookies for a URL.

    Useful for inspecting auth tokens, persisted SPA state, feature flags,
    and any data the application stores in the browser between sessions.

    Args:
        url: Page to inspect.
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading (default: 3.0).

    Returns:
        JSON: { local_storage, session_storage, cookies, *_keys, cookies_count, _polaris }
    """
    t0 = _start()

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        storage = await page.evaluate("""
            () => ({
                localStorage: Object.fromEntries(
                    Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])
                ),
                sessionStorage: Object.fromEntries(
                    Object.keys(sessionStorage).map(k => [k, sessionStorage.getItem(k)])
                ),
            })
        """)
        cookies = await ctx.cookies()
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    ls = storage.get("localStorage", {})
    ss = storage.get("sessionStorage", {})
    return _wrap(
        {
            "url": url,
            "local_storage": ls,
            "session_storage": ss,
            "cookies": cookies,
            "local_storage_keys": list(ls.keys()),
            "session_storage_keys": list(ss.keys()),
            "cookies_count": len(cookies),
        },
        _polaris("browser_get_storage", t0, browser=bstate),
    )


# ── browser_run_playwright ────────────────────────────────────────────────────

@mcp.tool()
async def browser_run_playwright(
    code: str,
    session_file: Optional[str] = None,
    start_url: Optional[str] = None,
    timeout_seconds: int = 60,
) -> str:
    """Execute Python Playwright code directly — no LLM in the automation loop.

    The code body receives `page` (Playwright Page), `context` (BrowserContext),
    and `asyncio`. Write it as the body of an async function. Use `return` to
    return structured data back to the caller.

    Always use selectors discovered via browser_map_site or browser_explore_page
    rather than guessing.

    Example:
        count = await page.locator('[data-qa="ItemCard"]').count()
        title = await page.title()
        return {"title": title, "item_count": count}

    Args:
        code: Async function body with access to `page`, `context`, `asyncio`.
        session_file: Session file for authenticated sites.
        start_url: URL to navigate to before running the code.
        timeout_seconds: Total execution timeout in seconds (default: 60).

    Returns:
        JSON: { success, result, final_url, error, _polaris }
    """
    t0 = _start()
    cleaned = textwrap.dedent(code.rstrip())
    indented = "\n".join(f"    {line}" for line in cleaned.splitlines())
    fn_src = f"async def _scenario(page, context, asyncio):\n{indented}\n"

    ns: dict = {"asyncio": asyncio, "json": json}
    try:
        exec(fn_src, ns)  # noqa: S102
    except SyntaxError as e:
        return _wrap(
            {"success": False, "result": None, "error": f"SyntaxError: {e}"},
            _polaris("browser_run_playwright", t0),
        )

    bstate: dict = {}
    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)

        try:
            if start_url:
                await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(3)

            result = await asyncio.wait_for(
                ns["_scenario"](page, ctx, asyncio),
                timeout=timeout_seconds,
            )
            perf = await _page_perf(page)
            bstate = _browser_state(page, session_file, len(errors), perf)
            bstate["title"] = await _fetch_title(page)
            output = {"success": True, "result": result, "final_url": page.url, "error": None}

        except asyncio.TimeoutError:
            perf = await _page_perf(page)
            bstate = _browser_state(page, session_file, len(errors), perf)
            bstate["title"] = await _fetch_title(page)
            output = {
                "success": False, "result": None, "final_url": page.url,
                "error": f"Execution timed out after {timeout_seconds}s",
            }
        except Exception as e:
            perf = await _page_perf(page)
            bstate = _browser_state(page, session_file, len(errors), perf)
            bstate["title"] = await _fetch_title(page)
            output = {"success": False, "result": None, "final_url": page.url, "error": str(e)}
        finally:
            await browser.close()

    return _wrap(
        output,
        _polaris("browser_run_playwright", t0, browser=bstate,
                 params={"timeout_seconds": timeout_seconds, "start_url": start_url,
                         "session_used": bool(session_file and os.path.exists(session_file or ""))}),
    )


# ── browser_run_task ──────────────────────────────────────────────────────────

@mcp.tool()
async def browser_run_task(
    task: str,
    start_url: Optional[str] = None,
    model: Optional[str] = None,
    max_steps: int = 30,
    sensitive_data: Optional[dict] = None,
    session_file: Optional[str] = None,
) -> str:
    """Execute a natural-language automation task using an LLM agent (browser-use).

    This is the unstructured fallback — use it for initial exploration when selectors
    are not yet known. For anything requiring precision or repeatability, prefer
    browser_run_playwright or browser_execute_sequence with selectors from browser_map_site.

    Args:
        task: Natural language description of what to do.
              Use {placeholder} syntax to reference keys in sensitive_data.
        start_url: URL to navigate to before executing the task.
        model: LLM model ID (default: gpt-4o-mini). Prefix with "claude-" for Anthropic.
        max_steps: Maximum agent action steps (default: 30).
        sensitive_data: Dict of sensitive values not exposed to the LLM internally.
        session_file: Session file for authenticated sites.

    Returns:
        JSON: { result, model_used, _polaris }
    """
    t0 = _start()
    use_model = model or DEFAULT_MODEL
    llm = _get_llm(use_model)
    full_task = f"Navigate to {start_url}. Then: {task}" if start_url else task
    profile = _make_profile(HEADLESS, storage_state=session_file)

    agent = Agent(
        task=full_task,
        llm=llm,
        browser_profile=profile,
        max_actions_per_step=5,
        sensitive_data=sensitive_data or {},
    )
    run_result = await agent.run(max_steps=max_steps)
    final = run_result.final_result()
    if not final:
        history = run_result.action_results()
        final = str(history[-1].extracted_content or history[-1].error or "") if history else ""

    return _wrap(
        {"result": str(final), "model_used": use_model},
        _polaris("browser_run_task", t0,
                 params={"model": use_model, "max_steps": max_steps,
                         "session_used": bool(session_file and os.path.exists(session_file or ""))}),
    )


# ── browser_screenshot ────────────────────────────────────────────────────────

@mcp.tool()
async def browser_screenshot(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 3.0,
    full_page: bool = False,
) -> str:
    """Capture a screenshot of a URL and return it as a base64 PNG.

    Args:
        url: URL to capture.
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading (default: 3.0).
        full_page: If True, captures the full scrollable page (default: False = viewport only).

    Returns:
        JSON: { image: "data:image/png;base64,...", _polaris }
    """
    t0 = _start()

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        await page.screenshot(path=tmp, full_page=full_page)
        await browser.close()

    with open(tmp, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    os.unlink(tmp)

    return _wrap(
        {"image": f"data:image/png;base64,{data}"},
        _polaris("browser_screenshot", t0, browser=bstate,
                 params={"full_page": full_page, "wait_seconds": wait_seconds}),
    )


# ── browser_get_page_content ──────────────────────────────────────────────────

@mcp.tool()
async def browser_get_page_content(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 3.0,
) -> str:
    """Load a URL and return its visible text content (no HTML), up to 20,000 characters.

    Args:
        url: URL to load.
        session_file: Session file for authenticated sites.
        wait_seconds: Seconds to wait after loading (default: 3.0).

    Returns:
        JSON: { text, chars, truncated, _polaris }
    """
    t0 = _start()

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        errors = _attach_console_listener(page)
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        raw = await page.evaluate("""
            () => {
                document.querySelectorAll('script,style,noscript').forEach(el => el.remove());
                return (document.body || document.documentElement).innerText;
            }
        """)
        perf = await _page_perf(page)
        bstate = _browser_state(page, session_file, len(errors), perf)
        bstate["title"] = await _fetch_title(page)
        await browser.close()

    cleaned = "\n".join(line for line in raw.splitlines() if line.strip())
    truncated = len(cleaned) > 20_000
    text = cleaned[:20_000]

    return _wrap(
        {"text": text, "chars": len(text), "truncated": truncated},
        _polaris("browser_get_page_content", t0, browser=bstate),
    )


# ── browser_get_help ──────────────────────────────────────────────────────────

@mcp.tool()
def browser_get_help() -> str:
    """Return full Polaris MCP documentation as a string."""
    return _INSTRUCTIONS


if __name__ == "__main__":
    mcp.run(transport=MCP_TRANSPORT)
