"""Browser Use MCP — automação de browser + mapeamento de sites.

Arquitetura Map First → Execute → Verify:

  CONHECIMENTO
    browser_map_site          crawla o site, retorna selector_index completo
    browser_explore_page      inspeção profunda de 1 página (elementos ocultos)
    browser_intercept_network captura requisições XHR/fetch durante navegação
    browser_accessibility_tree árvore ARIA — funciona em qualquer site

  EXECUÇÃO
    browser_run_playwright    executa código Playwright gerado pela IA
    browser_execute_sequence  executa sequência tipada de ações (JSON)
    browser_run_task          fallback LLM para tarefas não-estruturadas

  VERIFICAÇÃO
    browser_diff_pages        diff estruturado entre dois estados de página
    browser_capture_console   captura console do browser (erros, logs)
    browser_get_storage       lê localStorage, sessionStorage, cookies

  AUTENTICAÇÃO
    browser_login             login pontual, salva sessão em arquivo
    browser_session_save      login nomeado persistente
    browser_session_check     valida se sessão ainda é ativa
    browser_session_list      lista todas as sessões salvas

  UTILITÁRIOS
    browser_screenshot        screenshot como base64 PNG
    browser_get_page_content  texto visível da página
    browser_get_help          documentação completa
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
import textwrap
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

from browser_use import Agent
from browser_use.browser import BrowserProfile
from browser_use.llm.anthropic.chat import ChatAnthropic
from browser_use.llm.openai.chat import ChatOpenAI
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

# ── configuração ──────────────────────────────────────────────────────────────

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8016"))
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
DEFAULT_MODEL = os.getenv("BROWSER_USE_MODEL", "gpt-4o-mini")
HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() not in ("false", "0", "no")
SESSIONS_DIR = os.getenv("BROWSER_MCP_SESSIONS_DIR", "/tmp/browser_mcp_sessions")

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("browser-mcp")

mcp = FastMCP("BrowserUsePythonLocal", host=MCP_HOST, port=MCP_PORT)


# ── helpers internos ──────────────────────────────────────────────────────────

def _get_llm(model: str):
    if model.startswith("claude"):
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY não configurado")
        return ChatAnthropic(model=model, api_key=ANTHROPIC_API_KEY)
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não configurado")
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
    """Executa código de ações inline (parâmetro actions_code)."""
    cleaned = textwrap.dedent(code.rstrip())
    indented = "\n".join(f"    {line}" for line in cleaned.splitlines())
    fn_src = f"async def _actions(page, context, asyncio):\n{indented}\n"
    ns: dict = {"asyncio": asyncio, "json": json}
    exec(fn_src, ns)  # noqa: S102
    await ns["_actions"](page, ctx, asyncio)


# JavaScript que extrai inventário completo de uma página
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
    session_file: str = "/tmp/browser_mcp_session.json",
    wait_after_login: float = 5.0,
) -> str:
    """Faz login em um site via Playwright e salva a sessão para uso posterior.

    Recomendado para sites com OAuth/SSO. Após login, passe session_file para
    browser_run_playwright, browser_map_site ou browser_run_task.

    Args:
        login_url: URL da página de login.
        username_value: E-mail ou usuário.
        password_value: Senha.
        username_selector: CSS selector do campo de usuário (vírgula = candidatos).
        password_selector: CSS selector do campo de senha.
        submit_selector: CSS selector do botão de submit.
        session_file: Onde salvar cookies/storage.
        wait_after_login: Segundos de espera após submit.

    Returns:
        URL final, título e caminho do arquivo de sessão.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        await page.goto(login_url, wait_until="networkidle", timeout=30_000)

        for sel in username_selector.split(","):
            try:
                await page.fill(sel.strip(), username_value, timeout=3_000)
                break
            except Exception:
                continue

        for sel in password_selector.split(","):
            try:
                await page.fill(sel.strip(), password_value, timeout=3_000)
                break
            except Exception:
                continue

        for sel in submit_selector.split(","):
            try:
                await page.click(sel.strip(), timeout=3_000)
                break
            except Exception:
                continue

        await page.wait_for_load_state("networkidle", timeout=30_000)
        await asyncio.sleep(wait_after_login)

        final_url = page.url
        title = await page.title()
        await ctx.storage_state(path=session_file)
        await browser.close()

    return f"Login concluído. URL={final_url} | Título={title} | Sessão={session_file}"


