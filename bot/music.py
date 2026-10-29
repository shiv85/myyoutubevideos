"""Original background music, synthesised from scratch (tanpura-style drone + soft plucks).
Because the program generates it, there is no copyright claim risk."""
import wave

import numpy as np

SR = 44100
LOOP = 32.0          # seconds; all frequencies complete whole cycles in 32 s -> seamless loop
SA = 138.5           # tonic (~C#3)


def _ks_pluck(freq, secs, decay=0.9965, seed=0):
    """Karplus-Strong plucked string."""
    rng = np.random.default_rng(seed)
    n = int(SR * secs)
    period = int(SR / freq)
    buf = rng.uniform(-1, 1, period).astype(np.float32)
    out = np.empty(n, np.float32)
    for i in range(n):
        j = i % period
        out[i] = buf[j]
        buf[j] = decay * 0.5 * (buf[j] + buf[(j + 1) % period])
    return out


def make_loop(path):
    n = int(SR * LOOP)
    t = np.arange(n) / SR
    mix = np.zeros(n, np.float32)
    # soft drone on Sa, Pa and low Sa with slow breathing
    for f, a in [(SA / 2, 0.30), (SA, 0.22), (SA * 1.5, 0.12), (SA * 2, 0.08), (SA * 3, 0.03)]:
        mix += a * np.sin(2 * np.pi * f * t).astype(np.float32)
    mix *= (0.75 + 0.25 * np.sin(2 * np.pi * t / 8.0)).astype(np.float32)
    # tanpura pattern: Pa  Sa  Sa  low-Sa, one pluck per second, wrapped circularly
    pattern = [SA * 0.75, SA, SA, SA / 2]
    for k in range(int(LOOP)):
        p = _ks_pluck(pattern[k % 4], 3.5, seed=k)
        start = k * SR
        idx = (np.arange(len(p)) + start) % n
        np.add.at(mix, idx, 0.45 * p)
    mix /= np.max(np.abs(mix)) + 1e-6
    data = (mix * 0.8 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    return path
