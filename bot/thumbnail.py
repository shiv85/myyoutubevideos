"""Thumbnail (1280x720) and opening title card, drawn from scratch with Pillow."""
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .common import FONT_BOLD, FONT_SEMI


def _cover(im, W, H):
    s = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    l, t = (im.width - W) // 2, (im.height - H) // 2
    return im.crop((l, t, l + W, t + H))


def _wrap(d, text, font, maxw):
    lines, cur = [], ""
    for w in text.split():
        if cur and d.textlength(cur + " " + w, font=font) > maxw:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + [cur]


def make_thumbnail(bg_path, big_text, label, out, size=(1280, 720)):
    W, H = size
    im = _cover(Image.open(bg_path).convert("RGB"), W, H)
    im = Image.eval(im, lambda v: min(255, int(v * 1.08)))  # a touch brighter
    # dark gradient on the left for readable text
    shade = Image.new("L", (W, H))
    sd = ImageDraw.Draw(shade)
    for x in range(W):
        sd.line([(x, 0), (x, H)], fill=int(215 * max(0.0, 1 - x / (W * 0.75))))
    im = Image.composite(Image.new("RGB", (W, H), (10, 5, 0)), im, shade)
    d = ImageDraw.Draw(im)

    size_px = int(H * 0.20)
    while size_px > 40:
        font = ImageFont.truetype(str(FONT_BOLD), size_px)
        lines = _wrap(d, big_text, font, W * 0.62)
        if len(lines) <= 3 and len(lines) * size_px * 1.15 < H * 0.7:
            break
        size_px -= 6
    y = int(H * 0.5 - len(lines) * size_px * 1.15 / 2) + int(H * 0.05)
    colours = [(255, 214, 0), (255, 255, 255), (255, 214, 0)]
    for i, ln in enumerate(lines):
        d.text((int(W * 0.05), y), ln, font=font, fill=colours[i % 3], stroke_width=max(4, size_px // 14),
               stroke_fill=(0, 0, 0))
        y += int(size_px * 1.15)

    lf = ImageFont.truetype(str(FONT_SEMI), int(H * 0.06))
    tw = d.textlength(label, font=lf)
    pad = int(H * 0.02)
    d.rounded_rectangle([int(W * 0.05) - pad, int(H * 0.07) - pad, int(W * 0.05) + tw + pad,
                         int(H * 0.07) + lf.size + pad], radius=10, fill=(200, 30, 30))
    d.text((int(W * 0.05), int(H * 0.07)), label, font=lf, fill=(255, 255, 255))
    im.save(out, "JPEG", quality=90)
    return out


def make_title_card(bg_path, title, out, size):
    W, H = size
    im = _cover(Image.open(bg_path).convert("RGB"), W, H).filter(ImageFilter.GaussianBlur(6))
    im = Image.eval(im, lambda v: int(v * 0.4))
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(FONT_BOLD), int(min(W, H) * 0.085))
    lines = _wrap(d, title, font, W * 0.85)
    lh = int(font.size * 1.3)
    y = H // 2 - lh * len(lines) // 2
    for ln in lines:
        d.text((W // 2, y), ln, font=font, fill=(255, 225, 150), anchor="mt", stroke_width=4, stroke_fill=(0, 0, 0))
        y += lh
    im.save(out, "JPEG", quality=92)
    return out
