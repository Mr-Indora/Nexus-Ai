"""Small stateless REST client for Google's Gemini Interactions API."""

import json
import time
from typing import Any

import httpx

from config import settings


def generate_text(
    prompt: str,
    *,
    json_schema: dict[str, Any] | None = None,
    json_mode: bool = False,
    max_output_tokens: int | None = None,
    thinking_level: str = "low",
    timeout: float = 40,
) -> str:
    """Generate text without storing prompts or responses on Google's side."""
    if not settings.GEMINI_API_KEY:
        raise ValueError("Gemini is not configured")

    payload: dict[str, Any] = {
        "model": settings.GEMINI_MODEL,
        "input": prompt,
        "store": False,
        "generation_config": {"thinking_level": thinking_level},
    }
    if json_schema is not None or json_mode:
        response_format: dict[str, Any] = {"type": "text", "mime_type": "application/json"}
        if json_schema is not None:
            response_format["schema"] = json_schema
        payload["response_format"] = response_format
    if max_output_tokens is not None:
        payload["generation_config"]["max_output_tokens"] = max_output_tokens

    headers = {
        "x-goog-api-key": settings.GEMINI_API_KEY,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    for attempt in range(2):
        response_text: list[str] = []
        try:
            with httpx.stream(
                "POST",
                "https://generativelanguage.googleapis.com/v1beta/interactions?alt=sse",
                headers=headers,
                json={**payload, "stream": True},
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    event_type = event.get("event_type", event.get("type"))
                    if event_type == "error":
                        error = event.get("error")
                        safe_code = error.get("code") if isinstance(error, dict) else "provider_error"
                        raise httpx.ReadError(f"Gemini stream reported {safe_code}")
                    if event_type != "step.delta":
                        continue
                    delta = event.get("delta")
                    if isinstance(delta, dict) and delta.get("type") == "text" and delta.get("text"):
                        response_text.append(str(delta["text"]))
        except httpx.HTTPStatusError as exc:
            # Retry once for Google's transient service failures only.
            if attempt == 1 or exc.response.status_code not in {502, 503, 504}:
                raise
            retry_after = exc.response.headers.get("Retry-After", "")
            try:
                delay = min(max(float(retry_after), 0.25), 1.5) if retry_after else 0.6
            except ValueError:
                delay = 0.6
            time.sleep(delay)
            continue
        except httpx.TimeoutException:
            if attempt == 1:
                raise
            time.sleep(0.6)
            continue
        if response_text:
            return "".join(response_text).strip()
        raise ValueError("Gemini returned no text output")
    raise ValueError("Gemini returned no text output")
