"""
VALLENT EXS — Message Component Buttons (link + response)
=============================================================
Self-contained, same design as embed_links.py / ticket_types.py — never
imports anything from vallent.py. Pure data + validation; vallent.py
wires this into its own /component builder, persistence, and
interaction handling.

Concept
-------
A "message component" is a Container-based message (same visual shape as
/embed's output — title, description, thumbnail, banner) that can also
carry buttons of TWO different kinds:

- link     — opens a URL directly. Stateless: Discord handles it
             entirely client-side, no interaction ever reaches the bot,
             so it keeps working forever with zero bot involvement.
- response — when clicked, the bot shows THAT user an ephemeral message
             with its own title/description. This needs a live
             interaction handler, so any message containing a response
             button has to be persisted (vallent.py's own
             `message_components` config store) and registered as a
             persistent view so it survives restarts.

A draft's "buttons" key is a plain list of dicts:
  {"kind": "link", "label", "url", "emoji"}
  {"kind": "response", "label", "emoji", "style",
   "response_title", "response_description"}

None of this depends on any bot/cfg state — vallent.py owns the actual
Container/View construction, the persistent custom_id scheme, and
routing a click back to the right button's stored response text.
"""

import re
from typing import Optional

import discord

MAX_BUTTONS = 25  # Discord's hard cap across all buttons in one message
_URL_RE = re.compile(r"^https?://\S+$")

BUTTON_STYLE_CHOICES = {
    "primary":   discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success":   discord.ButtonStyle.success,
    "danger":    discord.ButtonStyle.danger,
}


def build_link_button(label: str, url: str, emoji: str = ""):
    """Validate + build a link-type button dict. Returns (dict_or_None,
    error_or_None) — used for BOTH adding a new link button and editing
    an existing one in place."""
    if not (label or "").strip():
        return None, "Label can't be empty."
    if not (url or "").strip() or not _URL_RE.match(url.strip()):
        return None, "URL must start with `http://` or `https://`."
    return {"kind": "link", "label": label.strip()[:80], "url": url.strip(), "emoji": (emoji or "").strip() or None}, None


def add_link_button(buttons: list, label: str, url: str, emoji: str = "") -> Optional[str]:
    """Append a link-type button spec to `buttons` IN PLACE. Returns an
    error string (leaving `buttons` untouched) if invalid or already at
    the cap, else None on success."""
    if len(buttons) >= MAX_BUTTONS:
        return f"This message already has the max {MAX_BUTTONS} buttons Discord allows."
    built, err = build_link_button(label, url, emoji)
    if err:
        return err
    buttons.append(built)
    return None


def edit_link_button(buttons: list, index: int, label: str, url: str, emoji: str = "") -> Optional[str]:
    """Replace the link-type button at `index` IN PLACE (keeps its
    position in the list). Returns an error string (leaving `buttons`
    untouched) if invalid or index out of range."""
    if not (0 <= index < len(buttons)):
        return "That button no longer exists — the list may have changed."
    built, err = build_link_button(label, url, emoji)
    if err:
        return err
    buttons[index] = built
    return None


_MAX_RESPONSE_TEXT = 4000  # Discord's own hard cap on a single modal TextInput / TextDisplay — can't go higher than this


def build_response_button(label: str, response_title: str, response_description: str, emoji: str = "",
                           style: str = "secondary", response_thumbnail: str = "", response_banner: str = ""):
    """Validate + build a response-type button dict. Returns (dict_or_None,
    error_or_None) — used for BOTH adding a new response button and
    editing an existing one in place (caller decides append vs replace),
    so validation only lives in one place."""
    if not (label or "").strip():
        return None, "Label can't be empty."
    if not (response_title or "").strip() and not (response_description or "").strip():
        return None, "Set at least a response title or description — the button needs something to show when clicked."
    for field_name, val in (("thumbnail", response_thumbnail), ("banner", response_banner)):
        if val and not val.strip().startswith(("http://", "https://")):
            return None, f"Response {field_name} must be a direct image URL (starting with http:// or https://)."
    return {
        "kind": "response", "label": label.strip()[:80], "emoji": (emoji or "").strip() or None,
        "style": style if style in BUTTON_STYLE_CHOICES else "secondary",
        "response_title": (response_title or "").strip()[:256],
        "response_description": (response_description or "").strip()[:_MAX_RESPONSE_TEXT],
        "response_thumbnail": (response_thumbnail or "").strip() or None,
        "response_banner": (response_banner or "").strip() or None,
    }, None