# ── browser_map_site ──────────────────────────────────────────────────────────

@mcp.tool()
async def browser_map_site(
    url: str,
    session_file: Optional[str] = None,
    max_pages: int = 10,
    wait_seconds: float = 5.0,
    follow_links: bool = True,
) -> str:
    """Mapeia um site inteiro via crawl BFS e retorna mapa mental estruturado em JSON.

    Para cada página extrai: data-qa elements (tag, count, textos), forms/inputs,
    links internos, botões sem data-qa e triggers interativos.
    Retorna também selector_index cruzado com todos os data-qa encontrados no site.

    Use ANTES de escrever qualquer automação para conhecer os seletores reais.

    Args:
        url: URL de início do mapeamento.
        session_file: Sessão para sites autenticados (gerada por browser_login).
        max_pages: Limite de páginas a mapear via BFS (padrão: 10).
        wait_seconds: Espera após carregar cada página (padrão: 5.0).
        follow_links: Se False, mapeia só a URL inicial.

    Returns:
        JSON com { base_url, pages_mapped, pages, selector_index }
    """
    parsed = urlparse(url)
    base_origin = f"{parsed.scheme}://{parsed.hostname}"

    visited: set[str] = set()
    queue: list[str] = [url]
    pages: list[dict] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()

        while queue and len(pages) < max_pages:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            try:
                await page.goto(current, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(wait_seconds)

                if urlparse(page.url).hostname != parsed.hostname:
                    logger.warning("Redirecionado para fora do domínio: %s", page.url)
                    break

                snap = await _snapshot(page)
                pages.append(snap)

                if follow_links:
                    for link in snap.get("navigation_links", []):
                        full = urljoin(base_origin, link)
                        if full not in visited and full not in queue:
                            queue.append(full)

            except Exception as e:
                logger.warning("Erro ao mapear %s: %s", current, e)

        await browser.close()

    return json.dumps({
        "base_url": url,
        "session_used": bool(session_file and os.path.exists(session_file or "")),
        "pages_mapped": len(pages),
        "pages": pages,
        "selector_index": _selector_index(pages),
    }, ensure_ascii=False, indent=2)


# ── browser_explore_page ──────────────────────────────────────────────────────

@mcp.tool()
async def browser_explore_page(
    url: str,
    session_file: Optional[str] = None,
    trigger_interactions: bool = True,
    max_triggers: int = 8,
    wait_seconds: float = 5.0,
) -> str:
    """Inspeção profunda de uma página, incluindo elementos visíveis só após interações.

    Captura estado estático e depois (se trigger_interactions=True) clica em cada
    trigger interativo, registra quais novos [data-qa] aparecem (dropdowns, modais,
    tabs) e fecha antes de testar o próximo.

    Args:
        url: Página a inspecionar.
        session_file: Sessão para autenticação.
        trigger_interactions: Se True, clica em triggers para revelar hidden elements.
        max_triggers: Máx triggers a testar (padrão: 8).
        wait_seconds: Espera após carregar (padrão: 5.0).

    Returns:
        JSON com { static_elements, revealed_after_interactions, total_revealed_qa }
    """
    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        static = await _snapshot(page)
        static_qa = {el["qa"] for el in static["data_qa_elements"]}
        revealed: list[dict] = []

        if trigger_interactions:
            origin_url = page.url
            for trigger in static.get("interactive_triggers", [])[:max_triggers]:
                qa = trigger["qa"]
                try:
                    el = page.locator(f'[data-qa="{qa}"]').first
                    if not await el.is_visible(timeout=2_000):
                        continue

                    await el.click(force=True)
                    await asyncio.sleep(1.5)

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
                        })

                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.8)
                except Exception as e:
                    logger.debug("Trigger '%s' falhou: %s", qa, e)
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass

        await browser.close()

    return json.dumps({
        "url": url,
        "path": static["path"],
        "title": static["title"],
        "static_elements": static,
        "revealed_after_interactions": revealed,
        "total_revealed_qa": sum(len(r["new_elements"]) for r in revealed),
    }, ensure_ascii=False, indent=2)


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
    """Navega para uma URL, executa ações opcionais e captura todas as requisições de rede.

    Intercepta XHR/fetch em tempo real, revelando quais endpoints o frontend chama,
    com payloads de request e response. Ideal para mapear a API automaticamente.

    Args:
        url: URL inicial.
        session_file: Sessão para autenticação.
        actions_code: Código Python async (com `page`) a executar após carregar.
                      Use para disparar ações e capturar as chamadas resultantes.
        wait_seconds: Espera após carregar a página (padrão: 5.0).
        resource_types: Tipos a capturar, vírgula-separados (padrão: "fetch,xhr").
        filter_url_contains: Filtra entradas cuja URL contém esta string (opcional).
        max_body_chars: Trunca bodies de request/response (padrão: 2000).

    Returns:
        JSON com { requests_captured, entries: [{method, url, status, request_body, response_body}] }
    """
    target_types = {t.strip() for t in resource_types.split(",")}
    entries: list[dict] = []
    lock = asyncio.Lock()

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
        page.on("requestfinished", on_finished)

        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)

        if actions_code:
            await _exec_actions_code(page, ctx, actions_code)

        await asyncio.sleep(1.5)  # aguarda requests pendentes
        await browser.close()

    return json.dumps({
        "url": url,
        "requests_captured": len(entries),
        "entries": entries,
    }, ensure_ascii=False, indent=2)


