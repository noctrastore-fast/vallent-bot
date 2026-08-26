"""
VALLENT EXS — Rank Card Renderer
===================================
Generate rank-card & level-up card gambar secara LOKAL pakai Pillow — gak
gantung ke API pihak ketiga (some-random-api.com dkk) yang gampang down /
kena rate limit. Style-nya dark red/crimson, ngikutin branding VALLENT EXS.

Font di-bundle sendiri di assets/fonts/ (lisensi SIL OFL, boleh
didistribusikan ulang) biar tampilannya konsisten di mesin manapun bot
di-deploy (Railway, VPS, lokal, dll) — gak tergantung font apa yang
kebetulan ke-install di OS host.

FONT FALLBACK CHAIN — kenapa ini penting:
Font utama (BigShoulders / Outfit) itu font Latin biasa, banyak karakter
yang gak ke-cover (Cyrillic, Yunani, Arab, Thai, emoji, dll) — kalau
dipaksa render bakal jadi kotak "tofu" putih. Username Discord bisa
berisi HAMPIR APAPUN, jadi setiap karakter dicek satu-satu: kalau font
utama gak punya glyph-nya, otomatis lempar ke font fallback yang cocok
(NotoSans utk Cyrillic/Yunani/Vietnam, NotoSansArabic, NotoSansThai,
NotoEmoji utk emoji). Kalau semua fallback juga gak punya, baru barulah
dibiarkan tofu (kasus sangat jarang — misal aksara langka).
"""

import io
import logging
import math
import os
import random
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger("rank_card")

# ══════════════════════════════════════════════════════════════════
# ASSETS
# ══════════════════════════════════════════════════════════════════

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_BASE_DIR, "assets", "fonts")

F_DISPLAY = os.path.join(_FONT_DIR, "BigShoulders-Bold.ttf")
F_BOLD    = os.path.join(_FONT_DIR, "Outfit-Bold.ttf")
F_REG     = os.path.join(_FONT_DIR, "Outfit-Regular.ttf")

# Font fallback, urut dari yang paling mungkin kepakai. Semua ini variable
# font satu file yang nyimpen banyak ketebalan (axis "wght"), jadi kita
# tinggal set beratnya on-the-fly gak perlu file terpisah per bold/regular.
_FALLBACK_FONTS = [
    os.path.join(_FONT_DIR, "NotoSans-Var.ttf"),        # Latin extended, Cyrillic, Yunani, Vietnam, dst
    os.path.join(_FONT_DIR, "NotoSansArabic-Var.ttf"),  # Arab
    os.path.join(_FONT_DIR, "NotoSansThai-Var.ttf"),    # Thai
    os.path.join(_FONT_DIR, "NotoEmoji-Var.ttf"),       # Emoji (monokrom outline, bukan colored)
]

_font_cache: dict = {}
_cmap_cache: dict = {}

def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """Load font dasar (Latin) dengan cache + fallback ke default PIL font
    kalau file-nya gak ketemu — biar card tetap kegenerate (walau kurang
    cantik) daripada bikin command crash total."""
    key = (path, size)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(path, size)
        except Exception as e:
            log.warning(f"Font gagal dimuat ({path}): {e} — pakai fallback default.")
            _font_cache[key] = ImageFont.load_default(size=size)
    return _font_cache[key]

def _get_cmap(path: str) -> set:
    """Daftar codepoint yang beneran punya glyph di font tersebut — dicek
    sekali per font lalu di-cache, karena baca cmap itu operasi yang agak
    berat kalau diulang tiap karakter."""
    if path not in _cmap_cache:
        try:
            from fontTools.ttLib import TTFont as _TTFont
            _cmap_cache[path] = set(_TTFont(path, fontNumber=0).getBestCmap().keys())
        except Exception as e:
            log.warning(f"Gagal baca cmap {path}: {e}")
            _cmap_cache[path] = set()
    return _cmap_cache[path]

