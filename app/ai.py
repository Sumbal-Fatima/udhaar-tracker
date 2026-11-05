"""Thin client for Vultr Serverless Inference (OpenAI-compatible chat completions).

Every call is logged to the ai_runs table so the dashboard can show that the AI calls are real.
"""
import json
import re
import time
from datetime import datetime, timezone

import httpx

from . import config, db


class AIError(Exception):
    pass


def _post(payload: dict) -> dict:
    """The only function that touches the network. Tests replace it."""
    key = config.api_key()
    if not key:
        raise AIError("VULTR_INFERENCE_API_KEY is not set. Add it to the environment and restart.")
    try:
        r = httpx.post(
            f"{config.base_url()}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
    except httpx.HTTPError as e:
        raise AIError(f"Could not reach Vultr Serverless Inference: {e}") from e
    if r.status_code != 200:
        raise AIError(f"Vultr Serverless Inference returned {r.status_code}: {r.text[:300]}")
    return r.json()


def list_models() -> list:
    key = config.api_key()
    if not key:
        raise AIError("VULTR_INFERENCE_API_KEY is not set.")
    try:
        r = httpx.get(f"{config.base_url()}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    except httpx.HTTPError as e:
        raise AIError(f"Could not reach Vultr Serverless Inference: {e}") from e
    if r.status_code != 200:
        raise AIError(f"Model list failed with {r.status_code}: {r.text[:300]}")
    data = r.json()
    items = data.get("data", data) if isinstance(data, dict) else data
    return [m.get("id", m) if isinstance(m, dict) else m for m in items]


def _log(kind: str, ok: bool, latency_ms: int, usage: dict | None, error: str | None) -> None:
    conn = db.connect()
    conn.execute(
        "INSERT INTO ai_runs(kind, model, ok, latency_ms, prompt_tokens, completion_tokens, error, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            kind,
            config.model(),
            1 if ok else 0,
            latency_ms,
            (usage or {}).get("prompt_tokens"),
            (usage or {}).get("completion_tokens"),
            error,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return fenced.group(1).strip() if fenced else text


def _parse_json(text: str):
    text = _clean(text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])


def chat_json(kind: str, system: str, user_payload: dict, max_tokens: int = 1800) -> dict:
    """Call the model and return a parsed JSON object. One retry if the output is not valid JSON."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    last_error = "unknown error"
    for attempt in range(2):
        started = time.time()
        try:
            resp = _post(
                {"model": config.model(), "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
            )
            content = resp["choices"][0]["message"]["content"]
            parsed = _parse_json(content)
            _log(kind, True, int((time.time() - started) * 1000), resp.get("usage"), None)
            return parsed
        except AIError as e:
            _log(kind, False, int((time.time() - started) * 1000), None, str(e))
            raise
        except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
            last_error = f"model output was not valid JSON: {e}"
            _log(kind, False, int((time.time() - started) * 1000), None, last_error)
            messages.append({"role": "user", "content": "Your last reply was not valid JSON. Reply with only the JSON object."})
    raise AIError(last_error)
