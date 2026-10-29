"""Male Hindi narration with Microsoft Edge neural voices (free, no API key).
Default voice: hi-IN-MadhurNeural (male)."""
import asyncio
import time

import edge_tts

from .common import duration, log, run


async def _speak(text, out, voice, rate, pitch):
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(out))


def speak(text, out, cfg, offline=False):
    """Writes narration to `out` (mp3) and returns its duration in seconds."""
    if offline:  # test mode: silence with a realistic length (~2.4 words/sec)
        secs = max(2.0, len(text.split()) / 2.4)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{secs:.2f}",
             "-c:a", "libmp3lame", "-q:a", "4", str(out)])
        return secs
    for attempt in range(5):
        try:
            asyncio.run(_speak(text, out, cfg["voice"], cfg["voice_rate"], cfg["voice_pitch"]))
            d = duration(out)
            if d > 0.5:
                return d
        except Exception as e:
            log(f"TTS retry {attempt + 1}: {e}")
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("Text-to-speech failed 5 times")
