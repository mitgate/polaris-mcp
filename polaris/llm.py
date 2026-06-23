"""LLM helpers — generate_steps() asks the LLM to plan a step sequence."""

from __future__ import annotations

import json
import logging
from typing import Any

from polaris.config import ANTHROPIC_API_KEY, OPENAI_API_KEY

logger = logging.getLogger("polaris-mcp")


async def generate_steps(goal: str, map_context: dict, model: str) -> list[dict]:
    """Ask the LLM to generate an array of steps given a page map and goal.

    Returns a list of step dicts compatible with browser_execute_sequence.
    Returns [] (with a logged warning) if the LLM call or JSON parse fails.
    """
    prompt = (
        "You are a browser automation expert. Given a site map and a goal, "
        "generate a minimal and precise action sequence.\n\n"
        f"SITE MAP:\n{json.dumps(map_context, indent=2, ensure_ascii=False)}\n\n"
        f"GOAL: {goal}\n\n"
        "Available step types:\n"
        '  {"action": "goto", "url": "..."}\n'
        '  {"action": "click", "selector": "[data-qa=NAME]", "wait_after": 1.0}\n'
        '  {"action": "fill", "selector": "[data-qa=NAME]", "value": "..."}\n'
        '  {"action": "select", "selector": "...", "value": "..."}\n'
        '  {"action": "press", "key": "Enter"}\n'
        '  {"action": "wait_for", "selector": "[data-qa=NAME]"}\n'
        '  {"action": "wait_for", "seconds": 2}\n'
        '  {"action": "snapshot"}\n'
        '  {"action": "screenshot"}\n'
        '  {"action": "evaluate", "expression": "..."}\n\n'
        "Rules:\n"
        "- Use ONLY selectors from the site map above (prefer [data-qa=NAME])\n"
        "- Add wait_for or wait_after when actions trigger navigation or async loading\n"
        "- End with a snapshot step to capture the final state\n"
        '- Return ONLY a valid JSON object: {"steps": [...]}'
    )

    raw: Any = None
    try:
        if model.startswith("claude"):
            if not ANTHROPIC_API_KEY:
                raise RuntimeError("ANTHROPIC_API_KEY is not set")
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
        else:
            if not OPENAI_API_KEY:
                raise RuntimeError("OPENAI_API_KEY is not set")
            import openai

            client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content

        parsed = json.loads(raw)
        steps = parsed.get("steps", parsed)
        if isinstance(steps, list):
            return steps
        logger.warning("generate_steps: unexpected shape — %s", type(steps))
        return []

    except Exception as exc:
        logger.warning("generate_steps failed: %s", exc)
        return []
