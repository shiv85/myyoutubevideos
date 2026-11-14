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


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_gemini_model = None  # remembered after the first model that works


def _gemini_candidates():
    """Preferred model first, then Google's auto-updating alias, then whatever Flash models
    this key can actually use (so a retired model name never breaks the bot again)."""
    names = [env("GEMINI_MODEL"), "gemini-flash-latest"]
    try:
        r = requests.get(f"{GEMINI_BASE}/models", params={"key": env("GEMINI_API_KEY"), "pageSize": 200},
                         timeout=60)
        found = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        bad = ("tts", "image", "live", "lite", "audio", "embedding", "transcribe", "robotics", "computer")
        flash = [n for n in found if "flash" in n and not any(b in n for b in bad)]
        flash.sort(key=lambda n: ("preview" in n or "exp" in n,
                                  [-int(x) for x in re.findall(r"\d+", n)]))
        names += flash + [n for n in found if "pro" in n and not any(b in n for b in bad)]
    except Exception as e:
        log(f"could not list Gemini models: {e}")
    seen = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    return seen


def _gemini_call(model, system, prompt, max_tokens):
    r = requests.post(
        f"{GEMINI_BASE}/models/{model}:generateContent",
        params={"key": env("GEMINI_API_KEY")},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # newer models "think" before answering and that uses output tokens, so allow plenty
            "generationConfig": {"maxOutputTokens": max(max_tokens * 3, 16000), "temperature": 0.8,
                                 "responseMimeType": "application/json"},
        },
        timeout=600,
    )
    return r


def _gemini(system, prompt, max_tokens):
    global _gemini_model
    models = [_gemini_model] if _gemini_model else _gemini_candidates()
    last = None
    for model in models:
        r = _gemini_call(model, system, prompt, max_tokens)
        if r.status_code in (400, 404) and not _gemini_model and "API key" not in r.text:
            log(f"Gemini model '{model}' not available ({r.status_code}), trying next")
            last = r
            continue
        if r.status_code in (400, 403) and "API key" in r.text:
            raise RuntimeError("Gemini rejected the API key - check the GEMINI_API_KEY secret: " + r.text[:300])
        r.raise_for_status()
        if _gemini_model != model:
            log(f"using Gemini model: {model}")
        _gemini_model = model
        cand = r.json()["candidates"][0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise RuntimeError(f"Gemini returned no text (finishReason={cand.get('finishReason')})")
        return text
    raise RuntimeError(f"No usable Gemini model found. Last reply: {last.status_code if last else ''} "
                       f"{last.text[:300] if last else ''}")


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
