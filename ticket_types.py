"""
VALLENT EXS — Ticket Types (multi-category dropdown support)
==============================================================
Split out of vallent.py on purpose so that file doesn't keep growing —
this module is self-contained (same design as antinuke.py): it never
imports anything from vallent.py. Callers pass in whatever guild/panel
data it needs and get back plain data (dicts, discord.SelectOption lists)
that vallent.py wires into its own embeds/views/config saving.

Concept
-------
A ticket panel can now optionally have multiple "types" under one
dropdown — e.g. "Report a Bug" -> one category, "Billing" -> a totally
different category/log channel/support role. Each type is stored under
panel["types"][type_key] = {label, emoji, description, category,
log_channel, support_role, max_tickets}.

Panels that never configure any types keep working exactly like before
(single category, single button or single-option dropdown) — nothing
here is a breaking change, `get_type_config` transparently falls back to
the panel's own top-level category/log_channel/support_role/max_tickets
when no types dict (or an empty one) is present.
"""

import re
from typing import Optional

import discord

MAX_TYPES_PER_PANEL = 25  # Discord's own hard cap on select menu options

# A full custom emoji tag, e.g. <:name:123456789012345678> or <a:name:...>
_CUSTOM_EMOJI_RE = re.compile(r"^<(a?):([a-zA-Z0-9_]{2,32}):(\d+)>$")
# Discord's own `:name:` shortcode, optionally with a `~2` disambiguator
# suffix — this is what several Discord clients (notably mobile) paste
# into a plain text input when you pick a custom emoji from the emoji
# picker, instead of the full <:name:id> tag. It has no ID attached, so
# it has to be resolved against the guild's own emoji list by name.
_EMOJI_SHORTCODE_RE = re.compile(r"^:([a-zA-Z0-9_]{2,32})(?:~\d+)?:$")


