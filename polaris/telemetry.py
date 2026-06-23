"""Telemetry helpers — timing, _polaris block construction, and JSON wrapping."""

from __future__ import annotations

import json
import time as _time
from typing import Optional


def _start() -> float:
    return _time.monotonic()


def _elapsed_ms(t0: float) -> int:
    return round((_time.monotonic() - t0) * 1000)


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
