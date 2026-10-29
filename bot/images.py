"""Finds copyright-safe pictures.

Sources (in order):
 1. Wikimedia Commons — only files whose licence is Public Domain, CC0, CC BY or CC BY-SA
    (set "image_licenses": "public_domain_only" in config.json to allow only PD/CC0).
    Every CC BY / BY-SA image is credited automatically in the video description.
 2. Pexels (optional, if PEXELS_API_KEY is set) — free licence, no attribution needed.
 3. A generated artwork card (made from scratch by this program) if nothing safe is found.
Nothing is ever taken from Google Images, other YouTube videos, films or TV."""
import hashlib
import html
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .common import FONT_BOLD, env, log

UA = {"User-Agent": "AncientIndiaHistoryBot/1.0 (educational YouTube channel; python-requests)"}
COMMONS = "https://commons.wikimedia.org/w/api.php"

FREE_RE = re.compile(r"^(public domain|pd\b|pd-|cc0|cc[ -]?zero|cc by(-sa)?[ -]?\d|cc-by(-sa)?-\d|no restrictions)",
                     re.I)
PD_RE = re.compile(r"^(public domain|pd\b|pd-|cc0|cc[ -]?zero|no restrictions)", re.I)
BLOCK_WORDS = re.compile(r"(film|movie|poster|actor|actress|serial|logo|screenshot|cosplay|election|"
                         r"selfie|wedding|tv |television|cartoon|anime|banner|flag of|map of india \d{4})", re.I)


def _strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


class ImageFinder:
    def __init__(self, workdir, cfg):
        self.dir = Path(workdir) / "img"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lic_re = PD_RE if cfg.get("image_licenses") == "public_domain_only" else FREE_RE
        self.used_urls = set()
        self.credits = {}  # url -> credit line
        self.cache = {}    # query -> list of candidates

    # ---------- Wikimedia Commons ----------
    def _commons(self, query):
        if query in self.cache:
            return self.cache[query]
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 20,
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 2400,
        }
        out = []
        try:
            pages = requests.get(COMMONS, params=params, headers=UA, timeout=30).json() \
                .get("query", {}).get("pages", {})
        except Exception as e:
            log(f"commons search failed for '{query}': {e}")
            pages = {}
        for p in sorted(pages.values(), key=lambda p: p.get("index", 99)):
            info = (p.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            lic = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
            title = p.get("title", "")
            if info.get("mime") not in ("image/jpeg", "image/png"):
                continue
            if info.get("width", 0) < 900 or info.get("height", 0) < 500:
                continue
            if not self.lic_re.search(lic) or BLOCK_WORDS.search(title):
                continue
            if "nonfree" in lic.lower().replace("-", "") or "fair use" in lic.lower():
                continue
            artist = _strip_html(meta.get("Artist", {}).get("value", "")) or "Unknown"
            artist = re.sub(r"\s+", " ", artist)[:60]
            name = title.replace("File:", "").rsplit(".", 1)[0][:60]
            out.append({
                "url": info.get("thumburl") or info["url"],
                "credit": f"{name} — {artist}, {lic}, via Wikimedia Commons ({info.get('descriptionshorturl') or info.get('descriptionurl')})",
                "needs_credit": not PD_RE.search(lic),
            })
        self.cache[query] = out
        return out

    # ---------- Pexels (optional) ----------
    def _pexels(self, query):
        key = env("PEXELS_API_KEY")
        if not key:
            return []
        try:
            r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": key},
                             params={"query": query, "per_page": 10, "orientation": "landscape"}, timeout=30)
            return [{"url": p["src"]["large2x"], "credit": f"Photo by {p['photographer']} on Pexels",
                     "needs_credit": False} for p in r.json().get("photos", [])]
        except Exception as e:
            log(f"pexels failed: {e}")
            return []

    def _download(self, cand):
        name = hashlib.md5(cand["url"].encode()).hexdigest()[:16] + ".jpg"
        path = self.dir / name
        if not path.exists():
            r = requests.get(cand["url"], headers=UA, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
            Image.open(path).convert("RGB").save(path, "JPEG", quality=92)  # validate + normalise
        return path

    def find(self, queries, offline=False, allow_reuse=False):
        """Returns (path or None). Tries each query; avoids reusing pictures."""
        if offline:
            return None
        for q in [q for q in queries if q]:
            for source in (self._commons, self._pexels):
                for cand in source(q):
                    if cand["url"] in self.used_urls and not allow_reuse:
                        continue
                    try:
                        p = self._download(cand)
                    except Exception as e:
                        log(f"download failed: {e}")
                        continue
                    self.used_urls.add(cand["url"])
                    if cand["needs_credit"] or "Pexels" in cand["credit"]:
                        self.credits[cand["url"]] = cand["credit"]
                    return p
        return None


# ---------- framing ----------
def fit_frame(src, out, size):
    """Places any picture on a WxH canvas: blurred, darkened copy behind, full picture in front
    (nothing important gets cropped off old paintings)."""
    W, H = size
    im = Image.open(src).convert("RGB")
    bg = im.copy()
    s = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * s) + 1, int(bg.height * s) + 1))
    bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
    bg = bg.filter(ImageFilter.GaussianBlur(40)).point(lambda v: int(v * 0.45))
    s = min(W / im.width, H / im.height)
    # if the picture nearly fills the frame anyway, just cover-crop it
    if (im.width * s) / W > 0.82 and (im.height * s) / H > 0.82:
        s = max(W / im.width, H / im.height)
    fg = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    bg.crop((0, 0, W, H)).save(out, "JPEG", quality=92)
    return out


PALETTES = [((61, 32, 10), (196, 120, 40)), ((20, 30, 60), (180, 120, 50)), ((50, 15, 15), (210, 150, 60)),
            ((15, 40, 35), (190, 150, 70))]


def art_card(text, out, size, seed=0):
    """Original generated background (gradient + mandala rings + text). 100% our own artwork."""
    W, H = size
    c1, c2 = PALETTES[seed % len(PALETTES)]
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t * 0.6) for i in range(3)))
    cx, cy = W // 2, H // 2
    for r in range(80, max(W, H), 70):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*[min(255, v + 40) for v in c2],), width=2)
    im = im.filter(ImageFilter.GaussianBlur(2))
    d = ImageDraw.Draw(im)
    if not text.strip():
        im.save(out, "JPEG", quality=92)
        return out
    font = ImageFont.truetype(str(FONT_BOLD), int(min(W, H) * 0.09))
    words, lines, cur = text.split(), [], ""
    for w in words:
        if d.textlength(cur + " " + w, font=font) > W * 0.8 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    lh = int(font.size * 1.35)
    y = cy - lh * len(lines) // 2
    for ln in lines:
        d.text((cx, y), ln, font=font, fill=(255, 236, 190), anchor="mt", stroke_width=4, stroke_fill=(30, 15, 5))
        y += lh
    im.save(out, "JPEG", quality=92)
    return out
