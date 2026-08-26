"""
VALLENT EXS — Profile ID Card Renderer
==========================================
Renders the `profile` / `/profile` command's output as a proper ID-card
style image (member no., username, hierarchy, premium status, badges,
avatar photo, join dates) instead of a plain text embed — full replacement,
not a supplement.

Deliberately its OWN file, separate from both vallent.py and rank_card.py:
vallent.py stays the orchestrator (fetch data, fetch badge icons, call this),
rank_card.py stays scoped to the rank/level-up cards. This file only knows
how to turn already-fetched data into pixels — it never touches bot/cfg
state directly, same self-contained pattern as antinuke.py / ticket_types.py
etc. All the shared low-level primitives (font fallback chain, gradient
helpers, avatar safety net, background cover-crop) are imported from
rank_card.py instead of duplicated — one font/gradient engine for every
card this bot renders.
"""

import io
import math
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter

from rank_card import (
    F_DISPLAY, F_BOLD, F_REG,
    WHITE, MUTED, GOLD, CRIMSON,
    _font, draw_text, text_width,
    _vertical_gradient, _horizontal_gradient,
    _vertical_gradient_multi, _horizontal_gradient_multi, _lerp_multi,
    _lerp_color, _darken, _lighten, _gradient_text,
    _rounded_mask, _noise_texture, _safe_avatar, cover_image, _flatten,
    _draw_diamond,
)

W, H = 1536, 1024

# Default (non-custom-gradient) palette — same crimson family as every
# other card, so a free-tier ID card still visibly belongs to this bot.
ACCENT_DEFAULT = CRIMSON
BG_DEFAULT_TOP = (12, 7, 8)
BG_DEFAULT_BTM = (24, 8, 10)
BORDER_DEFAULT = (60, 14, 18)

MAX_BADGE_ICONS = 7   # beyond this, collapse the rest into a "+N" chip


# ══════════════════════════════════════════════════════════════════
# SMALL DRAWN ICONS — kept as vector shapes (not fetched images) so the
# card never depends on a third-party icon CDN being reachable.
# ══════════════════════════════════════════════════════════════════