def resolve_emoji_input(guild: Optional[discord.Guild], raw: str):
    """Normalize a user-typed emoji into something Discord's component API
    will actually accept. One invalid emoji rejects the ENTIRE message
    (400 Invalid Form Body), so anything unresolvable is dropped (None)
    with a warning instead of being passed through and blowing up the
    whole panel post/edit.

    Handles three shapes:
    - Full custom tag `<:name:id>` / `<a:name:id>` — already valid, used as-is.
    - Discord's `:name:` (or `:name~2:`) shortcode — looked up against the
      server's own emoji list by name.
    - A short plain unicode emoji — used as-is.

    Returns (resolved_emoji_or_None, warning_or_None).
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if _CUSTOM_EMOJI_RE.match(raw):
        return raw, None
    m = _EMOJI_SHORTCODE_RE.match(raw)
    if m:
        name = m.group(1)
        found = discord.utils.get(guild.emojis, name=name) if guild else None
        if found:
            return str(found), None
        return None, f"couldn't find a server emoji named `:{name}:` — using the default emoji instead"
    # Anything short that isn't wrapped in `:` or `<` is trusted as a
    # plain unicode emoji (Discord itself will reject it if it's wrong,
    # but that's a much narrower failure than a malformed custom tag).
    if len(raw) <= 8 and not raw.startswith(":") and not raw.startswith("<"):
        return raw, None
    return None, "that doesn't look like a valid emoji — using the default emoji instead"


def safe_emoji(raw: Optional[str]) -> Optional[str]:
    """Defense-in-depth sanity check for emoji strings already stored in
    config (e.g. saved before this validation existed, or edited by hand
    in the JSON). Never raises — just returns None for anything that
    isn't a full custom emoji tag or a short plain-unicode string, so one
    bad legacy value can never take down an otherwise-valid panel."""
    if not raw:
        return None
    if _CUSTOM_EMOJI_RE.match(raw):
        return raw
    if len(raw) <= 8 and not raw.startswith(":") and not raw.startswith("<"):
        return raw
    return None


def safe_emoji_for_guild(guild: Optional[discord.Guild], raw: Optional[str]) -> Optional[str]:
    """Same as safe_emoji(), but ALSO confirms a custom emoji tag
    (`<:name:id>` / `<a:name:id>`) still actually exists in `guild` before
    trusting it. A syntactically-valid tag whose emoji was later deleted
    from the server (or copy-pasted from a config edit / a different
    server) passes safe_emoji()'s regex fine but gets rejected by
    Discord's API with a 400 Invalid Form Body — and because that happens
    while building a whole Select's options list, ONE bad emoji takes
    down the entire dropdown with it. This is the version to use anywhere
    a stored emoji ends up inside a discord.ui.Select/SelectOption, so a
    stale emoji degrades to "no emoji shown" instead of a broken menu."""
    resolved = safe_emoji(raw)
    if not resolved or not guild:
        return resolved
    m = _CUSTOM_EMOJI_RE.match(resolved)
    if not m:
        return resolved  # plain unicode emoji — nothing to verify
    emoji_id = int(m.group(3))
    return resolved if discord.utils.get(guild.emojis, id=emoji_id) else None


def slugify_type_id(label: str, existing: dict) -> str:
    """Turn a type's label into a short, stable dict key (e.g. 'Billing
    Support' -> 'billing_support'). Uniquified against keys already used
    in this panel, same approach as custom badge IDs — so two similarly
    named types never collide or silently overwrite each other."""
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "type"
    key = base
    n = 2
    while key in existing:
        key = f"{base}_{n}"
        n += 1
    return key


def get_type_config(panel: dict, type_key: Optional[str]) -> dict:
    """Resolve the effective category/log/role/max/label for an open-ticket
    action. If the panel has a matching type configured, its own settings
    win (falling back to the panel's top-level values for anything it
    doesn't override); otherwise this is a classic single-category panel
    and the top-level values are used directly."""
    types = panel.get("types") or {}
    if type_key and type_key in types:
        t = types[type_key]
        return {
            "category":     t.get("category") or panel.get("category"),
            "log_channel":  t.get("log_channel") or panel.get("log_channel"),
            "support_role": t.get("support_role") or panel.get("support_role"),
            "max_tickets":  t.get("max_tickets") or panel.get("max_tickets", 1),
            "label":        t.get("label") or type_key,
        }
    return {
        "category":     panel.get("category"),
        "log_channel":  panel.get("log_channel"),
        "support_role": panel.get("support_role"),
        "max_tickets":  panel.get("max_tickets", 1),
        "label":        panel.get("title") or "Ticket",
    }


def build_type_select_options(panel: dict, guild: Optional[discord.Guild] = None) -> list:
    """discord.SelectOption list from a panel's configured types. Returns
    an empty list if the panel has no multi-type config (0 or 1 types
    isn't worth a "pick your type" dropdown) — caller should fall back to
    its existing single button/select behavior in that case.

    Pass `guild` when it's available (e.g. from an interaction) so any
    custom emoji that's been deleted since it was configured gets quietly
    dropped instead of causing Discord to reject the whole dropdown with
    a 400 Invalid Form Body. Safe to omit `guild` (e.g. building this at
    process startup for a persistent view with no guild context yet) —
    it just falls back to the non-guild-checked safe_emoji()."""
    types = panel.get("types") or {}
    if len(types) < 2:
        return []
    options = []
    for key, t in types.items():
        emoji = safe_emoji_for_guild(guild, t.get("emoji")) if guild else safe_emoji(t.get("emoji"))
        options.append(discord.SelectOption(
            label=(t.get("label") or key)[:100],
            value=key,
            description=(t.get("description") or "")[:100] or None,
            emoji=emoji or None,
        ))
    return options[:MAX_TYPES_PER_PANEL]


def format_type_list(panel: dict, resolve_channel, resolve_role) -> str:
    """Human-readable summary of a panel's configured types, for the
    `/tickettype list` command. `resolve_channel`/`resolve_role` are
    passed in (e.g. guild.get_channel / guild.get_role) so this module
    never needs its own guild/bot reference."""
    types = panel.get("types") or {}
    if not types:
        return "No ticket types configured — this panel uses its single top-level category."
    lines = []
    for key, t in types.items():
        cat  = resolve_channel(t.get("category")) if t.get("category") else None
        log  = resolve_channel(t.get("log_channel")) if t.get("log_channel") else None
        role = resolve_role(t.get("support_role")) if t.get("support_role") else None
        emoji = (t.get("emoji") + " ") if t.get("emoji") else ""
        lines.append(
            f"{emoji}**{t.get('label', key)}** — `{key}`\n"
            f"　Category: {cat.mention if cat else '*(falls back to panel default)*'} · "
            f"Log: {log.mention if log else '*(falls back to panel default)*'} · "
            f"Role: {role.mention if role else '*(none)*'}"
        )
    return "\n".join(lines)
