"""Writes the Hindi documentary script: a plan first, then each chapter separately
(chapter-by-chapter writing is what reliably gets a 10-12 minute video)."""
import json

from .llm import ask_json

SYSTEM = """You are the head writer of a respected Hindi YouTube documentary channel about ANCIENT INDIAN HISTORY
(Indus Valley/Harappan civilisation up to roughly 1200 CE).

Writing rules:
- Spoken, warm, clear Hindi in Devanagari script, like a calm male documentary narrator. Short sentences.
- Be historically accurate. Base claims on mainstream scholarship: archaeology (ASI excavations), inscriptions,
  coins, travellers' accounts (e.g. Megasthenes, Faxian, Xuanzang), and ancient texts named explicitly.
- When something is legend, tradition or disputed, SAY SO ("मान्यता है कि...", "कथाओं के अनुसार...",
  "इतिहासकारों में इस पर मतभेद है..."). Never present myth as proven fact. Never invent dates, numbers or quotes;
  if a date is approximate say "लगभग".
- Neutral and respectful to every religion, caste, region and community. No modern politics.
- Pure narration only: no stage directions, no markdown, no emojis, no brackets, no English sentences.
- Write years like "ईसा पूर्व 268" / "ईस्वी 320".
- All original writing. Never copy text from books, websites or other videos.
- image_query values are ENGLISH search phrases for Wikimedia Commons that will find REAL photos of monuments,
  archaeological sites, sculptures, coins, inscriptions, manuscripts, museum objects, or old paintings
  (e.g. "Sanchi Stupa gateway", "Mohenjo-daro Great Bath", "Gupta gold coin", "Ajanta cave painting").
  Never ask for film stills, actors, cartoons, modern people or maps of modern politics."""


def make_plan(topic, cfg, used_titles):
    n = cfg["chapters"]
    prompt = f"""Plan a {cfg['target_minutes_min']:.0f}-{cfg['target_minutes_max']:.0f} minute Hindi documentary on:
TOPIC: {topic}

Recently used titles (do not repeat): {json.dumps(used_titles[-30:], ensure_ascii=False)}

Return JSON:
{{
  "title": "catchy but honest Hindi YouTube title, max 85 characters, no clickbait lies",
  "description": "Hindi description, 120-200 words, what the viewer will learn",
  "tags": ["15 tags mixing Hindi and English search terms"],
  "hashtags": ["#प्राचीनभारत", "2 more"],
  "thumbnail_text": "2-4 punchy Hindi words for the thumbnail",
  "chapters": [
    {{"heading": "short Hindi chapter heading (max 5 words)",
      "covers": "2-3 sentences in English: exactly what this chapter must explain, with key facts",
      "image_queries": ["4-6 specific English Wikimedia Commons queries"]}}
  ],
  "short": {{
    "title": "Hindi title for a YouTube Short, max 70 characters",
    "narration": "Hindi narration for a 45-55 second vertical Short, 95-115 words. First sentence must be a strong hook (a surprising true fact). End with: पूरी कहानी हमारे चैनल पर देखिए।",
    "image_queries": ["5 specific English Wikimedia Commons queries"]
  }}
}}
Exactly {n} chapters. Chapter 1 opens with a hook; the last chapter is legacy/why it matters today."""
    return ask_json(SYSTEM, prompt, max_tokens=6000)


def write_chapter(plan, idx, cfg, extra=False):
    chapters = plan["chapters"]
    ch = chapters[idx] if not extra else {
        "heading": "कुछ रोचक तथ्य", "covers": "5 lesser-known but well-documented facts about the topic",
        "image_queries": [q for c in chapters for q in c.get("image_queries", [])][:6]}
    is_first, is_last = idx == 0 and not extra, idx == len(chapters) - 1 and not extra
    outline = "\n".join(f"{i + 1}. {c['heading']} — {c['covers']}" for i, c in enumerate(chapters))
    special = ""
    if is_first:
        special = (f"Start with a 2-3 sentence gripping hook, then greet: 'नमस्कार दोस्तों, "
                   f"{cfg['channel_name']} में आपका स्वागत है।' and say what this video will reveal.")
    if is_last:
        special = ("End with a reflective conclusion, then ask viewers to comment their thoughts, like the video "
                   "and subscribe for more stories of ancient India.")
    prompt = f"""Documentary title: {plan['title']}
Full outline:
{outline}

Now write ONLY chapter: "{ch['heading']}" — {ch['covers']}
Length: about {cfg['words_per_chapter']} Hindi words in total. {special}
Do not repeat facts that clearly belong to other chapters. Flow naturally from the previous chapter.
Split into 5-8 scenes; each scene is 1-3 sentences (20-40 words) that match ONE picture.

Return JSON:
{{"scenes": [{{"text": "Hindi narration", "image_query": "specific English Wikimedia Commons query"}}]}}
Suggested image queries for this chapter: {json.dumps(ch.get('image_queries', []))}"""
    data = ask_json(SYSTEM, prompt, max_tokens=4000)
    scenes = [s for s in data.get("scenes", []) if s.get("text", "").strip()]
    if not scenes:
        raise RuntimeError(f"chapter {idx} came back empty")
    return {"heading": ch["heading"], "scenes": scenes}


def pick_new_topic(used_topics):
    data = ask_json(SYSTEM, f"""Suggest ONE fresh, specific, well-documented ancient Indian history topic
for a 10-12 minute documentary that is NOT in this list: {json.dumps(used_topics[-80:], ensure_ascii=False)}
Return JSON: {{"topic": "topic in English"}}""", max_tokens=300)
    return data["topic"]