# ── browser_capture_console ───────────────────────────────────────────────────

@mcp.tool()
async def browser_capture_console(
    url: str,
    session_file: Optional[str] = None,
    actions_code: Optional[str] = None,
    wait_seconds: float = 5.0,
    levels: str = "log,warn,error,info",
) -> str:
    """Navega para uma URL e captura todo output do console do browser.

    Captura console.log/warn/error/info e erros de página (uncaught exceptions).
    Essencial para diagnosticar falhas silenciosas no frontend.

    Args:
        url: URL inicial.
        session_file: Sessão para autenticação.
        actions_code: Código Python async a executar após carregar (para disparar ações).
        wait_seconds: Espera após carregar (padrão: 5.0).
        levels: Níveis a capturar, vírgula-separados (padrão: "log,warn,error,info").

    Returns:
        JSON com { errors, warnings, info, all_messages }
    """
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

        await browser.close()

    return json.dumps({
        "url": url,
        "messages_captured": len(messages),
        "errors": [m for m in messages if m["type"] in ("error", "pageerror")],
        "warnings": [m for m in messages if m["type"] == "warn"],
        "info": [m for m in messages if m["type"] in ("log", "info")],
        "all_messages": messages,
    }, ensure_ascii=False, indent=2)


# ── browser_session_save / check / list ───────────────────────────────────────

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
    """Faz login e salva a sessão com um nome amigável para reutilização.

    Cria dois arquivos em BROWSER_MCP_SESSIONS_DIR (padrão: /tmp/browser_mcp_sessions):
    - {name}.json  → storage state do Playwright
    - {name}.meta.json → metadados (usuário, url, timestamps)

    Args:
        name: Nome da sessão (ex: "leandro", "front_qa", "admin").
        login_url: URL da página de login.
        username_value / password_value: Credenciais.
        username_selector / password_selector / submit_selector: CSS selectors.
        wait_after_login: Segundos de espera após submit.

    Returns:
        Confirmação com caminho do arquivo de sessão salvo.
    """
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    session_file = os.path.join(SESSIONS_DIR, f"{name}.json")
    meta_file = os.path.join(SESSIONS_DIR, f"{name}.meta.json")

    login_result = await browser_login(
        login_url=login_url,
        username_value=username_value,
        password_value=password_value,
        username_selector=username_selector,
        password_selector=password_selector,
        submit_selector=submit_selector,
        session_file=session_file,
        wait_after_login=wait_after_login,
    )

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

    return f"Sessão '{name}' salva. {login_result}"


