"""One run = one long documentary (10-12 min) + one Short, uploaded to YouTube.

python -m bot.main                 normal run (needs secrets)
python -m bot.main --no-upload     make the videos only
python -m bot.main --topic "..."   force a topic
python -m bot.main --offline       test run with the sample script, no internet needed (silent audio)"""
import argparse
import datetime as dt
import json
import re
import shutil
from pathlib import Path

from . import script_gen, upload
from .common import ROOT, duration, env, load_config, log, run
from .images import ImageFinder, art_card, fit_frame
from .music import make_loop
from .render import LONG, SHORT, render_video
from .thumbnail import make_thumbnail, make_title_card
from .tts import speak

STATE = ROOT / "state" / "history.json"


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"topics": [], "titles": [], "videos": []}


def save_state(st):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def next_topic(st):
    for line in (ROOT / "topics.txt").read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if t and not t.startswith("#") and t not in st["topics"]:
            return t
    return script_gen.pick_new_topic(st["topics"])


def fmt_ts(sec):
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def tempo(path, factor):
    tmp = path.with_suffix(".t.mp3")
    run(["ffmpeg", "-y", "-i", str(path), "-filter:a", f"atempo={factor:.4f}", str(tmp)])
    tmp.replace(path)
    return duration(path)


def silent(path, secs):
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(secs), str(path)])
    return path


def frame_for(scene_idx, queries, finder, size, workdir, fallback_text, offline, tag):
    out = workdir / f"frame_{tag}_{scene_idx:03d}.jpg"
    src = finder.find(queries, offline=offline)
    if src is None:
        src = finder.find(queries, offline=offline, allow_reuse=True)
    if src is None:
        art_card(fallback_text, out, size, seed=scene_idx)
        return out, None
    fit_frame(src, out, size)
    return out, src


def build_long(plan, cfg, workdir, finder, offline):
    min_s, max_s = cfg["target_minutes_min"] * 60, cfg["target_minutes_max"] * 60
    audio_dir = workdir / "audio"
    audio_dir.mkdir(exist_ok=True)

    scenes, k = [], 0

    def voice_chapter(ch):
        nonlocal k
        for j, sc in enumerate(ch["scenes"]):
            a = audio_dir / f"s{k:03d}.mp3"
            secs = speak(sc["text"], a, cfg, offline)
            scenes.append({"text": sc["text"], "audio": a, "secs": secs,
                           "query": sc.get("image_query", ""), "chapter": ch,
                           "heading": ch["heading"] if j == 0 else None})
            k += 1

    for ch in plan["chapters_written"]:
        voice_chapter(ch)
    total = sum(s["secs"] + 0.45 for s in scenes)
    log(f"narration length: {total / 60:.1f} min")

    # too short -> add a "interesting facts" chapter before the conclusion (max twice)
    extra_rounds = 0
    while total < min_s and not offline and extra_rounds < 2:
        extra = script_gen.write_chapter(plan, 0, cfg, extra=True)
        last = plan["chapters_written"].pop()
        plan["chapters_written"] += [extra, last]
        scenes.clear()
        k = 0
        for ch in plan["chapters_written"]:
            voice_chapter(ch)
        total = sum(s["secs"] + 0.45 for s in scenes)
        extra_rounds += 1
        log(f"extended narration: {total / 60:.1f} min")

    # too long -> speak slightly faster (max 8%)
    if total > max_s + 15:
        f = min(1.08, total / max_s)
        log(f"speeding narration by {f:.3f}x")
        for s in scenes:
            s["secs"] = tempo(s["audio"], f)
        total = sum(s["secs"] + 0.45 for s in scenes)

    # pictures
    first_src = None
    for i, s in enumerate(scenes):
        ch = s["chapter"]
        queries = [s["query"]] + ch.get("image_queries", []) + plan.get("fallback_queries", [])
        s["frame"], src = frame_for(i, queries, finder, LONG, workdir, ch["heading"], offline, "h")
        first_src = first_src or src

    # opening title card (3.5 s, music only)
    bg = first_src or art_card("", workdir / "plain_bg.jpg", LONG, seed=1)
    title_frame = make_title_card(bg, plan["title"], workdir / "title_card.jpg", LONG)
    title_scene = {"frame": title_frame, "audio": silent(audio_dir / "title.mp3", 3.5), "secs": 3.05, "text": ""}
    all_scenes = [title_scene] + scenes

    out = workdir / "long.mp4"
    starts, total = render_video(all_scenes, out, workdir, workdir / "music.wav", cfg)

    chapters_ts = []
    for st, sc in zip(starts, all_scenes):
        if sc.get("heading"):
            chapters_ts.append(("00:00" if not chapters_ts else fmt_ts(st), sc["heading"]))
    thumb = make_thumbnail(bg, plan.get("thumbnail_text") or plan["title"], cfg["series_label"],
                           workdir / "thumbnail.jpg")
    return out, thumb, chapters_ts, total


