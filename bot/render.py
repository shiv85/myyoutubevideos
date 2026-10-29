"""Turns scenes (picture + narration) into a finished video with Ken Burns motion,
burned-in Hindi subtitles, chapter headings, original music and loudness normalisation."""
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from .common import FONTS, log, run

LONG = (1920, 1080)
SHORT = (1080, 1920)


def _ass_time(t):
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_text(text, max_words):
    """Split narration into subtitle-sized pieces, preferring punctuation breaks."""
    pieces = [p.strip() for p in re.split(r"(?<=[।\?\!,;])\s+", text.strip()) if p.strip()]
    out = []
    for p in pieces:
        w = p.split()
        while len(w) > max_words:
            cut = max_words if len(w) - max_words >= 3 else (len(w) + 1) // 2
            out.append(" ".join(w[:cut]))
            w = w[cut:]
        if w:
            out.append(" ".join(w))
    return out


def write_ass(path, text, speech_secs, size, heading=None, vertical=False):
    W, H = size
    fs = 72 if not vertical else 92
    margin_v = 70 if not vertical else 520
    lines = chunk_text(text, 4 if vertical else 7) if text else []
    total_chars = sum(len(l) for l in lines) or 1
    events, t = [], 0.15
    for l in lines:
        d = speech_secs * len(l) / total_chars
        events.append(f"Dialogue: 0,{_ass_time(t)},{_ass_time(t + d)},Sub,,0,0,0,,{_esc(l)}")
        t += d
    if heading:
        events.append(f"Dialogue: 1,{_ass_time(0.2)},{_ass_time(4.5)},Head,,0,0,0,,{{\\fad(400,400)}}{_esc(heading)}")
    path.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Hind SemiBold,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,{5 if vertical else 4},2,2,80,80,{margin_v},1
Style: Head,Hind,{fs + 6},&H0060D8FF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,7,70,70,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n", encoding="utf-8")


MOTIONS = [
    ("1.0+0.10*on/{N}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),          # slow zoom in
    ("1.10-0.10*on/{N}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),         # slow zoom out
    ("1.10", "(iw-iw/zoom)*on/{N}", "ih/2-(ih/zoom/2)"),                  # pan left -> right
    ("1.10", "(iw-iw/zoom)*(1-on/{N})", "ih/2-(ih/zoom/2)"),              # pan right -> left
]


def render_clip(i, frame_jpg, audio, speech_secs, out, size, fps, text, heading=None, vertical=False):
    W, H = size
    total = speech_secs + 0.45
    N = max(1, int(total * fps))
    big = out.with_suffix(".big.jpg")
    Image.open(frame_jpg).resize((int(W * 1.5), int(H * 1.5)), Image.LANCZOS).save(big, quality=93)
    ass = out.with_suffix(".ass")
    write_ass(ass, text, speech_secs, size, heading, vertical)
    z, x, y = (m.format(N=N) for m in MOTIONS[i % len(MOTIONS)])
    vf = (f"[0:v]zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps},"
          f"subtitles={ass}:fontsdir={FONTS},"
          f"fade=t=in:st=0:d=0.3,fade=t=out:st={total - 0.3:.2f}:d=0.3,format=yuv420p[v];"
          f"[1:a]aresample=44100,apad[a]")
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(big), "-i", str(audio),
         "-filter_complex", vf, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(fps), "-g", str(fps * 4),
         "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "44100", str(out)])
    big.unlink(missing_ok=True)
    return total


def render_video(scenes, out_path, workdir, music_wav, cfg, vertical=False):
    """scenes: list of dicts {frame, audio, secs, text, heading?}. Returns list of clip start times."""
    size = SHORT if vertical else LONG
    fps = cfg.get("fps", 25)
    workdir = Path(workdir) / ("clips_v" if vertical else "clips_h")
    workdir.mkdir(parents=True, exist_ok=True)
    clips = [workdir / f"c{i:03d}.mp4" for i in range(len(scenes))]

    def job(i):
        s = scenes[i]
        return render_clip(i, s["frame"], s["audio"], s["secs"], clips[i], size, fps,
                           s.get("text", ""), s.get("heading"), vertical)

    workers = max(1, min(3, (os.cpu_count() or 2) // 2))
    log(f"rendering {len(scenes)} clips ({'Short' if vertical else 'long'}) with {workers} workers")
    with ThreadPoolExecutor(workers) as ex:
        durations = list(ex.map(job, range(len(scenes))))

    lst = workdir / "list.txt"
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    joined = workdir / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])

    vol = cfg.get("music_volume", 0.08)
    total = sum(durations)
    run(["ffmpeg", "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(music_wav),
         "-filter_complex",
         f"[1:a]aresample=44100,volume={vol},afade=t=in:d=2,afade=t=out:st={max(0, total - 3):.2f}:d=3[m];"
         f"[0:a][m]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-t", f"{total:.3f}", "-movflags", "+faststart", str(out_path)])
    starts, t = [], 0.0
    for d in durations:
        starts.append(t)
        t += d
    return starts, total
