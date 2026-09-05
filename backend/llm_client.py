import json
import re
import threading

import requests

from extract_case_items import (
    DEFAULT_MODEL,
    OPENROUTER_BASE_URL,
    _api_key,
)


###---###
# Cost tracking
###---###

# Thread-safe accumulator for OpenRouter usage cost across one audit run.
class CostTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._total = 0.0
        self._calls = 0

    def add(self, cost: float | None) -> None:
        if not cost:
            return
        with self._lock:
            self._total += cost
            self._calls += 1

    @property
    def total(self) -> float:
        with self._lock:
            return self._total

    @property
    def calls(self) -> int:
        with self._lock:
            return self._calls


def extract_json(raw: str) -> dict | list:
    text = raw.strip()

    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\n?|\n?```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)
        if not match:
            raise ValueError(
                "Model returned no valid JSON: " + text[:500]
            )
        return json.loads(match.group(0))


def chat_json(
    system_prompt: str,
    user_prompt: str,
    model_name: str = DEFAULT_MODEL,
    temperature: float = 0.0,
    timeout: int = 180,
    cost_tracker: CostTracker | None = None,
    enable_thinking: bool = False,
) -> dict | list:
    body = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    if enable_thinking:
        body["reasoning"] = {"enabled": True}
    else:
        body["reasoning"] = {"enabled": False}

    response = requests.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout,
    )

    if not response.ok:
        raise RuntimeError(
            f"OpenRouter {response.status_code}: {response.text[:500]}"
        )

    payload = response.json()

    if cost_tracker is not None:
        usage = payload.get("usage") or {}
        cost_tracker.add(usage.get("cost"))

    content = payload["choices"][0]["message"]["content"]
    return extract_json(content)