@mcp.tool()
async def browser_session_check(
    name: str,
    check_url: str,
    login_redirect_patterns: str = "login,auth,keycloak,signin,sso",
) -> str:
    """Verifica se uma sessão nomeada ainda é válida (não redirecionou para login).

    Args:
        name: Nome da sessão (gerada por browser_session_save).
        check_url: URL protegida a tentar acessar.
        login_redirect_patterns: Padrões na URL que indicam redirecionamento para login.

    Returns:
        JSON com { name, valid, final_url, reason }
    """
    session_file = os.path.join(SESSIONS_DIR, f"{name}.json")
    meta_file = os.path.join(SESSIONS_DIR, f"{name}.meta.json")

    if not os.path.exists(session_file):
        return json.dumps({
            "name": name, "valid": False,
            "reason": f"Sessão '{name}' não encontrada em {SESSIONS_DIR}",
        })

    patterns = [pt.strip() for pt in login_redirect_patterns.split(",")]

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        await page.goto(check_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(3)
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

    return json.dumps({
        "name": name,
        "valid": valid,
        "final_url": final_url,
        "session_file": session_file,
        "reason": "Redirecionou para login" if redirected else "Sessão ativa",
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def browser_session_list() -> str:
    """Lista todas as sessões salvas com seus metadados e status de validade.

    Returns:
        JSON com { sessions: [...], count }
    """
    if not os.path.exists(SESSIONS_DIR):
        return json.dumps({"sessions": [], "count": 0})

    sessions = []
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

    return json.dumps({"sessions": sessions, "count": len(sessions)}, indent=2)


# ── browser_diff_pages ────────────────────────────────────────────────────────

@mcp.tool()
async def browser_diff_pages(
    url_a: str,
    url_b: Optional[str] = None,
    actions_code: Optional[str] = None,
    session_file: Optional[str] = None,
    wait_seconds: float = 5.0,
) -> str:
    """Compara dois estados de página e retorna diff estruturado de elementos.

    Dois modos:
    - url_b fornecida: compara dois URLs diferentes.
    - actions_code fornecido: compara url_a antes e depois de executar as ações.

    Args:
        url_a: Primeira URL (estado inicial).
        url_b: Segunda URL (opcional — comparação entre páginas distintas).
        actions_code: Código a executar para transitar do estado A para B (opcional).
        session_file: Sessão para autenticação.
        wait_seconds: Espera após carregar cada estado.

    Returns:
        JSON com { added_qa, removed_qa, changed_texts, changed_counts, summary }
    """
    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()

        await page.goto(url_a, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        snap_a = await _snapshot(page)

        if url_b:
            await page.goto(url_b, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(wait_seconds)
        elif actions_code:
            await _exec_actions_code(page, ctx, actions_code)
            await asyncio.sleep(1.5)

        snap_b = await _snapshot(page)
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

    return json.dumps({
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
    }, ensure_ascii=False, indent=2)


# ── browser_accessibility_tree ────────────────────────────────────────────────

@mcp.tool()
async def browser_accessibility_tree(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 5.0,
    interesting_only: bool = True,
    max_depth: int = 6,
) -> str:
    """Extrai a árvore de acessibilidade (ARIA) de uma página.

    Funciona em qualquer site, independente de data-qa. Retorna roles e names
    semânticos que permitem navegação por significado, não por seletor técnico.

    Args:
        url: Página a inspecionar.
        session_file: Sessão para autenticação.
        wait_seconds: Espera após carregar (padrão: 5.0).
        interesting_only: Se True, filtra nós sem nome/role (padrão: True).
        max_depth: Profundidade máxima da árvore na versão flat (padrão: 6).

    Returns:
        JSON com { node_count, flat: [{depth, role, name, ...}], tree }
    """
    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        tree = await page.accessibility.snapshot(interesting_only=interesting_only)
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
    return json.dumps({
        "url": url,
        "node_count": len(flat),
        "flat": flat,
        "tree": tree,
    }, ensure_ascii=False, indent=2)


# ── browser_execute_sequence ──────────────────────────────────────────────────

@mcp.tool()
async def browser_execute_sequence(
    steps_json: str,
    session_file: Optional[str] = None,
    start_url: Optional[str] = None,
    stop_on_error: bool = True,
) -> str:
    """Executa uma sequência tipada de ações no browser definida como JSON.

    Mais seguro que browser_run_playwright para fluxos previsíveis: cada ação tem
    tipo declarado, tratamento de erro embutido e resultado por passo.

    Ações disponíveis e seus campos:
      { "action": "goto",       "url": "https://..." }
      { "action": "click",      "selector": "[data-qa=X]", "force": false, "wait_after": 1.0 }
      { "action": "fill",       "selector": "#input", "value": "texto" }
      { "action": "select",     "selector": "select", "value": "opcao" }
      { "action": "press",      "key": "Enter" }
      { "action": "hover",      "selector": "[data-qa=X]" }
      { "action": "scroll",     "x": 0, "y": 500 }
      { "action": "wait_for",   "selector": "[data-qa=X]", "timeout": 8000 }
      { "action": "wait_for",   "text": "Salvo com sucesso", "timeout": 5000 }
      { "action": "wait_for",   "seconds": 2 }
      { "action": "snapshot" }
      { "action": "screenshot", "full_page": false }
      { "action": "evaluate",   "expression": "document.title" }

    Args:
        steps_json: JSON array de steps (ver ações acima).
        session_file: Sessão para autenticação.
        start_url: URL inicial antes da sequência (opcional).
        stop_on_error: Se True, para na primeira falha (padrão: True).

    Returns:
        JSON com { steps_total, steps_succeeded, final_url, results: [{step, action, success, result, error}] }
    """
    try:
        steps: list[dict] = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return json.dumps({"success": False, "error": f"JSON inválido em steps_json: {e}"})

    results: list[dict] = []

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()

        if start_url:
            await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(3)

        for i, step in enumerate(steps):
            action = step.get("action", "")
            sr: dict = {"step": i + 1, "action": action, "success": False,
                        "result": None, "error": None}

            try:
                if action == "goto":
                    await page.goto(step["url"], wait_until="domcontentloaded", timeout=30_000)
                    sr["result"] = {"url": page.url}

                elif action == "click":
                    el = page.locator(step["selector"])
                    if step.get("index"):
                        el = el.nth(step["index"])
                    await el.click(force=step.get("force", False),
                                   timeout=step.get("timeout", 10_000))
                    await asyncio.sleep(step.get("wait_after", 1.0))
                    sr["result"] = {"url": page.url}

                elif action == "fill":
                    el = page.locator(step["selector"])
                    await el.wait_for(state="visible", timeout=step.get("timeout", 10_000))
                    await el.clear()
                    await el.fill(step["value"])

                elif action == "select":
                    await page.locator(step["selector"]).select_option(step["value"])

                elif action == "press":
                    await page.keyboard.press(step["key"])

                elif action == "hover":
                    await page.locator(step["selector"]).hover()

                elif action == "scroll":
                    x, y = step.get("x", 0), step.get("y", 500)
                    await page.evaluate(f"window.scrollBy({x}, {y})")

                elif action == "wait_for":
                    timeout = step.get("timeout", 10_000)
                    if sel := step.get("selector"):
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
                        sr["result"] = f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
                    os.unlink(tmp)

                elif action == "evaluate":
                    sr["result"] = await page.evaluate(step["expression"])

                else:
                    raise ValueError(f"Ação desconhecida: '{action}'")

                sr["success"] = True

            except Exception as e:
                sr["error"] = str(e)
                results.append(sr)
                if stop_on_error:
                    break
                continue

            results.append(sr)

        final_url = page.url
        await browser.close()

    return json.dumps({
        "steps_total": len(steps),
        "steps_executed": len(results),
        "steps_succeeded": sum(1 for r in results if r["success"]),
        "final_url": final_url,
        "results": results,
    }, ensure_ascii=False, indent=2)


# ── browser_get_storage ───────────────────────────────────────────────────────

@mcp.tool()
async def browser_get_storage(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 3.0,
) -> str:
    """Lê localStorage, sessionStorage e cookies de uma URL.

    Útil para inspecionar tokens de auth, estado persistido da SPA e preferências.

    Args:
        url: URL a inspecionar.
        session_file: Sessão para autenticação.
        wait_seconds: Espera após carregar (padrão: 3.0).

    Returns:
        JSON com { local_storage, session_storage, cookies, *_keys }
    """
    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
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
        await browser.close()

    ls = storage.get("localStorage", {})
    ss = storage.get("sessionStorage", {})
    return json.dumps({
        "url": url,
        "local_storage": ls,
        "session_storage": ss,
        "cookies": cookies,
        "local_storage_keys": list(ls.keys()),
        "session_storage_keys": list(ss.keys()),
        "cookies_count": len(cookies),
    }, ensure_ascii=False, indent=2)


# ── browser_run_playwright ────────────────────────────────────────────────────

@mcp.tool()
async def browser_run_playwright(
    code: str,
    session_file: Optional[str] = None,
    start_url: Optional[str] = None,
    timeout_seconds: int = 60,
) -> str:
    """Executa código Python Playwright diretamente, sem LLM intermediário.

    O código recebe `page`, `context` e `asyncio` já configurados.
    Escreva o corpo de uma função async — use `return` para retornar dados.
    Recomendado: use seletores obtidos via browser_map_site ou browser_explore_page.

    Exemplo:
        title = await page.title()
        count = await page.locator('[data-qa="Item"]').count()
        return {"title": title, "count": count}

    Args:
        code: Corpo async com acesso a `page`, `context`, `asyncio`.
        session_file: Sessão para autenticação.
        start_url: URL inicial antes de executar o código.
        timeout_seconds: Timeout total (padrão: 60s).

    Returns:
        JSON com { success, result, final_url, error }
    """
    cleaned = textwrap.dedent(code.rstrip())
    indented = "\n".join(f"    {line}" for line in cleaned.splitlines())
    fn_src = f"async def _scenario(page, context, asyncio):\n{indented}\n"

    ns: dict = {"asyncio": asyncio, "json": json}
    try:
        exec(fn_src, ns)  # noqa: S102
    except SyntaxError as e:
        return json.dumps({"success": False, "result": None, "error": f"SyntaxError: {e}"})

    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()

        try:
            if start_url:
                await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(3)

            result = await asyncio.wait_for(
                ns["_scenario"](page, ctx, asyncio),
                timeout=timeout_seconds,
            )
            output = {"success": True, "result": result, "final_url": page.url, "error": None}

        except asyncio.TimeoutError:
            output = {"success": False, "result": None,
                      "error": f"Timeout após {timeout_seconds}s"}
        except Exception as e:
            output = {"success": False, "result": None, "final_url": page.url, "error": str(e)}
        finally:
            await browser.close()

    return json.dumps(output, ensure_ascii=False, indent=2)


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
    """Executa uma tarefa de automação em linguagem natural via LLM (browser-use).

    Prefira browser_run_playwright ou browser_execute_sequence para tarefas
    com seletores conhecidos. Use esta tool para exploração inicial ou quando
    os seletores ainda não foram mapeados.

    Args:
        task: Descrição da tarefa. Use {placeholder} para dados sensíveis.
        start_url: URL inicial (opcional).
        model: LLM a usar (padrão: gpt-4o-mini). Use "claude-sonnet-4-6" para Claude.
        max_steps: Número máximo de ações (padrão: 30).
        sensitive_data: {"email": "...", "password": "..."} — não exposto ao LLM.
        session_file: Arquivo de sessão do browser_login.

    Returns:
        Resultado final da tarefa como texto.
    """
    use_model = model or DEFAULT_MODEL
    llm = _get_llm(use_model)
    full_task = f"Navegue para {start_url}. Em seguida: {task}" if start_url else task
    profile = _make_profile(HEADLESS, storage_state=session_file)

    agent = Agent(
        task=full_task,
        llm=llm,
        browser_profile=profile,
        max_actions_per_step=5,
        sensitive_data=sensitive_data or {},
    )
    result = await agent.run(max_steps=max_steps)
    final = result.final_result()
    if final:
        return str(final)
    history = result.action_results()
    if history:
        last = history[-1]
        return str(last.extracted_content or last.error or "Tarefa concluída sem resultado.")
    return "Tarefa concluída sem resultado."


# ── browser_screenshot ────────────────────────────────────────────────────────

@mcp.tool()
async def browser_screenshot(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 3.0,
    full_page: bool = False,
) -> str:
    """Tira um screenshot de uma URL e retorna como base64 PNG.

    Args:
        url: URL a capturar.
        session_file: Sessão para autenticação.
        wait_seconds: Espera após carregar (padrão: 3.0).
        full_page: Se True, captura a página inteira (padrão: False = viewport).

    Returns:
        String base64 no formato data:image/png;base64,...
    """
    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        await page.screenshot(path=tmp, full_page=full_page)
        await browser.close()

    with open(tmp, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    os.unlink(tmp)
    return f"data:image/png;base64,{data}"


# ── browser_get_page_content ──────────────────────────────────────────────────

@mcp.tool()
async def browser_get_page_content(
    url: str,
    session_file: Optional[str] = None,
    wait_seconds: float = 3.0,
) -> str:
    """Carrega uma URL e retorna o texto visível da página (sem HTML), máx 20.000 chars.

    Args:
        url: URL a carregar.
        session_file: Sessão para autenticação.
        wait_seconds: Espera após carregar (padrão: 3.0).

    Returns:
        Texto visível da página, truncado em 20.000 caracteres.
    """
    async with async_playwright() as p:
        browser, ctx = await _make_context(p, session_file)
        page = await ctx.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(wait_seconds)
        text = await page.evaluate("""
            () => {
                document.querySelectorAll('script,style,noscript').forEach(el => el.remove());
                return (document.body || document.documentElement).innerText;
            }
        """)
        await browser.close()

    cleaned = "\n".join(line for line in text.splitlines() if line.strip())
    return cleaned[:20_000]


# ── browser_get_help ──────────────────────────────────────────────────────────

@mcp.tool()
def browser_get_help() -> str:
    """Retorna documentação completa das ferramentas do Browser Use MCP."""
    return """
# Browser Use MCP — Documentação

## Arquitetura Map First → Execute → Verify

### CONHECIMENTO (antes de automatizar)
  browser_map_site(url, session_file, max_pages)
    → crawla o site, retorna selector_index com todos os [data-qa] por página

  browser_explore_page(url, session_file, trigger_interactions=True)
    → inspeção profunda: clica em triggers para revelar dropdowns/modais/tabs

  browser_intercept_network(url, session_file, actions_code)
    → captura XHR/fetch durante navegação: endpoints, payloads, respostas

  browser_accessibility_tree(url, session_file)
    → árvore ARIA semântica — funciona em qualquer site sem data-qa

### EXECUÇÃO (com seletores conhecidos)
  browser_run_playwright(code, session_file, start_url)
    → executa Python Playwright diretamente (sem LLM no loop)
    → `return {...}` captura resultado estruturado

  browser_execute_sequence(steps_json, session_file, start_url)
    → sequência tipada: goto/click/fill/select/press/hover/scroll/
                        wait_for/snapshot/screenshot/evaluate

  browser_run_task(task, start_url, model, session_file)
    → fallback LLM (browser-use) para exploração inicial

### VERIFICAÇÃO (confirmar que funcionou)
  browser_diff_pages(url_a, url_b=None, actions_code=None, session_file)
    → diff: added_qa, removed_qa, changed_texts, changed_counts

  browser_capture_console(url, session_file, actions_code)
    → captura errors, warnings, logs do console do browser

  browser_get_storage(url, session_file)
    → localStorage, sessionStorage, cookies

### AUTENTICAÇÃO
  browser_login(login_url, username, password, session_file)
    → login pontual, salva em arquivo

  browser_session_save(name, login_url, username, password)
    → salva sessão nomeada em /tmp/browser_mcp_sessions/

  browser_session_check(name, check_url)
    → verifica se sessão ainda é válida

  browser_session_list()
    → lista todas as sessões salvas e seus status

### UTILITÁRIOS
  browser_screenshot(url, session_file, full_page)
  browser_get_page_content(url, session_file)

## Fluxo típico para novo site

1. browser_session_save("minha_conta", "https://site.com/login", "user", "pass")
2. browser_map_site("https://site.com", session_file="/tmp/browser_mcp_sessions/minha_conta.json")
   → selector_index revela todos os [data-qa]
3. browser_explore_page("https://site.com/dashboard", ...)
   → descobre elementos em dropdowns/modais
4. browser_intercept_network("https://site.com/dashboard", ...)
   → mapeia API endpoints usados pela página
5. browser_execute_sequence('[{"action":"click","selector":"[data-qa=AddButton]"}]', ...)
   → executa a ação
6. browser_diff_pages("https://site.com/dashboard", actions_code="...")
   → confirma que o elemento foi adicionado

## Configuração via variáveis de ambiente
  BROWSER_USE_MODEL          modelo LLM padrão (default: gpt-4o-mini)
  BROWSER_HEADLESS           true/false (default: true)
  BROWSER_MCP_SESSIONS_DIR   diretório de sessões (default: /tmp/browser_mcp_sessions)
  OPENAI_API_KEY / ANTHROPIC_API_KEY
  MCP_HOST, MCP_PORT, MCP_TRANSPORT
"""


if __name__ == "__main__":
    mcp.run(transport=MCP_TRANSPORT)
