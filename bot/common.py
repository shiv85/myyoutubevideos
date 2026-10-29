import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "fonts"
FONT_BOLD = FONTS / "Hind-Bold.ttf"
FONT_SEMI = FONTS / "Hind-SemiBold.ttf"


def load_config():
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def log(*a):
    print("[bot]", *a, flush=True)


def run(cmd, quiet=True):
    """Run a command; raise with stderr tail on failure."""
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(map(str, cmd[:6]))} ...\n{r.stderr[-2500:]}")
    if not quiet:
        print(r.stdout)
    return r.stdout


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def env(name, default=None):
    v = os.getenv(name)
    return v if v not in (None, "") else default