def _ribbon(accent) -> Image.Image:
    """Folded-flag logo tab for the top-left corner — a rectangle with a
    triangular notch cut into the bottom, same silhouette language as a
    classic ID-card corner ribbon. `accent` is a plain (r,g,b) or a list
    of 2-3 (r,g,b) gradient stops."""
    w, h = 150, 250
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pts = [(0, 0), (w, 0), (w, h), (w // 2, h - 46), (0, h)]
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    if isinstance(accent[0], (tuple, list)):
        fill = _vertical_gradient_multi((w, h), list(accent)).convert("RGBA")
    else:
        fill = Image.new("RGBA", (w, h), (*accent, 255))
    fill.putalpha(mask)
    layer = Image.alpha_composite(layer, fill)
    d = ImageDraw.Draw(layer)
    f = _font(F_DISPLAY, 92)
    tw = d.textlength("V", font=f)
    d.text(((w - tw) / 2 - 2, 46), "V", font=f, fill=(255, 255, 255, 235))
    return layer


def _calendar_icon(size: int, color) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    pad = size * 0.1
    top = size * 0.22
    d.rounded_rectangle([pad, top, size - pad, size - pad], radius=size * 0.12, outline=color, width=max(2, size // 12))
    d.line([pad, top + size * 0.16, size - pad, top + size * 0.16], fill=color, width=max(2, size // 14))
    for fx in (pad + size * 0.14, size - pad - size * 0.14):
        d.line([fx, top - size * 0.08, fx, top + size * 0.1], fill=color, width=max(2, size // 12))
    return layer


def _shield_icon(size: int, color) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    pts = [
        (size * 0.5, size * 0.04), (size * 0.92, size * 0.2), (size * 0.92, size * 0.5),
        (size * 0.5, size * 0.97), (size * 0.08, size * 0.5), (size * 0.08, size * 0.2),
    ]
    d.polygon(pts, outline=color, width=max(2, size // 14))
    r = size * 0.16
    cx, cy = size * 0.5, size * 0.44
    star = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.42
        star.append((cx + math.cos(ang) * rad, cy + math.sin(ang) * rad))
    d.polygon(star, fill=color)
    return layer


# ══════════════════════════════════════════════════════════════════
# LAYOUT PRIMITIVES
# ══════════════════════════════════════════════════════════════════

def _info_row(canvas: Image.Image, x: int, y: int, w: int, label: str, value: str,
              value_color=WHITE, badge_icons: Optional[list] = None, diamond_color=None) -> None:
    """One 'LABEL : value' row. If `badge_icons` is given, a row of small
    icon chips is pasted instead of the plain text value — used for the
    BADGES row so hierarchy/premium/custom badges show as real icons.
    `diamond_color` draws a small diamond bullet before the value (used
    for the Premium row when active) instead of plain text alignment."""
    draw = ImageDraw.Draw(canvas)
    draw.text((x, y), label.upper(), font=_font(F_BOLD, 22), fill=MUTED)
    colon_x = x + 270
    draw.text((colon_x, y - 2), ":", font=_font(F_BOLD, 28), fill=(90, 90, 95))
    value_x = colon_x + 40
    if diamond_color:
        _draw_diamond(draw, value_x + 8, y + 14, 8, diamond_color)
        value_x += 26

    if badge_icons:
        bx = value_x
        chip = 48
        gap = 10
        shown = badge_icons[:MAX_BADGE_ICONS]
        overflow = len(badge_icons) - len(shown)
        for icon in shown:
            chip_bg = Image.new("RGBA", (chip, chip), (0, 0, 0, 0))
            ImageDraw.Draw(chip_bg).rounded_rectangle([0, 0, chip - 1, chip - 1], radius=12, fill=(255, 255, 255, 18))
            canvas.paste(chip_bg, (bx, y - 8), chip_bg)
            if icon["kind"] == "image" and icon.get("img") is not None:
                img = icon["img"].convert("RGBA").resize((chip - 12, chip - 12), Image.LANCZOS)
                canvas.paste(img, (bx + 6, y - 8 + 6), img)
            else:
                d2 = ImageDraw.Draw(canvas)
                ch = icon.get("char", "\u2022")
                cw = text_width(d2, ch, F_REG, 24, bold=False)
                draw_text(d2, (bx + (chip - cw) / 2, y - 8 + (chip - 30) / 2), ch, F_REG, 24, icon.get("color", WHITE), bold=False)
            bx += chip + gap
        if overflow > 0:
            d3 = ImageDraw.Draw(canvas)
            d3.rounded_rectangle([bx, y - 8, bx + chip, y - 8 + chip], radius=12, fill=(255, 255, 255, 14))
            txt = f"+{overflow}"
            tw = text_width(d3, txt, F_BOLD, 20)
            d3.text((bx + (chip - tw) / 2, y - 8 + (chip - 22) / 2), txt, font=_font(F_BOLD, 20), fill=MUTED)
        if not shown:
            draw.text((value_x, y - 2), "No badges yet", font=_font(F_BOLD, 26), fill=MUTED)
    else:
        size = 32
        while text_width(draw, value, F_BOLD, size) > (x + w - value_x) and size > 16:
            size -= 2
        draw.text((value_x, y - 4), value, font=_font(F_BOLD, size), fill=value_color)


def _divider(canvas: Image.Image, x: int, y: int, w: int) -> None:
    ImageDraw.Draw(canvas).line([x, y, x + w, y], fill=(255, 255, 255, 22), width=2)


# ══════════════════════════════════════════════════════════════════
# MAIN RENDER
# ══════════════════════════════════════════════════════════════════

def render_profile_card(
    avatar_bytes: bytes,
    username: str,
    member_no: int,
    hierarchy_label: str,
    hierarchy_color: tuple,
    premium_text: str,
    is_premium: bool,
    badge_icons: list,
    joined_str: str,
    created_str: str,
    level: int,
    background_bytes: Optional[bytes] = None,
    accent_colors: Optional[tuple] = None,
) -> io.BytesIO:
    grad   = list(accent_colors) if (is_premium and accent_colors) else None
    accent = grad[0] if grad else (GOLD if is_premium else ACCENT_DEFAULT)
    accent2 = grad[-1] if grad else accent
    border = _darken(grad[0], 0.55) if grad else ((110, 80, 8) if is_premium else BORDER_DEFAULT)

    # ── Background ───────────────────────────────────────────────
    custom_bg = cover_image(background_bytes, (W, H)) if (is_premium and background_bytes) else None
    if custom_bg is not None:
        canvas = custom_bg.convert("RGBA")
        canvas = Image.alpha_composite(canvas, Image.new("RGBA", (W, H), (5, 3, 4, 160)))
        side_scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(side_scrim).rectangle([0, 0, int(W * 0.66), H], fill=(5, 3, 4, 120))
        side_scrim = side_scrim.filter(ImageFilter.GaussianBlur(50))
        canvas = Image.alpha_composite(canvas, side_scrim)
    elif grad:
        bg_colors = [_darken(c, 0.07 + i * (0.11 / max(len(grad) - 1, 1))) for i, c in enumerate(grad)]
        canvas = _vertical_gradient_multi((W, H), bg_colors).convert("RGBA")
    else:
        canvas = _vertical_gradient((W, H), BG_DEFAULT_TOP, BG_DEFAULT_BTM).convert("RGBA")

    canvas = Image.alpha_composite(canvas, _noise_texture((W, H), opacity=6))

    # Giant faint wordmark bleeding across the middle — same branding
    # fingerprint idea as the rank card's "VX" watermark, sized for this
    # much bigger canvas.
    wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    wf = _font(F_DISPLAY, int(H * 0.85))
    wd = ImageDraw.Draw(wm)
    wtxt = "VX"
    tw = wd.textlength(wtxt, font=wf)
    wd.text((W * 0.42 - tw * 0.5, -H * 0.12), wtxt, font=wf, fill=(255, 255, 255, 10))
    canvas = Image.alpha_composite(canvas, wm.rotate(-6, resample=Image.BICUBIC))

    # Outer border + rounded silhouette clip
    outer_mask = _rounded_mask((W, H), 34)
    framed = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    framed.paste(canvas, (0, 0), outer_mask)
    canvas = framed
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([2, 2, W - 3, H - 3], radius=34, outline=(255, 255, 255, 40), width=2)

    # ── Header: ribbon + title + subtitle ───────────────────────
    ribbon = _ribbon(grad if grad else accent)
    canvas.paste(ribbon, (0, 0), ribbon)

    title_x = 210
    draw.text((title_x, 62), "VALLENT", font=_font(F_DISPLAY, 66), fill=WHITE)
    name_w = draw.textlength("VALLENT ", font=_font(F_DISPLAY, 66))
    if grad:
        _gradient_text(canvas, (title_x + name_w, 62), "ID CARD", F_DISPLAY, 66, grad)
        draw = ImageDraw.Draw(canvas)
    else:
        draw.text((title_x + name_w, 62), "ID CARD", font=_font(F_DISPLAY, 66), fill=accent)

    sub = "OFFICIAL MEMBER PROFILE"
    draw.text((title_x, 150), sub, font=_font(F_REG, 24), fill=MUTED)
    draw.rectangle([title_x, 196, title_x + 60, 200], fill=(*accent, 255))

    tag = "VALLENT EXS  |"
    tag_w = draw.textlength(tag, font=_font(F_REG, 22))
    draw.text((W - 60 - tag_w, 70), tag, font=_font(F_REG, 22), fill=MUTED)

    # ── Left info column ─────────────────────────────────────────
    rows_x, rows_w = 110, 800
    y = 300
    row_gap = 92
    _info_row(canvas, rows_x, y, rows_w, "Member No", f"#{member_no:04d}")
    draw = ImageDraw.Draw(canvas)
    _divider(canvas, rows_x, y + 46, rows_w)
    y += row_gap
    _info_row(canvas, rows_x, y, rows_w, "Username", username)
    _divider(canvas, rows_x, y + 46, rows_w)
    y += row_gap
    _info_row(canvas, rows_x, y, rows_w, "Hierarchy", hierarchy_label, value_color=hierarchy_color)
    _divider(canvas, rows_x, y + 46, rows_w)
    y += row_gap
    _info_row(canvas, rows_x, y, rows_w, "Premium", premium_text, value_color=(accent if is_premium else MUTED),
              diamond_color=accent if is_premium else None)
    _divider(canvas, rows_x, y + 46, rows_w)
    y += row_gap
    _info_row(canvas, rows_x, y, rows_w, "Badges", "", badge_icons=badge_icons)

    # ── Right side: avatar photo + level badge + join dates ─────
    photo_size = 420
    px, py = 1010, 300
    av = _safe_avatar(avatar_bytes)
    photo = cover_image_from_pil(av, (photo_size, photo_size))
    pmask = _rounded_mask((photo_size, photo_size), 26)
    photo_final = Image.new("RGBA", (photo_size, photo_size), (0, 0, 0, 0))
    photo_final.paste(photo, (0, 0), pmask)
    canvas.paste(photo_final, (px, py), photo_final)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([px, py, px + photo_size, py + photo_size], radius=26, outline=(*accent, 255), width=5)

    # Overlapping "LEVEL" badge circle, bottom-right of the photo
    r = 78
    ccx, ccy = px + photo_size, py + photo_size
    circle = Image.new("RGBA", (r * 2, r * 2), (0, 0, 0, 0))
    cd = ImageDraw.Draw(circle)
    cd.ellipse([0, 0, r * 2 - 1, r * 2 - 1], fill=(10, 6, 7, 255), outline=(*accent, 255), width=6)
    lvl_lbl = "LEVEL"
    lw = cd.textlength(lvl_lbl, font=_font(F_BOLD, 20))
    cd.text((r - lw / 2, r - 46), lvl_lbl, font=_font(F_BOLD, 20), fill=MUTED)
    lvl_txt = str(level)
    lsize = 52
    lf = _font(F_DISPLAY, lsize)
    lvw = cd.textlength(lvl_txt, font=lf)
    cd.text((r - lvw / 2, r - 16), lvl_txt, font=lf, fill=WHITE)
    canvas.paste(circle, (int(ccx - r), int(ccy - r)), circle)

    # Join-date rows below the photo
    draw = ImageDraw.Draw(canvas)
    icon_sz = 44
    dy = py + photo_size + 30
    cal1 = _calendar_icon(icon_sz, (*accent, 255))
    canvas.paste(cal1, (px, dy), cal1)
    draw.text((px + icon_sz + 16, dy - 2), "JOINED SERVER", font=_font(F_BOLD, 20), fill=MUTED)
    draw.text((px + icon_sz + 16, dy + 24), joined_str, font=_font(F_BOLD, 28), fill=WHITE)

    dy2 = dy + 88
    cal2 = _calendar_icon(icon_sz, (*accent, 255))
    canvas.paste(cal2, (px, dy2), cal2)
    draw.text((px + icon_sz + 16, dy2 - 2), "DISCORD SINCE", font=_font(F_BOLD, 20), fill=MUTED)
    draw.text((px + icon_sz + 16, dy2 + 24), created_str, font=_font(F_BOLD, 28), fill=WHITE)

    # ── Footer ────────────────────────────────────────────────────
    shield = _shield_icon(56, (*accent, 255))
    canvas.paste(shield, (rows_x, H - 92), shield)
    draw = ImageDraw.Draw(canvas)
    draw.text((rows_x + 78, H - 78), "VALLENT EXS", font=_font(F_REG, 24), fill=MUTED)

    return _flatten(canvas)


def cover_image_from_pil(img: Image.Image, size) -> Image.Image:
    """Same crop-to-cover logic as rank_card.cover_image(), but for an
    already-decoded PIL image (the avatar) instead of raw bytes — used so
    a non-square Discord avatar still fully fills the square photo frame
    instead of stretching."""
    w, h = size
    img = img.convert("RGBA")
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new_w, new_h = math.ceil(src_w * scale), math.ceil(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))