def build_short(plan, cfg, workdir, finder, offline):
    sh = plan["short"]
    audio_dir = workdir / "audio_short"
    audio_dir.mkdir(exist_ok=True)
    sentences = [x.strip() for x in re.split(r"(?<=[।\?\!])\s+", sh["narration"]) if x.strip()]
    groups, cur = [], ""
    for s_ in sentences:  # ~1-2 sentences per picture
        cur = (cur + " " + s_).strip()
        if len(cur.split()) >= 14:
            groups.append(cur)
            cur = ""
    if cur:
        groups.append(cur)

    scenes = []
    for i, text in enumerate(groups):
        a = audio_dir / f"v{i:03d}.mp3"
        scenes.append({"text": text, "audio": a, "secs": speak(text, a, cfg, offline)})
    total = sum(s["secs"] + 0.45 for s in scenes)
    if total > cfg["short_max_seconds"]:
        f = min(1.2, total / cfg["short_max_seconds"])
        for s in scenes:
            s["secs"] = tempo(s["audio"], f)
        total = sum(s["secs"] + 0.45 for s in scenes)
    qs = sh.get("image_queries", [])
    for i, s in enumerate(scenes):
        queries = qs[i:] + qs[:i] + [q for c in plan["chapters"] for q in c.get("image_queries", [])]
        s["frame"], _ = frame_for(i, queries, finder, SHORT, workdir, sh["title"], offline, "v")
    out = workdir / "short.mp4"
    render_video(scenes, out, workdir, workdir / "music.wav", cfg, vertical=True)
    log(f"Short length: {total:.0f}s")
    return out


def description_long(plan, chapters_ts, finder, cfg):
    parts = [plan["description"].strip(), "", "📌 अध्याय (Chapters):"]
    parts += [f"{t} {h}" for t, h in chapters_ts]
    parts += ["", " ".join(plan.get("hashtags", [])), "",
              "🔔 प्राचीन भारत की और कहानियों के लिए चैनल को सब्सक्राइब करें।", "",
              "ℹ️ इस वीडियो में नैरेशन AI वॉइस से तैयार किया गया है। तथ्य इतिहास के मान्य स्रोतों पर आधारित हैं; "
              "जहाँ बात परंपरा या मान्यता की है, वहाँ स्पष्ट बताया गया है।",
              "🎵 बैकग्राउंड संगीत: मूल रचना (original, self-generated)."]
    credits = list(finder.credits.values())
    if credits:
        parts += ["", "📷 Image credits (free licences):"] + [f"• {c}" for c in credits]
    text = "\n".join(parts)
    while len(text) > 4900 and credits:  # keep credits complete; trim the summary first
        plan["description"] = plan["description"][: max(0, len(plan["description"]) - 300)]
        parts[0] = plan["description"]
        text = "\n".join(parts)
        if not plan["description"]:
            break
    return text[:4900]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--topic")
    ap.add_argument("--out", default=str(ROOT / "output"))
    args = ap.parse_args()
    args.topic = args.topic or env("BOT_TOPIC")
    args.no_upload = args.no_upload or env("BOT_UPLOAD") == "false"

    cfg = load_config()
    st = load_state()
    workdir = Path(args.out)
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)

    if args.offline:
        plan = json.loads((ROOT / "sample_plan.json").read_text(encoding="utf-8"))
        topic = plan["topic"]
    else:
        topic = args.topic or next_topic(st)
        log(f"topic: {topic}")
        plan = script_gen.make_plan(topic, cfg, st["titles"])
        plan["chapters_written"] = []
        for i in range(len(plan["chapters"])):
            log(f"writing chapter {i + 1}/{len(plan['chapters'])}")
            ch = script_gen.write_chapter(plan, i, cfg)
            ch["image_queries"] = plan["chapters"][i].get("image_queries", [])
            plan["chapters_written"].append(ch)
    plan.setdefault("fallback_queries", [topic])
    (workdir / "script.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    make_loop(workdir / "music.wav")
    finder = ImageFinder(workdir, cfg)
    result = {"topic": topic, "date": dt.date.today().isoformat()}

    long_id = None
    if cfg.get("make_long", True):
        long_mp4, thumb, chapters_ts, total = build_long(plan, cfg, workdir, finder, args.offline)
        desc = description_long(plan, chapters_ts, finder, cfg)
        (workdir / "description.txt").write_text(desc, encoding="utf-8")
        result.update(long_video=str(long_mp4), long_minutes=round(total / 60, 2), title=plan["title"])
        log(f"long video ready: {total / 60:.1f} min")
        if not args.no_upload and not args.offline and upload.can_upload():
            long_id = upload.upload(long_mp4, plan["title"], desc, plan.get("tags", []), cfg, thumb)
            result["long_id"] = long_id

    if cfg.get("make_short", True):
        short_mp4 = build_short(plan, cfg, workdir, finder, args.offline)
        sh = plan["short"]
        title = sh["title"] if "#shorts" in sh["title"].lower() else f"{sh['title']} #Shorts"
        sdesc = "\n".join([sh["narration"].split("।")[0] + "।", "",
                           f"▶ पूरी वीडियो: https://youtu.be/{long_id}" if long_id else f"▶ {cfg['channel_url']}",
                           "", "#Shorts #प्राचीनभारत #IndianHistory",
                           "ℹ️ AI वॉइस नैरेशन | 🎵 मूल संगीत (original)"]
                          + (["", "📷 Image credits:"] + [f"• {c}" for c in finder.credits.values()]
                             if finder.credits else []))
        (workdir / "short_description.txt").write_text(sdesc, encoding="utf-8")
        result["short_video"] = str(short_mp4)
        if not args.no_upload and not args.offline and upload.can_upload():
            result["short_id"] = upload.upload(short_mp4, title, sdesc[:4900], plan.get("tags", [])[:15] + ["shorts"], cfg)

    (workdir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.offline:
        st["topics"].append(topic)
        st["titles"].append(plan["title"])
        st["videos"].append(result)
        save_state(st)
    log("done: " + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