def add_response_button(buttons: list, label: str, response_title: str, response_description: str,
                         emoji: str = "", style: str = "secondary", response_thumbnail: str = "", response_banner: str = "") -> Optional[str]:
    """Append a response-type button spec to `buttons` IN PLACE. Returns
    an error string (leaving `buttons` untouched) if invalid or already
    at the cap, else None on success."""
    if len(buttons) >= MAX_BUTTONS:
        return f"This message already has the max {MAX_BUTTONS} buttons Discord allows."
    built, err = build_response_button(label, response_title, response_description, emoji, style, response_thumbnail, response_banner)
    if err:
        return err
    buttons.append(built)
    return None


def edit_response_button(buttons: list, index: int, label: str, response_title: str, response_description: str,
                          emoji: str = "", style: str = "secondary", response_thumbnail: str = "", response_banner: str = "") -> Optional[str]:
    """Replace the response-type button at `index` IN PLACE (keeps its
    position in the list, unlike remove+re-add). Returns an error string
    (leaving `buttons` untouched) if invalid or index out of range."""
    if not (0 <= index < len(buttons)):
        return "That button no longer exists — the list may have changed."
    built, err = build_response_button(label, response_title, response_description, emoji, style, response_thumbnail, response_banner)
    if err:
        return err
    buttons[index] = built
    return None


def remove_button(buttons: list, index: int) -> Optional[dict]:
    """Remove and return the button at `index` (0-based), or None if the
    index is out of range."""
    if 0 <= index < len(buttons):
        return buttons.pop(index)
    return None


def build_action_rows(buttons: list, component_id: Optional[str], callback) -> list:
    """Build the ActionRows to drop straight into a discord.ui.Container.
    `component_id` + `callback` are only needed for response-type buttons
    (their custom_id encodes component_id + button index so a click can
    be routed back to the right stored response; link buttons need
    neither — they're pure url-style, no bot interaction at all). Pass
    component_id=None / callback=None for a buttons list that's link-only.
    Returns an empty list for no buttons — callers should then build the
    Container with no ActionRow at all, so the message stays a plain
    text/image container, never an empty button row."""
    rows = []
    for i in range(0, min(len(buttons), MAX_BUTTONS), 5):
        row = discord.ui.ActionRow()
        for idx, btn in enumerate(buttons[i:i + 5], start=i):
            if btn["kind"] == "link":
                row.add_item(discord.ui.Button(
                    label=btn["label"][:80], url=btn["url"],
                    style=discord.ButtonStyle.link, emoji=btn.get("emoji") or None,
                ))
            else:
                item = discord.ui.Button(
                    label=btn["label"][:80],
                    style=BUTTON_STYLE_CHOICES.get(btn.get("style", "secondary"), discord.ButtonStyle.secondary),
                    emoji=btn.get("emoji") or None,
                    custom_id=f"vx_msgcomp:{component_id}:{idx}",
                )
                if callback:
                    item.callback = callback
                row.add_item(item)
        rows.append(row)
    return rows


def describe_button(btn: dict) -> str:
    """One-line human-readable summary for the Manage Buttons list."""
    if btn["kind"] == "link":
        return f"🔗 {btn['label']} — {btn['url']}"
    return f"💬 {btn['label']} — shows \"{btn.get('response_title') or btn.get('response_description', '')[:40]}\""
