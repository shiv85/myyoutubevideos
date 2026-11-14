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


def _gemini_call(model, system, prompt, max_tokens, low_thinking=True):
    gen = {"maxOutputTokens": max(max_tokens * 3, 16000), "temperature": 0.8,
           "responseMimeType": "application/json"}
    if low_thinking:  # less "thinking" = faster answers and fewer timeouts
        gen["thinkingConfig"] = {"thinkingLevel": "low"}
    return requests.post(
        f"{GEMINI_BASE}/models/{model}:generateContent",
        params={"key": env("GEMINI_API_KEY")},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen,
        },
        timeout=240,
    )


_gemini_models = None      # usable models, best first
_no_low_thinking = set()   # models that reject the thinkingLevel setting
_gemini_round = 0


def _gemini(system, prompt, max_tokens):
    """Tries each usable model in turn. Busy (429/500/503) or slow models are skipped
    for this attempt instead of waiting on them."""
    global _gemini_models, _gemini_round
    if _gemini_models is None:
        _gemini_models = _gemini_candidates()
    # start each new attempt from a different model, so one overloaded model can't block everything
    order = _gemini_models[_gemini_round % len(_gemini_models):] + _gemini_models[:_gemini_round % len(_gemini_models)]
    _gemini_round += 1
    problems = []
    for model in list(order):
        try:
            r = _gemini_call(model, system, prompt, max_tokens, model not in _no_low_thinking)
            if r.status_code == 400 and "thinking" in r.text.lower():
                _no_low_thinking.add(model)
                r = _gemini_call(model, system, prompt, max_tokens, False)
        except requests.exceptions.RequestException as e:
            log(f"Gemini '{model}' too slow / connection problem, trying next model")
            problems.append(f"{model}: {type(e).__name__}")
            continue
        if r.status_code in (400, 403) and "API key" in r.text:
            raise RuntimeError("Gemini rejected the API key - check the GEMINI_API_KEY secret: " + r.text[:300])
        if r.status_code in (400, 404):
            log(f"Gemini model '{model}' not available ({r.status_code}), removing it")
            if model in _gemini_models and len(_gemini_models) > 1:
                _gemini_models.remove(model)
            problems.append(f"{model}: {r.status_code}")
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            log(f"Gemini '{model}' busy ({r.status_code}), trying next model")
            problems.append(f"{model}: {r.status_code}")
            continue
        r.raise_for_status()
        cand = (r.json().get("candidates") or [{}])[0]
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            log(f"Gemini '{model}' returned no text (finishReason={cand.get('finishReason')}), trying next")
            problems.append(f"{model}: empty")
            continue
        log(f"Gemini answered with model: {model}")
        return text
    raise RuntimeError("all Gemini models failed this round: " + "; ".join(problems)[:500])


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


def ask_json(system, prompt, max_tokens=8000, tries=6):
    last = None
    for i in range(tries):
        try:
            return _extract_json(ask(system, prompt + "\n\nReply with ONE valid JSON object only. No markdown.",
                                     max_tokens))
        except Exception as e:  # network blips, rate limits, broken JSON
            last = e
            log(f"LLM attempt {i + 1} failed: {e}")
            time.sleep(min(120, 20 * (i + 1)))  # 20s, 40s, 60s ... gives busy servers time to recover
    raise RuntimeError(f"LLM failed after {tries} tries: {last}")
