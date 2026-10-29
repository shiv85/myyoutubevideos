"""Talks to an LLM for script writing. Supports Claude (Anthropic) or Gemini.
Set ANTHROPIC_API_KEY  (optionally CLAUDE_MODEL)  or  GEMINI_API_KEY (optionally GEMINI_MODEL)."""
import json
import re
import time

import requests

from .common import env, log


def _claude(system, prompt, max_tokens):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": env("ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": env("CLAUDE_MODEL", "claude-sonnet-5-5"),
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=300,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])


def _gemini(system, prompt, max_tokens):
    model = env("GEMINI_MODEL", "gemini-2.5-flash")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": env("GEMINI_API_KEY")},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.8,
                                 "responseMimeType": "application/json"},
        },
        timeout=300,
    )
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def ask(system, prompt, max_tokens=8000):
    if env("ANTHROPIC_API_KEY"):
        return _claude(system, prompt, max_tokens)
    if env("GEMINI_API_KEY"):
        return _gemini(system, prompt, max_tokens)
    raise RuntimeError("No LLM key found. Add ANTHROPIC_API_KEY or GEMINI_API_KEY as a GitHub secret.")


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


def ask_json(system, prompt, max_tokens=8000, tries=4):
    last = None
    for i in range(tries):
        try:
            return _extract_json(ask(system, prompt + "\n\nReply with ONE valid JSON object only. No markdown.",
                                     max_tokens))
        except Exception as e:  # network blips, rate limits, broken JSON
            last = e
            log(f"LLM attempt {i + 1} failed: {e}")
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"LLM failed after {tries} tries: {last}")