def _load_variable(path: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    """Load font fallback (variable font) di berat tertentu, dengan cache."""
    key = (path, size, weight)
    if key not in _font_cache:
        try:
            f = ImageFont.truetype(path, size)
            try:
                f.set_variation_by_axes([weight] if len(f.get_variation_axes()) == 1 else [weight, 100])
            except Exception:
                pass
            _font_cache[key] = f
        except Exception as e:
            log.warning(f"Font fallback gagal dimuat ({path}): {e}")
            _font_cache[key] = ImageFont.load_default(size=size)
    return _font_cache[key]

def _resolve_font(ch: str, primary_path: str, size: int, bold: bool):
    """Cari font pertama (utama, lalu fallback berurutan) yang punya glyph
    buat karakter ini. Kalau gak ada satupun yang cocok, tetap kembalikan
    font utama (best effort — tofu box, tapi command gak crash)."""
    if ch.isspace() or ord(ch) in _get_cmap(primary_path):
        return _font(primary_path, size)
    weight = 700 if bold else 400
    for fb_path in _FALLBACK_FONTS:
        if os.path.exists(fb_path) and ord(ch) in _get_cmap(fb_path):
            return _load_variable(fb_path, size, weight)
    return _font(primary_path, size)

def _runs(text: str, primary_path: str, size: int, bold: bool = True):
    """Pecah teks jadi potongan-potongan (substring, font) — tiap potongan
    pakai satu font yang sama, biar hemat draw call dan render-nya rapi."""
    runs, cur_font, cur_text = [], None, ""
    for ch in text:
        f = _resolve_font(ch, primary_path, size, bold)
        if f is cur_font:
            cur_text += ch
        else:
            if cur_text:
                runs.append((cur_text, cur_font))
            cur_font, cur_text = f, ch
    if cur_text:
        runs.append((cur_text, cur_font))
    return runs

def draw_text(draw: ImageDraw.ImageDraw, xy, text: str, primary_path: str, size: int, fill, bold: bool = True) -> None:
    """Ganti draw.text() biasa — otomatis lempar tiap karakter yang gak
    ke-cover font utama ke font fallback yang punya glyph-nya."""
    x, y = xy
    for t, f in _runs(text, primary_path, size, bold):
        draw.text((x, y), t, font=f, fill=fill)
        x += draw.textlength(t, font=f)

def text_width(draw: ImageDraw.ImageDraw, text: str, primary_path: str, size: int, bold: bool = True) -> float:
    """Ganti draw.textlength() biasa — ngukur lebar teks yang mixed-font."""
    return sum(draw.textlength(t, font=f) for t, f in _runs(text, primary_path, size, bold))

# ══════════════════════════════════════════════════════════════════
# PALETTE — samain sama COLOR_* di vallent.py
# ══════════════════════════════════════════════════════════════════

CRIMSON   = (220, 20, 60)
DARK_RED  = (139, 0, 0)
BG_TOP    = (14, 8, 9)
BG_BOTTOM = (35, 8, 10)
WHITE     = (245, 245, 245)
MUTED     = (170, 150, 150)
GOLD      = (245, 158, 11)

# Premium variants — swapped in wherever a normal card uses the crimson
# palette, so a premium card reads as gold-themed top to bottom instead of
# "red card with a gold ring slapped on".
GOLD_DARK    = (110, 80, 8)     # premium equivalent of DARK_RED (border/blood)
GOLD_BG_TOP  = (16, 12, 4)      # premium equivalent of BG_TOP
GOLD_BG_BTM  = (42, 28, 4)      # premium equivalent of BG_BOTTOM

# ══════════════════════════════════════════════════════════════════
# PRIMITIVES
# ══════════════════════════════════════════════════════════════════

def _lerp_multi(colors: list, t: float) -> tuple:
    """Interpolate a color at position t (0..1) across N>=1 stops evenly
    spaced along the gradient — generalizes _lerp_color to support the
    optional 3rd gradient color, while `_lerp_multi([c1, c2], t)` still
    behaves exactly like the old 2-color _lerp_color."""
    n = len(colors)
    if n == 1:
        return tuple(colors[0])
    t = max(0.0, min(1.0, t))
    seg = 1.0 / (n - 1)
    idx = min(int(t / seg), n - 2)
    local_t = (t - idx * seg) / seg
    return _lerp_color(colors[idx], colors[idx + 1], local_t)

def _vertical_gradient_multi(size, colors: list) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        base.putpixel((0, y), _lerp_multi(colors, t))
    return base.resize((w, h))

def _horizontal_gradient_multi(size, colors: list) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (w, 1))
    for x in range(w):
        t = x / max(w - 1, 1)
        base.putpixel((x, 0), _lerp_multi(colors, t))
    return base.resize((w, h))

def _vertical_gradient(size, top, bottom) -> Image.Image:
    return _vertical_gradient_multi(size, [top, bottom])

def _horizontal_gradient(size, left, right) -> Image.Image:
    return _horizontal_gradient_multi(size, [left, right])

def _lerp_color(c1, c2, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

def _darken(c, factor: float) -> tuple:
    return tuple(int(ch * factor) for ch in c)

def _lighten(c, factor: float) -> tuple:
    return tuple(int(ch + (255 - ch) * factor) for ch in c)

def _gradient_text(canvas: Image.Image, xy, text: str, primary_path: str, size: int, colors, bold: bool = True) -> float:
    """Draw `text` filled with a left-to-right gradient (2 or 3 colors)
    instead of a flat color — used for the premium accent label when the
    user has a custom gradient set. `colors` is a list of 2+ (r,g,b)
    stops. `canvas` must be an RGBA image (every card here is). Returns
    the drawn width so callers can position what comes next. Degrades to
    nothing drawn (returns 0) for empty text rather than raising."""
    x, y = xy
    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    total_w = int(text_width(scratch, text, primary_path, size, bold)) + 2
    if total_w <= 0:
        return 0
    probe = _font(primary_path, size)
    ascent, descent = probe.getmetrics()
    total_h = ascent + descent + 4
    mask_layer = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw_text(ImageDraw.Draw(mask_layer), (0, 0), text, primary_path, size, (255, 255, 255, 255), bold)
    grad = _horizontal_gradient_multi((total_w, total_h), list(colors)).convert("RGBA")
    grad.putalpha(mask_layer.split()[-1])
    canvas.paste(grad, (int(x), int(y)), grad)
    return total_w

def _rounded_mask(size, radius) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask

def _safe_avatar(avatar_bytes: bytes) -> Image.Image:
    """Kalau avatar gagal di-decode (network error, format aneh, dll), pakai
    placeholder abu-abu polos daripada bikin seluruh card gagal digenerate."""
    try:
        return Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    except Exception:
        placeholder = Image.new("RGBA", (256, 256), (45, 45, 45, 255))
        ImageDraw.Draw(placeholder).ellipse([48, 40, 208, 200], fill=(90, 90, 90, 255))
        return placeholder

def cover_image(image_bytes: bytes, size) -> Optional[Image.Image]:
    """Decode a user-supplied background image and crop/scale it to fully
    cover `size` (W, H) — same idea as CSS `background-size: cover`, so an
    arbitrary aspect-ratio upload never stretches or leaves gaps. Returns
    None (never raises) on any decode failure so a bad/dead image URL just
    falls back to the normal gradient background instead of crashing the
    whole card render."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    except Exception as e:
        log.warning(f"Custom rank card background gagal di-decode: {e}")
        return None
    w, h = size
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new_w, new_h = math.ceil(src_w * scale), math.ceil(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top  = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))

def _circle_avatar(avatar_img: Image.Image, diameter: int, ring_color, ring_width: int = 6) -> Image.Image:
    avatar_img = avatar_img.convert("RGBA").resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, diameter, diameter], fill=255)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(avatar_img, (0, 0), mask)
    ring_size = diameter + ring_width * 2
    ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    if isinstance(ring_color[0], (tuple, list)):
        fill_layer = _vertical_gradient_multi((ring_size, ring_size), list(ring_color)).convert("RGBA")
        ring_mask = Image.new("L", (ring_size, ring_size), 0)
        ImageDraw.Draw(ring_mask).ellipse([0, 0, ring_size, ring_size], fill=255)
        fill_layer.putalpha(ring_mask)
        ring = fill_layer
    else:
        rd.ellipse([0, 0, ring_size, ring_size], fill=ring_color)
    rd = ImageDraw.Draw(ring)
    rd.ellipse([ring_width, ring_width, ring_width + diameter, ring_width + diameter], fill=(0, 0, 0, 0))
    ring.paste(out, (ring_width, ring_width), out)
    return ring

def _draw_diamond(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color) -> None:
    """Ikon diamond digambar langsung (bukan karakter font) — dipakai buat
    tag PREMIUM MEMBER supaya gak pernah jadi kotak tofu apapun font-nya."""
    draw.polygon([(cx, cy - r), (cx + r * 0.72, cy), (cx, cy + r), (cx - r * 0.72, cy)], fill=color)

def _draw_progress_bar(card_img: Image.Image, x, y, w, h, pct, track_color, fill_left, fill_right):
    draw = ImageDraw.Draw(card_img)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=track_color)
    pct = max(0.0, min(pct, 1.0))
    fill_w = max(int(w * pct), h) if pct > 0.01 else 0
    if fill_w > 0:
        grad = _horizontal_gradient((fill_w, h), fill_left, fill_right)
        mask = _rounded_mask((fill_w, h), h // 2)
        card_img.paste(grad, (x, y), mask)

# ══════════════════════════════════════════════════════════════════
# TACTICAL-CARD PRIMITIVES — distinctive shapes so cards don't read as a
# generic template: angled corner cut, hex avatar frame, HUD corner ticks,
# a huge low-opacity "VX" wordmark, and a fine grain texture for depth.
# ══════════════════════════════════════════════════════════════════

BLOOD  = (90, 4, 12)
SILVER = (192, 192, 200)
BRONZE = (205, 127, 50)

def _noise_texture(size, opacity: int = 8) -> Image.Image:
    w, h = size
    n = Image.effect_noise((w, h), 24).convert("L")
    alpha = Image.new("L", (w, h), opacity)
    return Image.merge("RGBA", (n, n, n, alpha))

def _hex_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    cx = cy = size / 2
    r = size / 2
    pts = [(cx + r * math.cos(math.radians(60 * i - 90)), cy + r * math.sin(math.radians(60 * i - 90))) for i in range(6)]
    d.polygon(pts, fill=255)
    return mask

def _hex_avatar(avatar_img: Image.Image, diameter: int, ring_color, ring_width: int = 6) -> Image.Image:
    """Hexagonal avatar frame — distinctive alternative to the standard
    circle-crop every rank-card bot uses. `ring_color` can be a plain (r,g,b)
    for a solid ring, or a list of 2-3 (r,g,b) stops for a diagonal gradient
    ring (used when a premium user has a custom gradient set)."""
    avatar_img = avatar_img.convert("RGBA").resize((diameter, diameter), Image.LANCZOS)
    inner_mask = _hex_mask(diameter)
    inner = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    inner.paste(avatar_img, (0, 0), inner_mask)

    ring_size  = diameter + ring_width * 2
    outer_mask = _hex_mask(ring_size)
    if isinstance(ring_color[0], (tuple, list)):
        ring_layer = _vertical_gradient_multi((ring_size, ring_size), list(ring_color)).convert("RGBA")
        ring_layer.putalpha(255)
    else:
        ring_layer = Image.new("RGBA", (ring_size, ring_size), (*ring_color, 255))
    ring_layer.putalpha(outer_mask)

    hole_mask = Image.new("L", (ring_size, ring_size), 0)
    hole_mask.paste(inner_mask, (ring_width, ring_width))
    ring_alpha = ring_layer.split()[3]
    ring_layer.putalpha(ImageChops.subtract(ring_alpha, hole_mask))

    final = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
    final.paste(ring_layer, (0, 0), ring_layer)
    final.paste(inner, (ring_width, ring_width), inner)
    return final

def _fire_aura(diameter: int, ring_width: int, colors: Optional[tuple] = None) -> Image.Image:
    """A soft, irregular 'blaze' licking up from behind a premium avatar's
    ring. Instead of sharp radiating spikes, this builds a few overlapping
    wavy coronas (uneven, flame-like bumps around the circumference) —
    a deep outer glow, a mid layer, and a bright inner lick — each blurred
    by a different amount so the inner layer reads sharper ("closer to the
    fire") and the outer layer reads softer (glow bleeding outward).
    Deterministic (no randomness) so re-rendering the same card looks the
    same every time.

    `colors` is an optional list of 2-3 (r,g,b) gradient stops — when a
    premium user has set a custom `rankcolor`, the flame itself shifts to
    match (outer glow leaning into the last color, the hot inner lick
    leaning into and brightening the first) instead of always burning
    gold."""
    pad  = max(int(diameter * 0.24), 20)
    size = diameter + ring_width * 2 + pad * 2
    cx = cy = size / 2
    base_r = diameter / 2 + ring_width

    if colors:
        colors = list(colors)
        mid = _lerp_multi(colors, 0.5)
        # (color, alpha, extra_radius, bump_amplitude, bump_count, phase, blur)
        layers = [
            (_darken(colors[-1], 0.4),  70,  pad * 0.95, pad * 0.55, 5, 0.4, 9),   # outer glow, leans into last color
            (mid,                       105, pad * 0.55, pad * 0.40, 6, 2.1, 6),   # mid blend
            (_lighten(colors[0], 0.4),  140, pad * 0.25, pad * 0.28, 7, 4.0, 3),   # bright inner lick, leans into first color
        ]
    else:
        # Every layer stays in the same gold family as the rest of the
        # default premium theme (no orange/red) so nothing clashes.
        layers = [
            ((90, 62, 4),    70,  pad * 0.95, pad * 0.55, 5, 0.4, 9),   # deep gold, outer glow
            ((160, 108, 10), 105, pad * 0.55, pad * 0.40, 6, 2.1, 6),   # mid gold
            ((245, 176, 60), 140, pad * 0.25, pad * 0.28, 7, 4.0, 3),   # bright gold, inner licks
        ]

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    n_pts = 96
    for color, alpha, extra_r, amp, bumps, phase, blur in layers:
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        pts = []
        for i in range(n_pts):
            t = i / n_pts * 2 * math.pi
            wobble = math.sin(bumps * t + phase) * amp + math.sin(bumps * 2.3 * t + phase * 1.7) * amp * 0.3
            r = base_r + extra_r + wobble
            pts.append((cx + math.cos(t) * r, cy + math.sin(t) * r))
        d.polygon(pts, fill=(*color, alpha))
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        out = Image.alpha_composite(out, layer)
    return out

def _corner_bracket(draw: ImageDraw.ImageDraw, x, y, size, color, flip_x=False, flip_y=False, width=3):

    dx = -1 if flip_x else 1
    dy = -1 if flip_y else 1
    draw.line([(x, y), (x + dx * size, y)], fill=color, width=width)
    draw.line([(x, y), (x, y + dy * size)], fill=color, width=width)

def _diagonal_clip_mask(w: int, h: int, cut: int = 46) -> Image.Image:
    """Card silhouette with the top-right corner sliced off at an angle —
    breaks up the 'plain rounded rectangle' silhouette every card uses."""
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon([(0, 0), (w - cut, 0), (w, cut), (w, h), (0, h)], fill=255)
    return mask

def _vx_watermark(size, opacity: int = 15) -> Image.Image:
    """Huge, faint 'VX' wordmark bleeding off the top-right — a branding
    fingerprint unique to this bot rather than a generic gradient card."""
    w, h = size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = _font(F_DISPLAY, int(h * 0.9))
    txt = "VX"
    tw = d.textlength(txt, font=f)
    d.text((w - tw * 0.55, -h * 0.22), txt, font=f, fill=(255, 255, 255, opacity))
    return layer.rotate(-8, resample=Image.BICUBIC)

def _segmented_bar(draw: ImageDraw.ImageDraw, x, y, w, h, pct, segments, track_color, fill_color, gap=3):
    """HUD-style tick-segmented bar instead of a plain smooth gradient pill.
    `fill_color` can be a plain (r,g,b) for a solid bar, or a list of 2-3
    (r,g,b) stops so each filled tick shades across the gradient — used
    for the premium custom gradient perk."""
    is_gradient = isinstance(fill_color[0], (tuple, list))
    seg_w  = (w - gap * (segments - 1)) / segments
    filled = max(0.0, min(pct, 1.0)) * segments
    for i in range(segments):
        sx = x + i * (seg_w + gap)
        draw.rectangle([sx, y, sx + seg_w, y + h], fill=track_color)
        amt = max(0.0, min(1.0, filled - i))
        if amt > 0:
            seg_fill = _lerp_multi(list(fill_color), i / max(segments - 1, 1)) if is_gradient else fill_color
            draw.rectangle([sx, y, sx + seg_w * amt, y + h], fill=seg_fill)

def _card_base(W: int, H: int, cut: int = 48, blood_xy=None, premium: bool = False, background_bytes: Optional[bytes] = None, accent_colors: Optional[tuple] = None) -> Image.Image:
    """Shared background stack for both cards: gradient + blood glow +
    VX watermark + grain texture, clipped to the angled card silhouette.
    `premium=True` swaps the whole palette to gold instead of crimson, so
    a premium card is unmistakably different at a glance, not just the
    avatar ring.

    `accent_colors` (premium-only, caller enforces that) is an optional
    list of 2-3 (r,g,b) stops — when set, it replaces the fixed gold
    palette with the user's own gradient: darker tints of every stop for
    the background, the first/last colors split across the corner
    brackets/HUD dots (with the exact middle stop on the middle bracket)
    so the frame itself visibly reads as a gradient, not just a single
    swapped color.

    `background_bytes` (premium-only feature, caller enforces that) swaps
    the flat gradient for a user-uploaded image, cropped to cover the full
    card. A dark scrim + the existing noise/watermark layers are still
    composited on top so text stays readable no matter how bright/busy the
    uploaded image is — the border, corner brackets and glow are untouched
    either way, so a custom background still unmistakably reads as a
    VALLENT EXS card, not a random image with text slapped on."""
    if premium and accent_colors:
        colors    = list(accent_colors)
        bg_colors = [_darken(c, 0.09 + i * (0.13 / max(len(colors) - 1, 1))) for i, c in enumerate(colors)]
        border    = _darken(colors[0], 0.5)
        corner    = colors[0]
        corner2   = colors[-1]
        corner_mid = _lerp_multi(colors, 0.5)
        blood     = _darken(colors[-1], 0.5)
    else:
        bg_colors = [GOLD_BG_TOP, GOLD_BG_BTM] if premium else [BG_TOP, BG_BOTTOM]
        border    = GOLD_DARK   if premium else DARK_RED
        corner    = GOLD        if premium else CRIMSON
        corner2   = corner
        corner_mid = corner
        blood     = GOLD_DARK   if premium else BLOOD

    custom_bg = cover_image(background_bytes, (W, H)) if background_bytes else None
    if custom_bg is not None:
        base = custom_bg.convert("RGBA")
        scrim = Image.new("RGBA", (W, H), (5, 3, 4, 150))
        base = Image.alpha_composite(base, scrim)
        # extra scrim behind where the avatar/text sit (left ~60% of the
        # card) so a bright/busy background never fights with the name.
        side_scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(side_scrim).rectangle([0, 0, int(W * 0.62), H], fill=(5, 3, 4, 110))
        side_scrim = side_scrim.filter(ImageFilter.GaussianBlur(40))
        base = Image.alpha_composite(base, side_scrim)
    else:
        base = _vertical_gradient_multi((W, H), bg_colors).convert("RGBA")
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        bx, by = blood_xy or (-180, H - 220)
        gd.ellipse([bx, by, bx + 560, by + 460], fill=(*blood, 80))
        base = Image.alpha_composite(base, glow)
    base = Image.alpha_composite(base, _vx_watermark((W, H)))
    base = Image.alpha_composite(base, _noise_texture((W, H)))
    clip = _diagonal_clip_mask(W, H, cut=cut)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.paste(base, (0, 0), clip)

    border_pts = [(0, 0), (W - cut, 0), (W, cut), (W, H), (0, H), (0, 0)]

    if premium:
        # ── PREMIUM ONLY: outer glow — a soft, blurred duplicate of the
        # frame sitting just behind the crisp stroke, so the border reads
        # as glowing neon instead of a flat single-pixel outline. Kept
        # exclusive to premium so the upgrade actually feels special
        # instead of every card looking the same. ──────────────────────
        glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow_layer).line(border_pts, fill=(*corner, 140), width=9)
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(5))
        canvas = Image.alpha_composite(canvas, glow_layer)

        draw = ImageDraw.Draw(canvas)
        draw.line(border_pts, fill=(*border, 255), width=3)

        # inset hairline: a faint second line just inside the main stroke
        # gives the frame some depth instead of a single flat outline
        inset = 5
        inset_pts = [
            (inset, inset), (W - cut - inset * 0.5, inset), (W - inset, cut + inset * 0.5),
            (W - inset, H - inset), (inset, H - inset), (inset, inset),
        ]
        draw.line(inset_pts, fill=(*corner, 100), width=1)

        _corner_bracket(draw, 16, 16, 26, (*corner, 235), width=3)
        _corner_bracket(draw, 16, H - 16, 26, (*corner_mid, 235), flip_y=True, width=3)
        _corner_bracket(draw, W - 16, H - 16, 26, (*corner2, 235), flip_x=True, flip_y=True, width=3)

        # small glowing HUD dots at each bracket's vertex
        for (bx, by), dot_c in [((16, 16), corner), ((16, H - 16), corner_mid), ((W - 16, H - 16), corner2)]:
            draw.ellipse([bx - 4, by - 4, bx + 4, by + 4], fill=(*dot_c, 90))
            draw.ellipse([bx - 2, by - 2, bx + 2, by + 2], fill=(*dot_c, 255))
    else:
        # ── REGULAR: plain flat outline, unchanged — kept simple on
        # purpose so the premium glow-up above actually stands out. ────
        draw = ImageDraw.Draw(canvas)
        draw.line(border_pts, fill=(*border, 255), width=3)
        _corner_bracket(draw, 16, 16, 24, (*corner, 220))
        _corner_bracket(draw, 16, H - 16, 24, (*corner, 220), flip_y=True)

    return canvas

def _flatten(canvas: Image.Image) -> io.BytesIO:
    out = Image.new("RGB", canvas.size, (8, 4, 5))
    out.paste(canvas, (0, 0), canvas)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ══════════════════════════════════════════════════════════════════
# RANK CARD — dipakai di command `rank` / `/rank`
# ══════════════════════════════════════════════════════════════════

def render_rank_card(
    avatar_bytes: bytes,
    username: str,
    level: int,
    rank: int,
    cur_xp: int,
    need_xp: int,
    total_xp: int,
    is_premium: bool = False,
    messages: int = 0,
    background_bytes: Optional[bytes] = None,
    accent_colors: Optional[tuple] = None,
) -> io.BytesIO:
    W, H = 934, 300
    cut  = 50
    grad = accent_colors if (is_premium and accent_colors) else None
    accent = grad[0] if grad else (GOLD if is_premium else CRIMSON)
    canvas = _card_base(W, H, cut=cut, premium=is_premium, background_bytes=background_bytes if is_premium else None, accent_colors=grad)
    draw   = ImageDraw.Draw(canvas)

    av = _safe_avatar(avatar_bytes)
    ring_color = grad if grad else accent
    avatar_d = 168
    hexring  = _hex_avatar(av, avatar_d, ring_color, ring_width=6)
    ax, ay = 50, (H - hexring.height) // 2
    if is_premium:
        aura = _fire_aura(avatar_d, ring_width=6, colors=grad)
        aura_pos = (int(ax + hexring.width / 2 - aura.width / 2), int(ay + hexring.height / 2 - aura.height / 2))
        canvas.paste(aura, aura_pos, aura)
    canvas.paste(hexring, (ax, ay), hexring)

    draw = ImageDraw.Draw(canvas)
    text_x = ax + hexring.width + 40
    max_w  = W - text_x - 70

    f_small = _font(F_BOLD, 22)
    f_tiny  = _font(F_REG, 18)
    f_xp    = _font(F_BOLD, 20)

    uname   = username.upper()
    name_y  = 36
    size    = 50
    while text_width(draw, uname, F_DISPLAY, size) > max_w and size > 26:
        size -= 2
    draw_text(draw, (text_x, name_y), uname, F_DISPLAY, size, WHITE)
    if grad:
        underline = _horizontal_gradient_multi((50, 4), grad).convert("RGBA")
        canvas.paste(underline, (text_x, name_y + size + 2))
    else:
        draw.rectangle([text_x, name_y + size + 2, text_x + 50, name_y + size + 6], fill=(*accent, 255))

    sub_y = name_y + size + 16
    if is_premium:
        _draw_diamond(draw, text_x + 7, sub_y + 11, 8, accent)
        # "PREMIUM" alone reads cleaner than "PREMIUM MEMBER" — same info,
        # less clutter, and it leaves room to breathe next to the diamond.
        if grad:
            _gradient_text(canvas, (text_x + 20, sub_y), "PREMIUM", F_BOLD, 22, grad)
            draw = ImageDraw.Draw(canvas)
        else:
            draw.text((text_x + 20, sub_y), "PREMIUM", font=f_small, fill=GOLD)
        sub_y += 28

    rl_y = sub_y + 6
    draw.text((text_x, rl_y), "RANK", font=f_tiny, fill=MUTED)
    rank_w = draw.textlength("RANK ", font=f_tiny)
    draw.text((text_x + rank_w, rl_y - 3), f"#{rank}", font=f_small, fill=WHITE)
    lvl_x = text_x + rank_w + draw.textlength(f"#{rank}", font=f_small) + 36
    draw.text((lvl_x, rl_y), "LEVEL", font=f_tiny, fill=MUTED)
    lvl_w = draw.textlength("LEVEL ", font=f_tiny)
    draw.text((lvl_x + lvl_w, rl_y - 3), str(level), font=f_small, fill=(*accent, 255))

    bar_y = rl_y + 42
    bar_w = W - text_x - 90
    bar_h = 22
    bar_fill = grad if grad else accent
    _segmented_bar(draw, text_x, bar_y, bar_w, bar_h, cur_xp / max(need_xp, 1), 20, (35, 16, 18), bar_fill, gap=3)

    xp_text = f"{cur_xp:,} / {need_xp:,} XP"
    xp_w = draw.textlength(xp_text, font=f_xp)
    draw.text((text_x + bar_w - xp_w, bar_y - 26), xp_text, font=f_xp, fill=WHITE)


    footer = f"TOTAL XP {total_xp:,}   //   MESSAGES {messages:,}"
    draw.text((text_x, bar_y + bar_h + 16), footer, font=f_tiny, fill=MUTED)

    _watermark(draw, canvas.size)
    return _flatten(canvas)

# ══════════════════════════════════════════════════════════════════
# LEVEL-UP CARD — dipakai di notifikasi level up otomatis
# ══════════════════════════════════════════════════════════════════

def render_levelup_card(avatar_bytes: bytes, username: str, old_level: int, new_level: int, is_premium: bool = False, role_names: list | None = None, background_bytes: Optional[bytes] = None, accent_colors: Optional[tuple] = None) -> io.BytesIO:
    W, H = 934, 282
    grad = list(accent_colors) if (is_premium and accent_colors) else None
    accent = grad[0] if grad else (GOLD if is_premium else CRIMSON)
    if grad:
        bg_colors = [_darken(c, 0.09 + i * (0.15 / max(len(grad) - 1, 1))) for i, c in enumerate(grad)]
        border    = _darken(grad[0], 0.5)
    else:
        bg_colors = [GOLD_BG_TOP, GOLD_BG_BTM] if is_premium else [(18, 4, 6), (48, 6, 10)]
        border    = GOLD_DARK if is_premium else DARK_RED

    custom_bg = cover_image(background_bytes, (W, H)) if (is_premium and background_bytes) else None
    if custom_bg is not None:
        card = custom_bg.convert("RGBA")
        card = Image.alpha_composite(card, Image.new("RGBA", (W, H), (5, 3, 4, 150)))
        side_scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(side_scrim).rectangle([0, 0, int(W * 0.55), H], fill=(5, 3, 4, 110))
        side_scrim = side_scrim.filter(ImageFilter.GaussianBlur(40))
        card = Image.alpha_composite(card, side_scrim)
    else:
        card = _vertical_gradient_multi((W, H), bg_colors).convert("RGBA")
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        cx, cy = 160, H // 2
        glow_color2 = grad[-1] if grad else accent
        for r, a in [(230, 26), (170, 40), (110, 60)]:
            gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*_lerp_color(accent, glow_color2, min(r / 230, 1)), a))
        card = Image.alpha_composite(card, glow)

    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle([2, 2, W - 3, H - 3], radius=22, outline=(*border, 255), width=3)

    av = _safe_avatar(avatar_bytes)
    avatar_d = 176
    avatar_ring = _circle_avatar(av, avatar_d, grad if grad else (*accent, 255), ring_width=7)
    ax, ay = 60, (H - avatar_ring.height) // 2
    if is_premium:
        aura = _fire_aura(avatar_d, ring_width=7, colors=grad)
        aura_pos = (int(ax + avatar_ring.width / 2 - aura.width / 2), int(ay + avatar_ring.height / 2 - aura.height / 2))
        card.paste(aura, aura_pos, aura)
    card.paste(avatar_ring, (ax, ay), avatar_ring)

    draw = ImageDraw.Draw(card)
    text_x = ax + avatar_ring.width + 50
    max_w  = W - text_x - 40

    f_tag = _font(F_BOLD, 26)
    # "LEVEL UP" alone for regular members; premium gets a small diamond +
    # "PREMIUM" instead of the old "· PREMIUM" suffix — cleaner two-part tag.
    if is_premium:
        draw.text((text_x, 46), "LEVEL UP", font=f_tag, fill=(*accent, 255))
        lvlup_w = draw.textlength("LEVEL UP   ", font=f_tag)
        _draw_diamond(draw, text_x + lvlup_w + 7, 46 + 18, 7, accent)
        if grad:
            card_canvas = card  # already RGBA
            _gradient_text(card_canvas, (text_x + lvlup_w + 20, 46), "PREMIUM", F_BOLD, 24, grad)
            draw = ImageDraw.Draw(card)
        else:
            draw.text((text_x + lvlup_w + 20, 46), "PREMIUM", font=_font(F_BOLD, 24), fill=GOLD)
    else:
        draw.text((text_x, 46), "LEVEL UP", font=f_tag, fill=(*accent, 255))

    # Level progression, e.g. "LEVEL 6  ➔  LEVEL 7" — auto-shrinks to fit
    prog_txt = f"LEVEL {old_level}  \u2192  LEVEL {new_level}"
    size = 64
    while text_width(draw, prog_txt, F_DISPLAY, size) > max_w and size > 30:
        size -= 2
    draw_text(draw, (text_x, 82), prog_txt, F_DISPLAY, size, WHITE)

    sub  = f"{username} reached a new level!"
    ssize = 30
    while text_width(draw, sub, F_BOLD, ssize) > max_w and ssize > 16:
        ssize -= 2
    draw_text(draw, (text_x, 190), sub, F_BOLD, ssize, MUTED)

    if role_names:
        role_txt = "\U0001F381 Unlocked: " + ", ".join(role_names)  # 🎁
        rsize = 22
        while text_width(draw, role_txt, F_BOLD, rsize) > max_w and rsize > 14:
            rsize -= 2
        draw_text(draw, (text_x, 226), role_txt, F_BOLD, rsize, accent if is_premium else GOLD)

    draw = ImageDraw.Draw(card)
    _watermark(draw, card.size)

    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf

def _watermark(draw: ImageDraw.ImageDraw, size):
    W, H = size
    wm_font = _font(F_BOLD, 16)
    watermark = "VALLENT EXS"
    wm_w = draw.textlength(watermark, font=wm_font)
    draw.text((W - wm_w - 24, H - 32), watermark, font=wm_font, fill=(120, 60, 60))

# ══════════════════════════════════════════════════════════════════
# LEADERBOARD CARD — dipakai di command `leaderboard` / `/leaderboard`
# ══════════════════════════════════════════════════════════════════

def _truncate(draw: ImageDraw.ImageDraw, text: str, path: str, size: int, max_w: int) -> str:
    if text_width(draw, text, path, size) <= max_w:
        return text
    while text and text_width(draw, text + "…", path, size) > max_w:
        text = text[:-1]
    return text + "…" if text else "…"

def render_leaderboard_card(guild_name: str, entries: list) -> io.BytesIO:
    """entries: list of dict {rank, avatar_bytes, name, level, xp} — urut dari #1,
    maksimal ditampilin 10 baris."""
    entries  = entries[:10]
    W        = 800
    row_h    = 66
    header_h = 100
    cut      = 44
    H = header_h + row_h * max(len(entries), 1) + 30

    canvas = _card_base(W, H, cut=cut, blood_xy=(-180, -180))
    draw   = ImageDraw.Draw(canvas)

    f_meta = _font(F_REG, 15)
    f_rank = _font(F_DISPLAY, 24)

    title = _truncate(draw, "XP LEADERBOARD", F_DISPLAY, 34, W - 64)
    draw_text(draw, (32, 24), title, F_DISPLAY, 34, WHITE)
    draw.rectangle([33, 62, 90, 65], fill=(*CRIMSON, 255))
    sub = _truncate(draw, guild_name.upper(), F_REG, 17, W - 64)
    draw_text(draw, (32, 72), sub, F_REG, 17, MUTED, bold=False)

    if not entries:
        f_empty = _font(F_REG, 22)
        draw.text((32, header_h + 10), "No XP data yet.", font=f_empty, fill=MUTED)

    rank_colors = {1: GOLD, 2: SILVER, 3: BRONZE}
    y = header_h
    name_max_w = W - 86 - 62 - 16 - 130
    for e in entries:
        rank   = e["rank"]
        accent = rank_colors.get(rank, CRIMSON)
        if rank <= 3:
            draw.rectangle([14, y + 3, W - 14, y + row_h - 3], fill=(*accent, 22))
            draw.rectangle([14, y + 3, 18, y + row_h - 3], fill=(*accent, 255))

        rank_str = f"#{rank}"
        rw = draw.textlength(rank_str, font=f_rank)
        draw.text((56 - rw / 2, y + row_h / 2 - 14), rank_str, font=f_rank, fill=accent if rank <= 3 else MUTED)

        av = _safe_avatar(e["avatar_bytes"])
        ring_color = accent if rank <= 3 else CRIMSON
        avatar_d = 46
        hexring  = _hex_avatar(av, avatar_d, ring_color, ring_width=3)
        ax = 86
        ay = y + (row_h - hexring.height) // 2
        canvas.paste(hexring, (ax, ay), hexring)

        name_x   = ax + hexring.width + 16
        name_txt = _truncate(draw, e["name"], F_BOLD, 21, name_max_w)
        draw_text(draw, (name_x, y + 9), name_txt, F_BOLD, 21, WHITE)
        draw.text((name_x, y + 35), f"LVL {e['level']}", font=f_meta, fill=MUTED)

        xp_str = f"{e['xp']:,} XP"
        xp_w = text_width(draw, xp_str, F_BOLD, 21)
        draw_text(draw, (W - 32 - xp_w - cut * 0.3, y + row_h / 2 - 11), xp_str, F_BOLD, 21, WHITE)

        y += row_h

    draw = ImageDraw.Draw(canvas)
    _watermark(draw, canvas.size)
    return _flatten(canvas)

# ══════════════════════════════════════════════════════════════════
# CAPTCHA — dipakai di sistem verifikasi member baru (join -> Unverified
# role -> selesein captcha -> Verified role). Gambar sengaja dibikin
# noisy/distorsi (rotasi per-huruf, garis coretan, speckle) biar gak
# gampang di-OCR bot, tapi tetep gampang dibaca manusia.
# ══════════════════════════════════════════════════════════════════

_CAPTCHA_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I — biar gak ambigu

def generate_captcha_code(length: int = 6) -> str:
    """Random alnum code, huruf ambigu (0/O/1/I/l) udah disingkirin dari pool."""
    return "".join(random.choice(_CAPTCHA_CHARS) for _ in range(length))

def render_captcha_image(code: str) -> io.BytesIO:
    """Render `code` jadi gambar captcha bergaya VALLENT EXS (dark red,
    grain texture), dibikin sengaja susah buat OCR bot: ukuran huruf acak,
    rotasi acak, sine-wave warp per karakter, spacing dirapetin/dibikin
    dikit overlap biar gak gampang di-segmentasi kolom-per-kolom, plus
    garis coretan yang nembus langsung tulisannya (bukan cuma nongkrong
    di background) — tapi tetep kebaca jelas sama mata manusia."""
    W, H = 340, 140
    img = _vertical_gradient((W, H), (26, 6, 8), (46, 10, 12)).convert("RGBA")
    img = Image.alpha_composite(img, _noise_texture((W, H), opacity=34))
    draw = ImageDraw.Draw(img)

    # garis coretan acak DI BELAKANG teks — beda warna/ketebalan tiap garis
    for _ in range(9):
        x1, y1 = random.randint(0, W), random.randint(0, H)
        x2, y2 = random.randint(0, W), random.randint(0, H)
        col = random.choice([CRIMSON, (150, 40, 50), (200, 160, 60)])
        draw.line([(x1, y1), (x2, y2)], fill=(*col, random.randint(70, 120)), width=random.randint(1, 3))

    n = max(len(code), 1)
    # spacing dirapetin (bahkan sengaja dikit overlap) — teknik standar
    # anti-OCR biar bot yang nyoba "potong per kolom lalu tebak 1 huruf per
    # potongan" gak bisa kerja bersih
    spacing = W / (n + 0.6)
    glyphs_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    for i, ch in enumerate(code):
        size = random.randint(44, 60)
        f = _font(F_DISPLAY, size)
        glyph = Image.new("RGBA", (96, 110), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glyph)
        gd.text((16, 14), ch, font=f, fill=(255, 255, 255, 255))

        angle = random.uniform(-30, 30)
        glyph = glyph.rotate(angle, resample=Image.BICUBIC, expand=True)

        # sine-wave warp per karakter: tiap kolom pixel digeser vertikal
        # dikit sesuai gelombang sinus acak — bikin bentuk huruf gak
        # "flat", jauh lebih ganggu buat template-matching OCR daripada
        # rotasi doang, tapi manusia masih gampang baca
        amp   = random.uniform(2, 5)
        freq  = random.uniform(0.08, 0.18)
        phase = random.uniform(0, math.tau)
        warped = Image.new("RGBA", glyph.size, (0, 0, 0, 0))
        for x in range(glyph.width):
            offset = int(amp * math.sin(freq * x + phase))
            warped.paste(glyph.crop((x, 0, x + 1, glyph.height)), (x, offset))
        glyph = warped

        px = int(spacing * (i + 0.8)) - glyph.width // 2 + random.randint(-4, 4)
        py = H // 2 - glyph.height // 2 + random.randint(-10, 10)
        glyphs_layer.alpha_composite(glyph, (px, py))

    img = Image.alpha_composite(img, glyphs_layer)
    draw = ImageDraw.Draw(img)

    # garis coretan DI ATAS teks juga, motong sebagian bentuk huruf — ini
    # yang paling efektif ngerusak akurasi OCR/segmentasi otomatis
    for _ in range(4):
        x1, y1 = random.randint(0, W), random.randint(int(H * 0.3), int(H * 0.7))
        x2, y2 = random.randint(0, W), random.randint(int(H * 0.3), int(H * 0.7))
        draw.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, 90), width=2)

    # speckle noise padat di lapisan paling atas
    for _ in range(220):
        x, y = random.randint(0, W - 1), random.randint(0, H - 1)
        draw.point((x, y), fill=(255, 255, 255, random.randint(20, 90)))

    out = Image.new("RGB", (W, H), (20, 4, 6))
    out.paste(img, (0, 0), img)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf
