"""
VALLENT EXS — Discord Moderation Bot
Author  : Niks. (Founder)
Version : 1.2.0

"No mercy. No limits. Full control."

Features:
  - Full moderation suite (kick, ban, timeout, warn, purge, lock, slowmode, etc.)
  - No-prefix command system (owner + premium users)
  - Bot role hierarchy: Founder > Developer > Management > Staff
  - Profile card with badges
  - XP leveling + rank card (image)
  - Giveaway system with winner role auto-assign
  - Ticket system
  - Honeypot anti-spam channel (auto-ban)
  - Premium system with expiry
  - Multi-language support
  - Owner supreme — overrides all permission checks
"""

import discord
import aiohttp
import io
import traceback
import sys
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import re
import asyncio
import time
import datetime
import logging
import pytz
from collections import defaultdict
from typing import Optional, Union
from types import SimpleNamespace

logging.basicConfig(level=logging.INFO)

from emoji_config import (
    BADGE_FOUNDER, BADGE_DEVELOPER, BADGE_MANAGEMENT, BADGE_STAFF,
    BADGE_PREMIUM, BADGE_NOPREFIX, BADGE_USER, BADGE_MODERATOR, BADGE_SERVER_MANAGER,
    BADGE_MOONKEEPER,
    ICON_MODERATION, ICON_ROLE, ICON_INFO, ICON_TICKET, ICON_LEVEL,
    ICON_GIVEAWAY, ICON_ANTISPAM, ICON_OWNER,
    ICON_SUCCESS, ICON_ERROR, ICON_WARNING, ICON_LOADING,
    ICON_PROFILE, ICON_BADGES, ICON_COMMANDS, ICON_PREMIUM_TAG,
    ICON_TICKET_OPEN, ICON_TICKET_CLOSE, ICON_GIVEAWAY_REACT, ICON_GIVEAWAY_PARTICIPANTS, ICON_WINNER,
    ICON_BOOST, ICON_ANTINUKE, ICON_IGNORE, ICON_AUTOMOD, ICON_AUTORESPONSE,
    ICON_AFK, ICON_VERIFICATION,
    ICON_STATUS_ONLINE, ICON_STATUS_OFFLINE, ICON_STATUS_MAINTENANCE,
    ICON_STATUS_UPDATE, ICON_STATUS_DEGRADED,
    ICON_EMBED, ICON_EMBED_SEND, ICON_COMPONENT,
    e
)
import rank_card
import profile_card
import dashboard
from PIL import Image
import antinuke
import ticket_types
import vote_system
import embed_links
import message_components
from aiohttp import web as aiohttp_web


# ══════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════

BOT_NAME      = "VALLENT EXS"
BOT_TAGLINE   = "by Nikoliesamphink."
BOT_VERSION   = "1.2.0"
BOT_BANNER_URL: Optional[str] = None  # populated once in on_ready() from the bot account's Discord banner, if it has one
BOT_PREFIX    = "!vx "
CONFIG_PATH   = "data/config.json"
WIB           = pytz.timezone("Asia/Jakarta")

# Dark red palette
COLOR_PRIMARY = 0x8B0000   # Dark red
COLOR_SUCCESS = 0x22C55E   # Green
COLOR_ERROR   = 0xEF4444   # Red
COLOR_WARNING = 0xF59E0B   # Amber
COLOR_INFO    = 0xDC143C   # Crimson

# Support server invite link — set via env var SUPPORT_INVITE
SUPPORT_INVITE = os.getenv("SUPPORT_INVITE", "")
# top.gg vote integration — all optional. Without TOPGG_WEBHOOK_AUTH set,
# the webhook server simply never starts (no port opened, no crash) and
# /vote or !vote will just tell people voting isn't configured yet.
TOPGG_VOTE_URL      = os.getenv("TOPGG_VOTE_URL", "")        # e.g. https://top.gg/bot/<id>/vote
TOPGG_WEBHOOK_AUTH   = os.getenv("TOPGG_WEBHOOK_AUTH", "")    # secret string, must match top.gg's Webhooks tab
# Railway auto-injects PORT when the service has Public Networking turned
# on — that's the port your app MUST bind to for Railway's public domain
# to actually reach it. WEB_PORT is only a manual fallback for
# non-Railway hosts; on Railway, PORT always wins. Shared by the top.gg
# webhook AND the dashboard below — one process, one port, one app.
WEB_PORT             = int(os.getenv("PORT") or os.getenv("VOTE_WEBHOOK_PORT", "8080"))

# Web dashboard — all optional. Without DISCORD_CLIENT_SECRET set, the
# dashboard routes simply never get registered (no login link works, but
# nothing crashes and the vote webhook still runs fine on its own).
DASHBOARD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DASHBOARD_REDIRECT_URI  = os.getenv("DASHBOARD_REDIRECT_URI", "")   # e.g. https://your-domain/auth/discord/callback
DASHBOARD_SESSION_SECRET = os.getenv("SESSION_SECRET", "")          # any long random string, signs the login cookie

SPAM_THRESHOLD = 3
SPAM_WINDOW    = 8.0

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════

def load_config() -> dict:
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        default = {
            "guilds":            {},
            "premium_users":     [],
            "premium_guilds":    [],
            "premium_commands":  [],
            "premium_expiry":    {},
            "no_prefix_users":   [],
            "no_prefix_guilds":  [],
            "no_prefix_expiry":  {},
            "bot_roles":         {},
            "role_sync":         {},
            "custom_badges":     {},
            "user_custom_badges": {},
            "premium_backgrounds": {},   # uid(str) -> image URL, custom rank card background (premium-only)
            "premium_colors": {},        # uid(str) -> [hex1, hex2], custom 2-color gradient accent (premium-only)
            "profile_ids": {},           # uid(str) -> int, sequential "member no." shown on the ID card, assigned once
            "profile_id_counter": 0,     # last-assigned profile_ids number
            "profile_backgrounds": {},   # uid(str) -> image URL, custom ID card background (premium-only, separate from premium_backgrounds)
            "profile_colors": {},        # uid(str) -> [hex1, hex2], custom ID card gradient (premium-only, separate from premium_colors)
            "active_giveaways": {},      # message_id(str) -> giveaway dict, so a giveaway survives a bot restart
            "giveaway_history": {},      # message_id(str) -> ended giveaway dict (capped), so `giveaway reroll` still works after it's over
            "error_log_channel_id": None,  # channel where unexpected errors get auto-reported
            "status_channel_id": None,
            "votes":             {},
            "payment_methods": {
                "qris":    {"enabled": True, "image_url": "", "info": ""},
                "bank":    {"enabled": True, "bank_name": "", "account_number": "", "account_name": ""},
                "ewallet": {"enabled": True, "type": "", "number": ""},
            }
        }
        save_config(default)
        return default
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("guilds",           {})
    data.setdefault("premium_users",    [])
    data.setdefault("premium_guilds",   [])
    data.setdefault("premium_commands", [])
    data.setdefault("premium_expiry",   {})
    data.setdefault("no_prefix_users",  [])
    data.setdefault("no_prefix_guilds", [])
    data.setdefault("no_prefix_expiry", {})
    data.setdefault("bot_roles",        {})
    data.setdefault("custom_badges",      {})   # badge_id -> {"name": str, "emoji": str} — owner-defined, free-form badges
    data.setdefault("user_custom_badges", {})   # uid(str) -> [badge_id, ...] — which custom badges each user holds
    data.setdefault("premium_backgrounds", {})  # uid(str) -> image URL, custom rank card background (premium-only)
    data.setdefault("premium_colors", {})       # uid(str) -> [hex1, hex2], custom 2-color gradient accent (premium-only)
    data.setdefault("profile_ids", {})          # uid(str) -> int, sequential "member no." shown on the ID card, assigned once
    data.setdefault("profile_id_counter", 0)    # last-assigned profile_ids number
    data.setdefault("profile_backgrounds", {})  # uid(str) -> image URL, custom ID card background (premium-only, separate from premium_backgrounds)
    data.setdefault("profile_colors", {})       # uid(str) -> [hex1, hex2], custom ID card gradient (premium-only, separate from premium_colors)
    data.setdefault("active_giveaways", {})     # message_id(str) -> giveaway dict, so a giveaway survives a bot restart
    data.setdefault("giveaway_history", {})     # message_id(str) -> ended giveaway dict (capped), so `giveaway reroll` still works after it's over
    data.setdefault("error_log_channel_id", None)  # channel where unexpected errors get auto-reported
    data.setdefault("moonkeeper_users",     [])   # uid list — manual Moonkeeper grants (independent of bot_roles hierarchy)
    data.setdefault("moonkeeper_sync_role", None)  # single Discord role ID synced to Moonkeeper, if any
    data.setdefault("role_sync",        {})
    data.setdefault("status_channel_id", None)  # channel where `botstatus` posts online/maintenance/update/offline updates
    data.setdefault("votes",            {})
    data.setdefault("support_server_members", [])  # user IDs who have joined the support server
    data.setdefault("commands_run",           {})  # uid -> number of commands run
    data.setdefault("xp_boost",                {})  # uid(str) -> {"expiry": iso, "multiplier": float}
    data.setdefault("join_boost_last_grant",    {})  # uid(str) -> iso timestamp of last support-server-join XP boost grant (anti leave/rejoin farm)
    data.setdefault("maintenance", {"enabled": False, "reason": "", "since": None})
    for gid, gc in data.get("guilds", {}).items():
        _init_guild(gc)
    save_config(data)
    return data

def _init_guild(gc: dict):
    gc.setdefault("main_channel",      None)
    gc.setdefault("announce_channel",  None)
    gc.setdefault("level_channel",     None)
    gc.setdefault("levelup_message",   "{mention} just leveled up to **Level {level}**! Keep chatting in {server} to climb even higher. {roles}")
    # Migration: kalau nilai yang kesimpen masih PERSIS default lama (artinya
    # user belum pernah custom manual), upgrade otomatis ke default baru.
    if gc.get("levelup_message") == "{mention} leveled up to **Level {level}**!":
        gc["levelup_message"] = "{mention} just leveled up to **Level {level}**! Keep chatting in {server} to climb even higher. {roles}"
    gc.setdefault("antispam", {
        "trap_channel": None,
        "log_channel":  None,
        "ignore_users": [],
        "ignore_roles": [],
        "threshold":    SPAM_THRESHOLD,
        "window":       SPAM_WINDOW,
        "flood_count":  5,
        "flood_window": 4,
        "punishment":   "ban",
    })
    gc["antispam"].setdefault("trap_channel", None)
    gc["antispam"].setdefault("log_channel",  None)
    gc["antispam"].setdefault("ignore_users", [])
    gc["antispam"].setdefault("ignore_roles", [])
    gc["antispam"].setdefault("threshold",    SPAM_THRESHOLD)
    gc["antispam"].setdefault("window",       SPAM_WINDOW)
    gc["antispam"].setdefault("flood_count",  5)
    gc["antispam"].setdefault("flood_window", 4)
    gc["antispam"].setdefault("punishment",   "ban")
    # Migration from the old spam_trap_channel key (before the centralized antispam dict existed)
    if "spam_trap_channel" in gc:
        legacy_trap = gc.pop("spam_trap_channel")
        if legacy_trap and not gc["antispam"]["trap_channel"]:
            gc["antispam"]["trap_channel"] = legacy_trap
    gc.setdefault("leveling_enabled",  True)
    gc.setdefault("xp_per_message",    [15, 25])
    gc.setdefault("xp_cooldown",       60)
    gc.setdefault("xp_difficulty",     1.0)
    gc.setdefault("xp_ignore_roles",   [])   # role IDs that never gain XP
    gc.setdefault("members_xp",        {})
    gc.setdefault("level_roles",       {})
    gc.setdefault("warnings",          {})
    gc.setdefault("boost", {
        "channel":     None,
        "title":       "New Server Boost!",
        "emoji":       e(ICON_BOOST, "🎉"),
        "description": "{mention} just boosted **{server}**! Thanks for the support 💜",
    })
    gc["boost"].setdefault("channel",     None)
    gc["boost"].setdefault("title",       "New Server Boost!")
    gc["boost"].setdefault("emoji",       e(ICON_BOOST, "🎉"))
    gc["boost"].setdefault("description", "{mention} just boosted **{server}**! Thanks for the support 💜")
    gc.setdefault("active_tickets",    {})   # uid(str) -> [{"channel_id","panel_id","opened_at"}, ...]
    gc.setdefault("mod_log_channel",   None)
    gc.setdefault("ignored_channels",  [])   # channel ID -> bot stays fully silent (no commands, no XP)
    gc.setdefault("autoresponses_enabled", True)
    gc.setdefault("autoresponses", {})   # trigger(lower) -> {"trigger","response","match","case_sensitive"}
    gc.setdefault("afk_users", {})   # uid(str) -> {"reason": str, "since": unix_ts}
    gc.setdefault("message_components", {})  # component_id -> {title, description, thumbnail, image, color, buttons, message_id, channel_id}
    gc.setdefault("antinuke", {
        "enabled":     False,
        "log_channel": None,
        "whitelist":   [],
        "punishment":  "strip_roles",
    })
    gc["antinuke"].setdefault("enabled",     False)
    gc["antinuke"].setdefault("log_channel", None)
    gc["antinuke"].setdefault("whitelist",   [])
    gc["antinuke"].setdefault("punishment",  "strip_roles")
    gc.setdefault("verification", {
        "enabled":            False,
        "channel_id":         None,
        "unverified_role_id": None,
        "verified_role_id":   None,
        "log_channel_id":     None,
        "message_id":         None,
        "panel_message":      "Click **Verify** below — I'll DM you a short captcha to unlock the rest of the server. Make sure your DMs are open!",
        "result_message":     "Thanks for verifying — enjoy your stay!",
    })
    gc["verification"].setdefault("enabled",            False)
    gc["verification"].setdefault("channel_id",         None)
    gc["verification"].setdefault("unverified_role_id", None)
    gc["verification"].setdefault("verified_role_id",   None)
    gc["verification"].setdefault("log_channel_id",     None)
    gc["verification"].setdefault("message_id",         None)
    gc["verification"].setdefault("panel_message",      "Click **Verify** below — I'll DM you a short captcha to unlock the rest of the server. Make sure your DMs are open!")
    gc["verification"].setdefault("result_message",     "Thanks for verifying — enjoy your stay!")
    gc.setdefault("ticket", {"panels": {}})
    gc["ticket"].setdefault("panels", {})
    # Migrate the old ticket structure (single-config, panels as a list) to the multi-panel dict.
    legacy_cat  = gc["ticket"].pop("category",     None) if "category"     in gc["ticket"] else None
    legacy_log  = gc["ticket"].pop("log_channel",  None) if "log_channel"  in gc["ticket"] else None
    legacy_role = gc["ticket"].pop("support_role", None) if "support_role" in gc["ticket"] else None
    legacy_max  = gc["ticket"].pop("max_tickets",  None) if "max_tickets"  in gc["ticket"] else None
    if isinstance(gc["ticket"].get("panels"), list):
        gc["ticket"]["panels"] = {}
    if legacy_cat and "default" not in gc["ticket"]["panels"]:
        gc["ticket"]["panels"]["default"] = {
            "category":     legacy_cat,
            "log_channel":  legacy_log,
            "support_role": legacy_role,
            "max_tickets":  legacy_max or 1,
            "title":        "Support Tickets",
            "description":  "Click the button below to open a support ticket.",
            "welcome_message": "Thanks for reaching out, {user}! Our support team has been notified and will be with you shortly. Please describe your issue in as much detail as you can.",
            "message_id":   None,
            "channel_id":   None,
        }
    for p in gc["ticket"]["panels"].values():
        p.setdefault("category",     None)
        p.setdefault("log_channel",  None)
        p.setdefault("support_role", None)
        p.setdefault("max_tickets",  1)
        p.setdefault("title",        "Support Tickets")
        p.setdefault("description",  "Click the button below to open a support ticket.")
        p.setdefault("welcome_message", "Thanks for reaching out, {user}! Our support team has been notified and will be with you shortly. Please describe your issue in as much detail as you can.")
        p.setdefault("message_id",   None)
        p.setdefault("channel_id",   None)
    # Migrate the old active_tickets format (uid -> single channel_id int) to the new format (uid -> list).
    for uid, val in list(gc["active_tickets"].items()):
        if isinstance(val, int):
            gc["active_tickets"][uid] = [{"channel_id": val, "panel_id": "default", "opened_at": None, "claimed_by": None}]
        else:
            for tk in val:
                tk.setdefault("claimed_by", None)

def save_config(cfg: dict):
    os.makedirs("data", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def guild_cfg(cfg: dict, guild_id: int) -> dict:
    gid = str(guild_id)
    if gid not in cfg["guilds"]:
        cfg["guilds"][gid] = {}
        _init_guild(cfg["guilds"][gid])
        save_config(cfg)
    gc = cfg["guilds"][gid]
    _init_guild(gc)
    return gc

# ══════════════════════════════════════════════════════════════════
# EMBED HELPERS
# ══════════════════════════════════════════════════════════════════

def _footer(embed: discord.Embed):
    embed.set_footer(text=f"{BOT_NAME} • {BOT_TAGLINE}")
    embed.timestamp = discord.utils.utcnow()
    return embed

def _title_with_icon(icon: str, fallback: str, text: str) -> str:
    ic = e(icon, fallback) if icon else fallback
    return f"{ic} {text}" if ic else text

def base_embed(title: str, description: str = "", color: int = COLOR_PRIMARY) -> discord.Embed:
    return _footer(discord.Embed(title=title, description=description, color=color))

def heading_md(title: str) -> str:
    """Turn a title into Components V2 markdown, auto-picking a smaller
    heading level (`#` -> `##` -> `###`) as it gets longer. Discord has no
    variable font-size for these text displays — heading level is the only
    lever we get — so a long title downgrades to a smaller heading instead
    of wrapping into a huge multi-line block like a short one would at `#`.
    Length thresholds are tuned for how much a single line comfortably fits
    at each level on a phone-width client before it wraps."""
    n = len(title)
    if n <= 20:
        return f"# {title}"
    if n <= 40:
        return f"## {title}"
    return f"### {title}"

def success_embed(desc: str) -> discord.Embed:
    return base_embed(_title_with_icon(ICON_SUCCESS, "✅", "Success"), desc, COLOR_SUCCESS)

def error_embed(desc: str) -> discord.Embed:
    return base_embed(_title_with_icon(ICON_ERROR, "❌", "Error"), desc, COLOR_ERROR)

def warning_embed(title: str, desc: str) -> discord.Embed:
    return base_embed(_title_with_icon(ICON_WARNING, "⚠️", title), desc, COLOR_WARNING)

def info_embed(title: str, desc: str) -> discord.Embed:
    return base_embed(_title_with_icon(ICON_INFO, "ℹ️", title), desc, COLOR_INFO)

_LAST_ERROR_REPORT: dict = {}  # (error_type, location) -> unix ts of last report, for basic dedup/rate-limit

async def report_error(
    error: BaseException,
    *,
    location: str,
    user: Optional[discord.abc.User] = None,
    guild: Optional[discord.Guild] = None,
    channel: Optional[discord.abc.Messageable] = None,
    extra: Optional[str] = None,
):
    """Posts a clean, professional error report to the configured error
    log channel (`errorlog channel #channel`) — so real bugs surface
    immediately in the support server instead of only living in Railway
    logs nobody's watching. Never raises itself (a bug in the reporter
    must never crash whatever it was reporting on, or shadow the
    original error) and always ALSO logs normally regardless of whether
    the Discord report succeeds.

    `location` is a short label for where this happened (a command name,
    an event handler, a background task) — that plus the exception type
    is used for light rate-limiting so one recurring bug doesn't spam the
    channel every single time it fires (max once per 60s per unique
    error+location pair)."""
    tb_text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    logging.error(f"[{BOT_NAME}] Unhandled error in {location}:\n{tb_text}")

    ch_id = cfg.get("error_log_channel_id")
    if not ch_id:
        return
    key = (type(error).__name__, location)
    now = time.time()
    if now - _LAST_ERROR_REPORT.get(key, 0) < 60:
        return
    _LAST_ERROR_REPORT[key] = now

    try:
        log_channel = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
    except Exception:
        return

    embed = discord.Embed(
        title="⚠️ Unhandled Error",
        color=COLOR_ERROR,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Location", value=f"`{location}`", inline=True)
    embed.add_field(name="Type", value=f"`{type(error).__name__}`", inline=True)
    if guild:
        embed.add_field(name="Server", value=f"{guild.name}\n`{guild.id}`", inline=True)
    if user:
        embed.add_field(name="User", value=f"{user}\n`{user.id}`", inline=True)
    if channel is not None and hasattr(channel, "mention"):
        embed.add_field(name="Channel", value=channel.mention, inline=True)
    if extra:
        embed.add_field(name="Context", value=extra[:1024], inline=False)
    msg = str(error) or "(no message)"
    embed.add_field(name="Message", value=f"```{msg[:1000]}```", inline=False)
    embed.set_footer(text=f"{BOT_NAME} • Auto-reported")

    try:
        if len(tb_text) > 3800:
            file = discord.File(io.BytesIO(tb_text.encode("utf-8")), filename="traceback.txt")
            await log_channel.send(embed=embed, file=file)
        else:
            embed.add_field(name="Traceback", value=f"```py\n{tb_text[-1000:]}\n```", inline=False)
            await log_channel.send(embed=embed)
    except Exception:
        logging.warning(f"[{BOT_NAME}] Couldn't deliver error report to log channel {ch_id}.")

# ══════════════════════════════════════════════════════════════════
# XP / LEVELING
# ══════════════════════════════════════════════════════════════════

def xp_for_level(level: int, difficulty: float = 1.0) -> int:
    return round((5 * (level ** 2) + 50 * level + 100) * difficulty)

def level_from_xp(xp: int, difficulty: float = 1.0) -> int:
    level = 0
    while xp >= xp_for_level(level, difficulty):
        xp -= xp_for_level(level, difficulty)
        level += 1
    return level

def xp_progress(total_xp: int, difficulty: float = 1.0):
    level = 0
    xp    = total_xp
    while xp >= xp_for_level(level, difficulty):
        xp -= xp_for_level(level, difficulty)
        level += 1
    return level, xp, xp_for_level(level, difficulty)

def get_member_xp(gc: dict, uid: str) -> dict:
    data = gc["members_xp"].setdefault(uid, {"xp": 0, "level": 0, "last_msg_ts": 0.0, "messages": 0})
    data.setdefault("messages", 0)
    return data

async def apply_level_roles(guild: discord.Guild, member: discord.Member, gc: dict, new_level: int) -> list:
    """Grant every level-role reward whose level is <= new_level that the member
    doesn't already have (stacking — once granted, earlier roles are never removed).
    Returns the list of roles that were just granted (for display in the level-up notification)."""
    level_roles = gc.get("level_roles", {})
    if not level_roles:
        return []
    granted = []
    for lvl_str, role_id in level_roles.items():
        try:
            lvl = int(lvl_str)
        except ValueError:
            continue
        if lvl > new_level:
            continue
        role = guild.get_role(role_id)
        if not role or role in member.roles:
            continue
        try:
            await member.add_roles(role, reason=f"Level role reward — reached level {lvl}")
            granted.append(role)
        except Exception as e:
            logging.error(f"[{BOT_NAME}] Failed to grant level role {role_id} to {member.id}: {e}")
    return granted

# ══════════════════════════════════════════════════════════════════
# BOT SETUP
# ══════════════════════════════════════════════════════════════════

intents                 = discord.Intents.default()
intents.message_content = True
intents.members         = True
intents.guilds          = True

cfg = load_config()

ORIGINAL_CMD_DESCRIPTIONS: dict[str, str] = {}

class VallentTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        data = getattr(interaction, "data", None)
        if not data:
            return True
        # Only process real slash-command invocations — let autocomplete
        # and other interaction types pass through untouched.
        if interaction.type != discord.InteractionType.application_command or data.get("type", 1) != 1:
            return True

        parts   = [data.get("name", "")]
        options = data.get("options", [])
        while options:
            opt = options[0]
            if opt.get("type") in (1, 2):
                parts.append(opt["name"])
                options = opt.get("options", [])
            else:
                break
        cmd_name  = " ".join(parts)
        is_owner_ = interaction.user.id == bot.owner_id

        # ── Maintenance mode — only the owner can bypass ────────────────
        if is_maintenance_on() and not is_owner_:
            m = cfg.get("maintenance", {})
            desc = f"**{BOT_NAME}** is under maintenance, please try again later."
            if m.get("reason"):
                desc += f"\n\n**Reason:** {m['reason']}"
            try:
                await interaction.response.send_message(
                    embed=warning_embed("Under Maintenance", desc), ephemeral=True)
            except discord.InteractionResponded:
                pass
            return False

        # ── Premium-locked command ──────────────────────────────────────
        if cmd_name in cfg.get("premium_commands", []) and not is_owner_ and not user_has_premium(interaction.guild, interaction.user):
            try:
                kwargs = {"embed": warning_embed(
                    "Premium Required",
                    f"Command `/{cmd_name}` is for **Premium** users only.\n"
                    "Contact the owner or join the support server to subscribe."
                ), "ephemeral": True}
                view = premium_upsell_view()
                if view:
                    kwargs["view"] = view
                await interaction.response.send_message(**kwargs)
            except discord.InteractionResponded:
                pass
            return False

        # ── Command usage counter ────────────────────────────────────────
        cmds_run = cfg.setdefault("commands_run", {})
        uid_str  = str(interaction.user.id)
        cmds_run[uid_str] = cmds_run.get(uid_str, 0) + 1
        save_config(cfg)

        return True

bot = commands.Bot(
    command_prefix=BOT_PREFIX,
    intents=intents,
    help_command=None,
    owner_id=int(os.getenv("OWNER_ID", "0")),
    tree_cls=VallentTree,
)

def bot_invite_url() -> Optional[str]:
    """Build the bot's OAuth2 invite URL. Returns None if the bot hasn't
    logged in yet (bot.user unavailable)."""
    if not bot.user:
        return None
    perms = discord.Permissions(
        kick_members=True, ban_members=True, moderate_members=True,
        manage_roles=True, manage_channels=True, manage_messages=True,
        manage_guild=True, manage_webhooks=True, manage_emojis=True,
        view_audit_log=True, mention_everyone=True, embed_links=True,
        attach_files=True, read_message_history=True, send_messages=True,
        add_reactions=True, connect=True, move_members=True,
        use_external_emojis=True,
    )
    return discord.utils.oauth_url(bot.user.id, permissions=perms, scopes=("bot", "applications.commands"))

def brand_embed(embed: discord.Embed) -> discord.Embed:
    """Attach the bot's avatar as a thumbnail and its Discord banner (if it
    has one set) as the embed image. Shared by the help menu, the mention
    auto-reply, and `commandlist` so the bot's branding stays consistent
    everywhere instead of being copy-pasted per command."""
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    if BOT_BANNER_URL:
        embed.set_image(url=BOT_BANNER_URL)
    return embed

def invite_support_view() -> discord.ui.View:
    """Shared 'Invite Me' / 'Support' link-button row — used by the mention
    auto-reply and the help menu. Support button only appears if SUPPORT_INVITE
    is configured and looks like a real URL."""
    view = discord.ui.View()
    invite_url = bot_invite_url()
    if invite_url:
        view.add_item(discord.ui.Button(label="Invite Me", style=discord.ButtonStyle.link, url=invite_url))
    if SUPPORT_INVITE and SUPPORT_INVITE.startswith(("http://", "https://")):
        view.add_item(discord.ui.Button(label="Support", style=discord.ButtonStyle.link, url=SUPPORT_INVITE))
    return view

def premium_upsell_view() -> Optional[discord.ui.View]:
    """Single 'Get Premium' link button pointing at the support server —
    shown whenever someone hits a Premium-locked command. Returns None if
    SUPPORT_INVITE isn't configured, so callers can skip attaching a view."""
    if not (SUPPORT_INVITE and SUPPORT_INVITE.startswith(("http://", "https://"))):
        return None
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Get Premium", style=discord.ButtonStyle.link, url=SUPPORT_INVITE, emoji="💎"))
    return view

def bot_info_embed(mention: str, guild_id: int) -> discord.Embed:
    """The card shown when the bot is @mentioned directly in chat."""
    embed = discord.Embed(
        title=f"{BOT_NAME} — INFO",
        description=(
            f"Hey {mention},\n"
            f"My prefix here is: `!vx` (`!v` also works)\n"
            f"Server ID: `{guild_id}`\n\n"
            f"Type `!vx help` to see the command list."
        ),
        color=COLOR_PRIMARY,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=BOT_TAGLINE)
    return brand_embed(embed)

# ══════════════════════════════════════════════════════════════════
# PREMIUM HELPERS
# ══════════════════════════════════════════════════════════════════

def user_has_premium(guild: Optional[discord.Guild], user: discord.abc.User) -> bool:
    uid        = str(user.id)
    expiry_map = cfg.get("premium_expiry", {})
    if user.id in cfg.get("premium_users", []):
        expiry_str = expiry_map.get(uid)
        if expiry_str:
            try:
                exp = datetime.datetime.fromisoformat(expiry_str)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=datetime.timezone.utc)
                if datetime.datetime.now(datetime.timezone.utc) > exp:
                    cfg["premium_users"] = [u for u in cfg["premium_users"] if u != user.id]
                    cfg["premium_expiry"].pop(uid, None)
                    save_config(cfg)
                    return False
            except Exception:
                pass
        return True
    if guild and guild.id in cfg.get("premium_guilds", []):
        return True
    return False

_BG_URL_RE = re.compile(r"^https?://\S+\.(png|jpe?g|webp)(\?\S*)?$", re.IGNORECASE)

async def _fetch_bg_bytes(is_prem: bool, target_uid: int, store_key: str) -> Optional[bytes]:
    """Download the target's custom background from `store_key`
    (`premium_backgrounds` for the rank/level-up cards, `profile_backgrounds`
    for the ID card — kept as two separate stores so a user can set a
    different background on each), if they're premium and have one set.
    Returns None (never raises) on any failure — a dead/removed image URL
    should degrade to the normal gradient background, never break the
    whole card render."""
    if not is_prem:
        return None
    url = cfg.get(store_key, {}).get(str(target_uid))
    if not url:
        return None
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
    except Exception:
        logging.warning(f"[{BOT_NAME}] Gagal download custom background ({store_key}) dari {url}")
        return None

async def fetch_rank_bg_bytes(is_prem: bool, target_uid: int) -> Optional[bytes]:
    """Custom background for the rank card + level-up card (set via `rankbg`)."""
    return await _fetch_bg_bytes(is_prem, target_uid, "premium_backgrounds")

async def fetch_profile_bg_bytes(is_prem: bool, target_uid: int) -> Optional[bytes]:
    """Custom background for the ID card only (set via `idcardbg`) — a
    separate store from the rank card's, on purpose: the two cards are
    very different sizes/shapes, so a user may want a different image
    cropped for each rather than one background forced onto both."""
    return await _fetch_bg_bytes(is_prem, target_uid, "profile_backgrounds")

_HEX_COLOR_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")

def parse_hex_color(raw: str) -> Optional[tuple]:
    """'#a672ff' / 'a672ff' -> (166, 114, 255). None for anything invalid."""
    m = _HEX_COLOR_RE.match((raw or "").strip())
    if not m:
        return None
    hex6 = m.group(1)
    return tuple(int(hex6[i:i+2], 16) for i in (0, 2, 4))

def _get_accent(is_prem: bool, uid: int, store_key: str) -> Optional[list]:
    """The target's custom gradient accent (2 or 3 colors) from `store_key`,
    if they're premium and have one set — returns a list of 2-3 (r,g,b)
    tuples, or None (falls back to the default gold theme). Never raises:
    a corrupted/legacy-bad stored value just degrades to default instead
    of breaking the card render."""
    if not is_prem:
        return None
    stops = cfg.get(store_key, {}).get(str(uid))
    if not stops or not (2 <= len(stops) <= 3):
        return None
    parsed = [parse_hex_color(s) for s in stops]
    if not all(parsed):
        return None
    return parsed

def get_premium_accent(is_prem: bool, uid: int) -> Optional[list]:
    """Custom gradient for the rank card + level-up card (set via `rankcolor`)."""
    return _get_accent(is_prem, uid, "premium_colors")

def get_profile_accent(is_prem: bool, uid: int) -> Optional[list]:
    """Custom gradient for the ID card only (set via `idcardcolor`) — a
    separate store from the rank card's, same reasoning as the background
    split above."""
    return _get_accent(is_prem, uid, "profile_colors")

def int_to_rgb(hex_int: int) -> tuple:
    """0xDC143C -> (220, 20, 60) — BOT_ROLE_BADGES stores colors as a hex
    int (for discord.Embed.color), the ID card needs plain RGB tuples."""
    return ((hex_int >> 16) & 0xFF, (hex_int >> 8) & 0xFF, hex_int & 0xFF)

def get_member_no(uid: int) -> int:
    """A stable, sequential 'member number' for the ID card — assigned
    once the first time a user's card is ever generated, then reused
    forever after (never reassigned, never reused even if data is pruned)."""
    ids = cfg.setdefault("profile_ids", {})
    key = str(uid)
    if key not in ids:
        cfg["profile_id_counter"] = cfg.get("profile_id_counter", 0) + 1
        ids[key] = cfg["profile_id_counter"]
        save_config(cfg)
    return ids[key]

_CUSTOM_EMOJI_ID_RE = re.compile(r"^<a?:[a-zA-Z0-9_]{2,32}:(\d+)>$")

async def fetch_badge_icon_images(uid: int) -> list:
    """Every badge this user holds (bot-role + custom), ready to paste onto
    the ID card: custom Discord emoji get their actual image downloaded
    from Discord's CDN in parallel; plain unicode/text emoji are left as
    text (drawn with the bundled emoji-fallback font, no network needed).
    A single badge's icon failing to download never drops the badge or
    breaks the others — it just falls back to drawing its raw character."""
    badges = get_user_badges(uid)
    role_infos  = [BOT_ROLE_BADGES.get(b, BOT_ROLE_BADGES["user"]) for b in badges]
    custom_defs = get_custom_badges(uid)
    entries = [{"emoji": info.get("emoji", "")} for info in role_infos]
    entries += [{"emoji": cb.get("emoji", "")} for cb in custom_defs]

    async def resolve(entry):
        raw = entry["emoji"]
        m = _CUSTOM_EMOJI_ID_RE.match(raw or "")
        if not m:
            return {"kind": "text", "char": raw or "\u2022", "color": (245, 245, 245)}
        animated = raw.startswith("<a:")
        emoji_id = m.group(1)
        ext = "gif" if animated else "png"
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size=64"
        # A plain aiohttp client with no headers gets blocked/empty-bodied by
        # some CDN edges that only serve real browser-looking requests — a
        # normal User-Agent fixes that without needing anything else.
        headers = {"User-Agent": "Mozilla/5.0 (compatible; VallentEXS/1.0; +https://discord.com)"}
        try:
            import aiohttp
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logging.warning(f"[{BOT_NAME}] badge emoji fetch: HTTP {resp.status} for {url}")
                        return {"kind": "text", "char": "\u2022", "color": (245, 245, 245)}
                    data = await resp.read()
            img = await asyncio.to_thread(Image.open, io.BytesIO(data))
            img.load()
            return {"kind": "image", "img": img}
        except Exception as ex:
            logging.warning(f"[{BOT_NAME}] badge emoji fetch failed for {url}: {ex!r}")
            return {"kind": "text", "char": "\u2022", "color": (245, 245, 245)}

    if not entries:
        return []
    return await asyncio.gather(*(resolve(e) for e in entries))

async def check_premium_expiry():
    now        = datetime.datetime.now(datetime.timezone.utc)
    expiry_map = cfg.get("premium_expiry", {})
    revoked    = []
    for uid_str, expiry_str in list(expiry_map.items()):
        try:
            exp = datetime.datetime.fromisoformat(expiry_str)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=datetime.timezone.utc)
            if now > exp:
                uid = int(uid_str)
                cfg["premium_users"] = [u for u in cfg.get("premium_users", []) if u != uid]
                expiry_map.pop(uid_str, None)
                revoked.append(uid)
        except Exception:
            pass
    if revoked:
        save_config(cfg)
        logging.info(f"[Premium] Expired and revoked: {revoked}")
        for uid in revoked:
            try:
                user = bot.get_user(uid) or await bot.fetch_user(uid)
                await user.send(embed=base_embed(
                    "Premium Expired",
                    f"Your Premium subscription on **{BOT_NAME}** has ended.\n"
                    "All premium commands and no-prefix access have been disabled.\n"
                    "Contact the owner if you'd like to renew.",
                    color=COLOR_ERROR
                ))
            except Exception:
                pass

def user_has_no_prefix(guild: Optional[discord.Guild], user: discord.abc.User) -> bool:
    """No-prefix is active for: the owner, manually-granted users (with/without a
    duration), granted guilds, or anyone with active Premium (Premium auto-unlocks no-prefix)."""
    if user.id == bot.owner_id:
        return True
    if user.id in cfg.get("no_prefix_users", []):
        uid_str    = str(user.id)
        expiry_str = cfg.get("no_prefix_expiry", {}).get(uid_str)
        if expiry_str:
            try:
                exp = datetime.datetime.fromisoformat(expiry_str)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=datetime.timezone.utc)
                if datetime.datetime.now(datetime.timezone.utc) > exp:
                    cfg["no_prefix_users"] = [u for u in cfg["no_prefix_users"] if u != user.id]
                    cfg.get("no_prefix_expiry", {}).pop(uid_str, None)
                    save_config(cfg)
                    return user_has_premium(guild, user)
            except Exception:
                pass
        return True
    if guild and guild.id in cfg.get("no_prefix_guilds", []):
        return True
    return user_has_premium(guild, user)

async def check_no_prefix_expiry():
    now        = datetime.datetime.now(datetime.timezone.utc)
    expiry_map = cfg.get("no_prefix_expiry", {})
    revoked    = []
    for uid_str, expiry_str in list(expiry_map.items()):
        try:
            exp = datetime.datetime.fromisoformat(expiry_str)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=datetime.timezone.utc)
            if now > exp:
                uid = int(uid_str)
                cfg["no_prefix_users"] = [u for u in cfg.get("no_prefix_users", []) if u != uid]
                expiry_map.pop(uid_str, None)
                revoked.append(uid)
        except Exception:
            pass
    if revoked:
        save_config(cfg)
        logging.info(f"[No-Prefix] Expired and revoked: {revoked}")
        for uid in revoked:
            try:
                user = bot.get_user(uid) or await bot.fetch_user(uid)
                await user.send(embed=base_embed(
                    "No-Prefix Expired",
                    f"Your no-prefix access on **{BOT_NAME}** has ended.\n"
                    "Commands now need the `!vx` prefix again.\n"
                    "Contact the owner if you'd like to renew.",
                    color=COLOR_ERROR
                ))
            except Exception:
                pass

def is_maintenance_on() -> bool:
    return bool(cfg.get("maintenance", {}).get("enabled", False))

def grant_xp_boost(uid: int, minutes: int = 60, multiplier: float = 1.10, extend_only: bool = False):
    """Grant a temporary XP boost — used as an incentive for joining the support
    server (and, separately, for voting on top.gg). Applies across ALL guilds
    (not per-guild) since this is a personal reward, not a server setting.

    extend_only=True never SHORTENS an already-longer boost still running
    (e.g. someone with a 60-min join-server boost active votes and gets a
    20-min boost — without this they'd lose 40 minutes of remaining time).
    """
    new_expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
    if extend_only:
        existing = cfg.get("xp_boost", {}).get(str(uid))
        if existing:
            try:
                cur_expiry = datetime.datetime.fromisoformat(existing["expiry"])
                if cur_expiry.tzinfo is None:
                    cur_expiry = cur_expiry.replace(tzinfo=datetime.timezone.utc)
                if cur_expiry > new_expiry:
                    new_expiry = cur_expiry
            except Exception:
                pass
    cfg.setdefault("xp_boost", {})[str(uid)] = {"expiry": new_expiry.isoformat(), "multiplier": multiplier}
    save_config(cfg)

_vote_webhook_started = False  # guards against starting the web server twice on gateway reconnects

async def _handle_topgg_vote(uid: int):
    """Called by vote_system's webhook app for every CONFIRMED real vote.
    Grants the +10% / 20-min boost (extend_only so it never shortens a
    longer boost already running), updates the vote ledger, and DMs the
    voter their reward — best-effort, a closed-DM user still gets the
    boost, they just don't get the notification."""
    grant_xp_boost(uid, minutes=vote_system.BOOST_MINUTES, multiplier=vote_system.BOOST_MULTIPLIER, extend_only=True)
    entry = vote_system.record_vote(cfg.setdefault("votes", {}), str(uid))
    save_config(cfg)
    print(f"[{BOT_NAME}] Vote reward granted to user {uid} (top.gg webhook).")

    try:
        user = bot.get_user(uid) or await bot.fetch_user(uid)
        streak = entry.get("streak", 1)
        embed = discord.Embed(
            title="🎉 Thanks for voting!",
            description=(
                f"You just earned a **+{int((vote_system.BOOST_MULTIPLIER - 1) * 100)}% XP Boost** "
                f"for the next **{vote_system.BOOST_MINUTES} minutes** on every server using {BOT_NAME}!\n\n"
                f"🔥 Vote streak: **{streak}**\n"
                f"You can vote again in **{vote_system.VOTE_COOLDOWN_HOURS} hours**."
            ),
            color=COLOR_SUCCESS,
            timestamp=discord.utils.utcnow()
        )
        if TOPGG_VOTE_URL:
            embed.add_field(name="Vote again later", value=f"[Click here]({TOPGG_VOTE_URL})", inline=False)
        embed.set_footer(text=BOT_NAME)
        await user.send(embed=embed)
    except Exception:
        # DMs closed / user uninstalled / whatever — the boost itself is
        # already granted above regardless, this is just the notification.
        logging.warning(f"[{BOT_NAME}] Couldn't DM vote reward notification to {uid} (DMs likely closed).")

async def start_vote_webhook_server():
    """Starts the single aiohttp server that hosts both the top.gg vote
    webhook and the web dashboard, on whichever of those is actually
    configured — safe to call repeatedly, only ever actually binds the
    port once (guarded by _vote_webhook_started). Kept under its old name
    since that's what on_ready already calls; it just does more now."""
    global _vote_webhook_started
    if _vote_webhook_started:
        return
    if not TOPGG_WEBHOOK_AUTH and not DASHBOARD_CLIENT_SECRET:
        return
    try:
        app = aiohttp_web.Application()

        if TOPGG_WEBHOOK_AUTH:
            vote_system.build_webhook_app(TOPGG_WEBHOOK_AUTH, _handle_topgg_vote, app=app)

        if DASHBOARD_CLIENT_SECRET and DASHBOARD_REDIRECT_URI and DASHBOARD_SESSION_SECRET:
            def _get_leveling(guild_id: int) -> dict:
                gc = guild_cfg(cfg, guild_id)
                ch = gc.get("level_channel")
                return {
                    "enabled": gc.get("leveling_enabled", True),
                    "channel_id": str(ch) if ch else None,
                    "difficulty": gc.get("xp_difficulty", 1.0),
                }

            def _set_leveling(guild_id: int, update: dict) -> None:
                gc = guild_cfg(cfg, guild_id)
                if "enabled" in update:
                    gc["leveling_enabled"] = update["enabled"]
                if "channel_id" in update:
                    gc["level_channel"] = int(update["channel_id"]) if update["channel_id"] else None
                if "difficulty" in update:
                    gc["xp_difficulty"] = update["difficulty"]
                save_config(cfg)

            def _get_antinuke(guild_id: int) -> dict:
                gc = guild_cfg(cfg, guild_id)
                ac = gc.get("antinuke", {})
                guild = bot.get_guild(guild_id)
                whitelist = []
                for uid in ac.get("whitelist", []):
                    member = guild.get_member(uid) if guild else None
                    whitelist.append({
                        "id": str(uid),
                        "name": member.display_name if member else f"Unknown ({uid})",
                        "avatar": str(member.display_avatar.url) if member else None,
                    })
                return {
                    "enabled": ac.get("enabled", False),
                    "log_channel": str(ac["log_channel"]) if ac.get("log_channel") else None,
                    "punishment": ac.get("punishment", "strip_roles"),
                    "whitelist": whitelist,
                    "bot_has_audit_log_perm": bool(guild and guild.me.guild_permissions.view_audit_log),
                }

            def _set_antinuke(guild_id: int, update: dict) -> Optional[str]:
                """Returns an error string on failure, None on success —
                mirrors the command's own check that the bot actually has
                View Audit Log before anti-nuke can be turned on."""
                gc = guild_cfg(cfg, guild_id)
                ac = gc.setdefault("antinuke", {"enabled": False, "log_channel": None, "whitelist": [], "punishment": "strip_roles"})
                if "enabled" in update and update["enabled"]:
                    guild = bot.get_guild(guild_id)
                    if not guild or not guild.me.guild_permissions.view_audit_log:
                        return "The bot needs the View Audit Log permission before Anti-Nuke can be enabled."
                if "enabled" in update:
                    ac["enabled"] = update["enabled"]
                if "log_channel" in update:
                    ac["log_channel"] = int(update["log_channel"]) if update["log_channel"] else None
                if "punishment" in update:
                    if update["punishment"] not in ("strip_roles", "kick", "ban"):
                        return "Invalid punishment option."
                    ac["punishment"] = update["punishment"]
                save_config(cfg)
                return None

            def _add_antinuke_whitelist(guild_id: int, user_id: int) -> Optional[str]:
                gc = guild_cfg(cfg, guild_id)
                ac = gc.setdefault("antinuke", {"enabled": False, "log_channel": None, "whitelist": [], "punishment": "strip_roles"})
                guild = bot.get_guild(guild_id)
                if not guild or not guild.get_member(user_id):
                    return "That user isn't a member of this server."
                wl = ac.setdefault("whitelist", [])
                if user_id not in wl:
                    wl.append(user_id)
                    save_config(cfg)
                return None

            def _remove_antinuke_whitelist(guild_id: int, user_id: int) -> None:
                gc = guild_cfg(cfg, guild_id)
                wl = gc.get("antinuke", {}).get("whitelist", [])
                if user_id in wl:
                    wl.remove(user_id)
                    save_config(cfg)

            def _get_antispam(guild_id: int) -> dict:
                gc = guild_cfg(cfg, guild_id)
                ac = gc.get("antispam", {})
                guild = bot.get_guild(guild_id)

                def _resolve_users(ids):
                    out = []
                    for uid in ids:
                        m = guild.get_member(uid) if guild else None
                        out.append({"id": str(uid), "name": m.display_name if m else f"Unknown ({uid})",
                                    "avatar": str(m.display_avatar.url) if m else None})
                    return out

                def _resolve_roles(ids):
                    out = []
                    for rid in ids:
                        r = guild.get_role(rid) if guild else None
                        out.append({"id": str(rid), "name": r.name if r else f"Unknown ({rid})",
                                    "color": (f"#{r.color.value:06x}" if r and r.color.value else "#a3908d")})
                    return out

                return {
                    "trap_channel": str(ac["trap_channel"]) if ac.get("trap_channel") else None,
                    "log_channel": str(ac["log_channel"]) if ac.get("log_channel") else None,
                    "punishment": ac.get("punishment", "ban"),
                    "threshold": ac.get("threshold", SPAM_THRESHOLD),
                    "window": ac.get("window", SPAM_WINDOW),
                    "flood_count": ac.get("flood_count", 5),
                    "flood_window": ac.get("flood_window", 4),
                    "ignore_users": _resolve_users(ac.get("ignore_users", [])),
                    "ignore_roles": _resolve_roles(ac.get("ignore_roles", [])),
                }

            def _set_antispam(guild_id: int, update: dict) -> Optional[str]:
                gc = guild_cfg(cfg, guild_id)
                ac = gc.setdefault("antispam", {})
                if "trap_channel" in update:
                    ac["trap_channel"] = int(update["trap_channel"]) if update["trap_channel"] else None
                if "log_channel" in update:
                    ac["log_channel"] = int(update["log_channel"]) if update["log_channel"] else None
                if "punishment" in update:
                    if update["punishment"] not in ("ban", "kick", "timeout"):
                        return "Invalid punishment option."
                    ac["punishment"] = update["punishment"]
                for int_field in ("threshold", "window", "flood_count", "flood_window"):
                    if int_field in update:
                        try:
                            val = int(update[int_field])
                        except (TypeError, ValueError):
                            return f"Invalid value for {int_field}."
                        if val < 1:
                            return f"{int_field} must be at least 1."
                        ac[int_field] = val
                save_config(cfg)
                return None

            def _add_antispam_ignore(guild_id: int, kind: str, target_id: int) -> Optional[str]:
                gc = guild_cfg(cfg, guild_id)
                ac = gc.setdefault("antispam", {})
                guild = bot.get_guild(guild_id)
                key = "ignore_users" if kind == "user" else "ignore_roles"
                if kind == "user" and (not guild or not guild.get_member(target_id)):
                    return "That user isn't a member of this server."
                if kind == "role" and (not guild or not guild.get_role(target_id)):
                    return "That role doesn't exist on this server."
                lst = ac.setdefault(key, [])
                if target_id not in lst:
                    lst.append(target_id)
                    save_config(cfg)
                return None

            def _remove_antispam_ignore(guild_id: int, kind: str, target_id: int) -> None:
                gc = guild_cfg(cfg, guild_id)
                key = "ignore_users" if kind == "user" else "ignore_roles"
                lst = gc.get("antispam", {}).get(key, [])
                if target_id in lst:
                    lst.remove(target_id)
                    save_config(cfg)

            # ---------------- My Rank Card / Profile customization ----------------
            # User-scoped (not guild-scoped) — mirrors `rankcolor`/`rankbg`
            # (rank card + level-up card) and `idcardcolor`/`idcardbg`
            # (profile ID card, kept separate) exactly, reusing the same
            # cfg stores, the same parse_hex_color/_BG_URL_RE validation,
            # and the same Premium gate as those commands.

            def _dashboard_user_is_premium(uid: int) -> bool:
                if user_has_premium(None, SimpleNamespace(id=uid)):
                    return True
                for guild in bot.guilds:
                    if guild.id in cfg.get("premium_guilds", []) and guild.get_member(uid):
                        return True
                return False

            def _get_customization(uid: int) -> dict:
                suid = str(uid)
                return {
                    "is_premium": _dashboard_user_is_premium(uid),
                    "rank_colors": cfg.get("premium_colors", {}).get(suid),
                    "rank_background": cfg.get("premium_backgrounds", {}).get(suid),
                    "profile_colors": cfg.get("profile_colors", {}).get(suid),
                    "profile_background": cfg.get("profile_backgrounds", {}).get(suid),
                }

            def _set_gradient(uid: int, colors: Optional[list], store_key: str) -> Optional[str]:
                if not _dashboard_user_is_premium(uid):
                    return "Custom gradients are a Premium perk — ask the bot owner about getting Premium."
                store = cfg.setdefault(store_key, {})
                suid = str(uid)
                if not colors:
                    store.pop(suid, None)
                    save_config(cfg)
                    return None
                if not (2 <= len(colors) <= 3):
                    return "Give 2 or 3 hex colors."
                parsed = [parse_hex_color(c) for c in colors]
                if not all(parsed):
                    return "That doesn't look like a valid hex color — use 6-digit hex codes like #a672ff."
                store[suid] = [c.strip().lstrip("#") for c in colors]
                save_config(cfg)
                return None

            def _set_background(uid: int, url: Optional[str], store_key: str) -> Optional[str]:
                if not _dashboard_user_is_premium(uid):
                    return "Custom backgrounds are a Premium perk — ask the bot owner about getting Premium."
                store = cfg.setdefault(store_key, {})
                suid = str(uid)
                if not url:
                    store.pop(suid, None)
                    save_config(cfg)
                    return None
                if not _BG_URL_RE.match(url.strip()):
                    return "That doesn't look like a valid direct image URL — it needs to start with http(s):// and end in .png, .jpg, .jpeg, or .webp."
                store[suid] = url.strip()
                save_config(cfg)
                return None

            def _set_rank_colors(uid: int, colors: Optional[list]) -> Optional[str]:
                return _set_gradient(uid, colors, "premium_colors")

            def _set_rank_background(uid: int, url: Optional[str]) -> Optional[str]:
                return _set_background(uid, url, "premium_backgrounds")

            def _set_profile_colors(uid: int, colors: Optional[list]) -> Optional[str]:
                return _set_gradient(uid, colors, "profile_colors")

            def _set_profile_background(uid: int, url: Optional[str]) -> Optional[str]:
                return _set_background(uid, url, "profile_backgrounds")

            dashboard.build_dashboard_routes(
                app,
                client_id=str(bot.user.id),
                client_secret=DASHBOARD_CLIENT_SECRET,
                redirect_uri=DASHBOARD_REDIRECT_URI,
                session_secret=DASHBOARD_SESSION_SECRET,
                get_bot=lambda: bot,
                get_leveling=_get_leveling,
                set_leveling=_set_leveling,
                get_antinuke=_get_antinuke,
                set_antinuke=_set_antinuke,
                add_antinuke_whitelist=_add_antinuke_whitelist,
                remove_antinuke_whitelist=_remove_antinuke_whitelist,
                get_antispam=_get_antispam,
                set_antispam=_set_antispam,
                add_antispam_ignore=_add_antispam_ignore,
                remove_antispam_ignore=_remove_antispam_ignore,
                get_customization=_get_customization,
                set_rank_colors=_set_rank_colors,
                set_rank_background=_set_rank_background,
                set_profile_colors=_set_profile_colors,
                set_profile_background=_set_profile_background,
            )
            print(f"[{BOT_NAME}] Web dashboard live at {DASHBOARD_REDIRECT_URI.rsplit('/auth/', 1)[0]}/dashboard")
        elif DASHBOARD_CLIENT_SECRET:
            logging.warning(f"[{BOT_NAME}] DISCORD_CLIENT_SECRET is set but DASHBOARD_REDIRECT_URI/SESSION_SECRET are missing — dashboard not started.")

        runner = aiohttp_web.AppRunner(app)
        await runner.setup()
        site = aiohttp_web.TCPSite(runner, "0.0.0.0", WEB_PORT)
        await site.start()
        _vote_webhook_started = True
        if TOPGG_WEBHOOK_AUTH:
            print(f"[{BOT_NAME}] top.gg vote webhook listening on port {WEB_PORT} (path /topgg/vote).")
    except Exception:
        logging.exception(f"[{BOT_NAME}] Failed to start the web server")

def can_receive_join_boost(uid: int, cooldown_hours: int = 24) -> bool:
    """Anti-farm guard for the support-server join XP boost. Without this,
    someone could leave and rejoin the support server on repeat to keep
    resetting the boost's 60-minute timer and hold +10% XP indefinitely for
    free. Limits a fresh grant to once per `cooldown_hours` per user —
    genuine returning members still get it, repeat leave/rejoin spam doesn't."""
    last = cfg.setdefault("join_boost_last_grant", {}).get(str(uid))
    if not last:
        return True
    try:
        last_dt = datetime.datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return True
    return datetime.datetime.now(datetime.timezone.utc) - last_dt >= datetime.timedelta(hours=cooldown_hours)

def mark_join_boost_granted(uid: int):
    cfg.setdefault("join_boost_last_grant", {})[str(uid)] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_config(cfg)

def get_xp_multiplier(uid: int) -> float:
    """Return the active XP multiplier for a user (1.0 if no boost / already expired).
    Expired entries are cleaned up automatically (lazy cleanup, same pattern as premium)."""
    boosts  = cfg.setdefault("xp_boost", {})
    uid_str = str(uid)
    entry   = boosts.get(uid_str)
    if not entry:
        return 1.0
    try:
        exp = datetime.datetime.fromisoformat(entry["expiry"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.timezone.utc)
        if datetime.datetime.now(datetime.timezone.utc) > exp:
            boosts.pop(uid_str, None)
            save_config(cfg)
            return 1.0
        return float(entry.get("multiplier", 1.0))
    except Exception:
        boosts.pop(uid_str, None)
        return 1.0

def xp_boost_remaining(uid: int) -> Optional[datetime.datetime]:
    """Return the expiry time of an active boost, or None if there isn't one."""
    entry = cfg.get("xp_boost", {}).get(str(uid))
    if not entry:
        return None
    try:
        exp = datetime.datetime.fromisoformat(entry["expiry"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.timezone.utc)
        return exp if exp > datetime.datetime.now(datetime.timezone.utc) else None
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════════
# BOT ROLES SYSTEM
# ══════════════════════════════════════════════════════════════════

BOT_ROLE_HIERARCHY = ["staff", "moderator", "server_manager", "management", "developer", "founder"]

BOT_ROLE_BADGES = {
    # Emoji sourced from emoji_config.py — edit that file to set the emoji IDs
    "founder":        {"label": "• Founder",        "color": 0x8B0000, "emoji": BADGE_FOUNDER},
    "developer":      {"label": "• Developer",      "color": 0xDC143C, "emoji": BADGE_DEVELOPER},
    "management":     {"label": "• Management",     "color": 0xB22222, "emoji": BADGE_MANAGEMENT},
    "moonkeeper":     {"label": "• Moonkeeper",      "color": 0x6366F1, "emoji": e(BADGE_MOONKEEPER, "🌙")},
    "server_manager": {"label": "• Server Manager", "color": 0xE67E22, "emoji": e(BADGE_SERVER_MANAGER, "🗂️")},
    "moderator":      {"label": "• Moderator",      "color": 0xC97C3D, "emoji": e(BADGE_MODERATOR, "🛡️")},
    "staff":          {"label": "• Staff",          "color": 0xCD5C5C, "emoji": BADGE_STAFF},
    "premium":        {"label": "• Premium",        "color": 0xF59E0B, "emoji": BADGE_PREMIUM},
    "noprefix":       {"label": "• No Prefix",      "color": 0x22C55E, "emoji": BADGE_NOPREFIX},
    "user":           {"label": "• User",           "color": 0x6B7280, "emoji": BADGE_USER},
}

def get_support_guild() -> Optional[discord.Guild]:
    support_server_id = int(os.getenv("SUPPORT_SERVER_ID", "0"))
    return bot.get_guild(support_server_id) if support_server_id else None

def get_synced_role(uid: int) -> Optional[str]:
    """Check the user's real Discord role in the support server against the
    role_sync mapping. Checked from highest (founder) to lowest (staff) — if the
    member has multiple synced roles, the highest badge wins."""
    guild = get_support_guild()
    if not guild:
        return None
    member = guild.get_member(uid)
    if not member:
        return None
    role_sync = cfg.get("role_sync", {})
    for tier in reversed(BOT_ROLE_HIERARCHY):  # founder -> developer -> management -> staff
        role_id = role_sync.get(tier)
        if role_id and any(r.id == role_id for r in member.roles):
            return tier
    return None

def get_bot_role(uid: int) -> str:
    if uid == bot.owner_id:
        return "founder"
    synced = get_synced_role(uid)
    if synced:
        return synced
    return cfg.get("bot_roles", {}).get(str(uid), "user")

def get_user_badges(uid: int) -> list:
    """
    Collect all of a user's badges.
    Hierarchy: founder > developer > management > staff > noprefix > premium > user
    The USER badge is only granted if the user has joined the bot's support server.
    If they have no badges at all -> empty list.
    """
    badges  = []
    role    = get_bot_role(uid)
    is_prem = uid in cfg.get("premium_users", [])
    if role != "user":
        badges.append(role)
    if is_moonkeeper(uid):
        badges.append("moonkeeper")
    if uid in cfg.get("no_prefix_users", []) or is_prem:
        badges.append("noprefix")
    if is_prem:
        badges.append("premium")
    if uid in cfg.get("support_server_members", []):
        badges.append("user")
    # No default badge — if it's empty, it stays empty
    return badges

# ══════════════════════════════════════════════════════════════════
# CUSTOM BADGES — free-form badges the owner designs and assigns
# ══════════════════════════════════════════════════════════════════
# Fully independent from BOT_ROLE_BADGES above. Name and emoji are 100%
# up to the owner (any text, any emoji including custom server emoji) —
# these aren't tied to a hierarchy or a Discord role, just a manual grant
# stored per-user. Only the bot owner can create/delete/give/remove them
# (see the `custombadge` command further down).

def _sanitize_badge_name(name: str) -> str:
    """Strip any Discord mention syntax (@user, @role, #channel, @everyone/
    @here) out of a badge name. Badge names are just display text — a
    mention accidentally typed while naming a badge (e.g. `custombadge
    create 🔥 Monarch @Niks.`) should never turn into a real, clickable/
    pinging tag on the profile card."""
    name = re.sub(r"<@!?\d+>|<@&\d+>|<#\d+>", "", name)
    name = re.sub(r"@(everyone|here)", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name

def _slugify_badge_id(name: str) -> str:
    """Turn a badge name into a short, stable dict key (e.g. 'Dragon Tamer'
    -> 'dragon_tamer'). If that slug is already used by a different badge,
    suffix it with a counter so two similarly-named badges never collide
    or silently overwrite each other."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "badge"
    defs = cfg.get("custom_badges", {})
    slug = base
    n = 2
    while slug in defs:
        slug = f"{base}_{n}"
        n += 1
    return slug

def get_custom_badges(uid: int) -> list:
    """Return this user's owner-granted custom badges, in the order they
    were given, as {"id", "name", "emoji"} dicts. If the owner later deletes
    a badge's definition, it's silently dropped here instead of erroring."""
    defs = cfg.get("custom_badges", {})
    ids  = cfg.get("user_custom_badges", {}).get(str(uid), [])
    return [{"id": bid, **defs[bid]} for bid in ids if bid in defs]

def _badge_display_lines(uid: int) -> tuple:
    """Build the formatted '<emoji> **Name**' lines for every badge a user
    holds — bot-role badges (Founder/Staff/etc.) first, then owner-granted
    custom badges — plus the total count. Shared by the profile embed and
    the support-server welcome DM so the two can never drift out of sync."""
    lines = []
    for b in get_user_badges(uid):
        info      = BOT_ROLE_BADGES.get(b, BOT_ROLE_BADGES["user"])
        emoji_str = info.get("emoji", "")
        prefix    = (emoji_str + " ") if emoji_str else "\u2022 "
        lines.append(prefix + "**" + info["label"] + "**")
    for cb in get_custom_badges(uid):
        emoji_str = cb.get("emoji", "")
        prefix    = (emoji_str + " ") if emoji_str else "\u2022 "
        lines.append(prefix + "**\u2022 " + cb["name"] + "**")
    return lines, len(lines)

def _resolve_badge_target(token: str) -> Optional[int]:
    """Parse a user mention or raw ID into an int. Returns None if the
    token isn't a valid user reference. Deliberately doesn't require the
    target to be a member of the current guild — custom badges are global
    (bot-wide), same as the bot-role badges, so the owner can badge anyone
    the bot has ever seen from any server."""
    m = re.match(r"<@!?(\d+)>$|^(\d{17,20})$", token.strip())
    if not m:
        return None
    return int(m.group(1) or m.group(2))

async def build_profile_card_file(target: discord.Member) -> discord.File:
    """Fetches everything the profile ID card needs (avatar, badge icons,
    custom background/gradient, level, dates) and renders it — shared by
    both `profile` and `/profile` so the two commands can never drift
    apart. Runs the actual Pillow render in a thread so it never blocks
    the event loop."""
    uid = target.id
    role      = get_bot_role(uid)
    role_info = BOT_ROLE_BADGES.get(role, BOT_ROLE_BADGES["user"])
    hierarchy_label = re.sub(r"^[^\w]+", "", role_info["label"]).upper()  # drop the "• " prefix
    hierarchy_color = int_to_rgb(role_info["color"])

    is_prem = uid in cfg.get("premium_users", [])
    if is_prem:
        exp_str = cfg.get("premium_expiry", {}).get(str(uid))
        premium_text = "Active — Lifetime"
        if exp_str:
            try:
                exp = datetime.datetime.fromisoformat(exp_str)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=datetime.timezone.utc)
                days_left = (exp - datetime.datetime.now(datetime.timezone.utc)).days
                premium_text = f"Active — {max(days_left, 0)}d left" if days_left >= 0 else "Expired"
            except Exception:
                premium_text = "Active"
    else:
        premium_text = "Not Active"

    avatar_url = str(target.display_avatar.with_format("png").with_size(256))
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            avatar_bytes = await resp.read()

    badge_icons   = await fetch_badge_icon_images(uid)
    bg_bytes      = await fetch_profile_bg_bytes(is_prem, uid)
    accent_colors = get_profile_accent(is_prem, uid)
    member_no     = get_member_no(uid)

    gc    = guild_cfg(cfg, target.guild.id)
    level = gc["members_xp"].get(str(uid), {}).get("level", 0)

    joined_str  = target.joined_at.strftime("%b %d, %Y") if target.joined_at else "Unknown"
    created_str = target.created_at.strftime("%b %d, %Y")

    buf = await asyncio.to_thread(
        profile_card.render_profile_card,
        avatar_bytes, target.display_name, member_no, hierarchy_label, hierarchy_color,
        premium_text, is_prem, badge_icons, joined_str, created_str, level,
        bg_bytes, accent_colors
    )
    return discord.File(buf, filename="profile.png")


# ══════════════════════════════════════════════════════════════════
# ANTI SPAM — Cross-channel fingerprint tracker
# ══════════════════════════════════════════════════════════════════

spam_tracker:       dict[tuple, dict[str, dict]] = defaultdict(dict)  # (guild_id, uid) -> fingerprint -> entry
spam_cleanup_times: dict[tuple, float]           = {}
flood_tracker:      dict[tuple, list]             = defaultdict(list)  # (guild_id, uid) -> [timestamps]

def _spam_fingerprint(message: discord.Message) -> str:
    parts: list[str] = []
    text = message.content.strip().lower()
    if text:
        parts.append(text)
    for att in message.attachments:
        parts.append(f"att:{att.filename.lower()}")
    url_pat = re.compile(r"(https?://[^\s]+|discord\.gg/[^\s]+)", re.IGNORECASE)
    for url in url_pat.findall(message.content):
        parts.append(f"url:{url.lower().split('?')[0].rstrip('/')}")
    for emb in message.embeds:
        if emb.url:
            parts.append(f"url:{emb.url.lower().split('?')[0].rstrip('/')}")
    return "|".join(sorted(set(parts))) or "empty"

def _antispam_is_ignored(member: discord.Member, ac: dict) -> bool:
    """True if this member should be skipped from all antispam detection —
    bot owner, manage_guild (staff/admin), or on the manual ignore list."""
    if member.id == bot.owner_id:
        return True
    if member.guild_permissions.manage_guild:
        return True
    if member.id in ac.get("ignore_users", []):
        return True
    role_ids = {r.id for r in member.roles}
    if role_ids & set(ac.get("ignore_roles", [])):
        return True
    return False

async def _antispam_punish(guild: discord.Guild, member: discord.Member, punishment: str, reason: str) -> str:
    """Execute the antispam punishment, return a short string for the log."""
    try:
        if punishment == "kick":
            await guild.kick(member, reason=reason)
            return "**KICKED**"
        elif punishment == "timeout":
            await member.timeout(datetime.timedelta(hours=1), reason=reason)
            return "**TIMEOUT** (1 hour)"
        else:  # ban — default, the harshest option for spam bots/raids
            await guild.ban(member, reason=reason, delete_message_seconds=86400)
            return "**BANNED**"
    except discord.Forbidden:
        return "⚠️ FAILED — bot lacks permission, or its role is lower than the target's"
    except Exception as e:
        logging.error(f"[{BOT_NAME}] Failed to execute antispam punishment: {e}")
        return f"⚠️ FAILED — {e}"

async def _antispam_log(guild: discord.Guild, gc: dict, member: discord.Member, kind: str, detail: str, result: str):
    """Send a report to the antispam log channel (if set) — shared by the
    honeypot, cross-channel spam, and flood detector for consistency."""
    ac     = gc.get("antispam", {})
    log_id = ac.get("log_channel") or _fallback_log_channel(gc)
    if not log_id:
        return
    log_ch = guild.get_channel(log_id)
    if not log_ch:
        return
    emb = base_embed(f"Antispam: {kind}", None, color=COLOR_ERROR)
    emb.add_field(name="User",   value=f"{member.mention} (`{member.id}`)", inline=True)
    emb.add_field(name="Action", value=result, inline=True)
    emb.add_field(name="Detail", value=detail, inline=False)
    emb.set_thumbnail(url=member.display_avatar.url)
    emb.set_footer(text=BOT_NAME)
    try:
        await log_ch.send(embed=emb)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════
# OWNER / PERMISSION HELPERS
# ══════════════════════════════════════════════════════════════════

OWNER_ONLY_CMDS = {"maintenance", "noprefix", "botrole", "grantpremium", "premiumlock", "blacklist", "vxleave", "vxservers", "vxguilds", "ownerhelp", "botstatus", "synccommands", "errorlog"}

def is_owner():
    async def predicate(ctx: commands.Context) -> bool:
        return ctx.author.id == bot.owner_id
    return commands.check(predicate)

def is_moonkeeper(uid: int) -> bool:
    """Independent of the staff hierarchy on purpose — Moonkeeper is a
    standalone permission flag, not a rank on the same ladder as
    staff/moderator/management/etc. That means holding a higher moderation
    tier never silently suppresses Moonkeeper (or vice versa): someone can
    be Management AND Moonkeeper at the same time, and both badges/both
    powers show up. Checked two ways: a manual per-user grant, or a synced
    Discord role in the support server (mirrors how the other tiers sync)."""
    if uid in cfg.get("moonkeeper_users", []):
        return True
    role_id = cfg.get("moonkeeper_sync_role")
    if role_id:
        guild = get_support_guild()
        member = guild.get_member(uid) if guild else None
        if member and any(r.id == role_id for r in member.roles):
            return True
    return False

def can_manage_access(uid: int) -> bool:
    """True for the bot owner, or anyone currently flagged as Moonkeeper
    (see `is_moonkeeper`) — a dedicated, standalone permission for this one
    power, separate from the moderation tiers so day-to-day staff can't
    cascade this access to others. Moonkeepers can grant/revoke no-prefix
    and premium on the owner's behalf, treated exactly as if the owner did
    it. Revoking it is instant and total: `botrole remove @user` for a
    manual grant, or removing the Discord role / `botrole sync remove
    moonkeeper` for a synced one — either way the person loses this power
    immediately, no separate permission list to clean up."""
    if uid == bot.owner_id:
        return True
    return is_moonkeeper(uid)

def is_owner_or_staff():
    async def predicate(ctx: commands.Context) -> bool:
        return can_manage_access(ctx.author.id)
    return commands.check(predicate)

def is_staff_or_above(uid: int) -> bool:
    role = get_bot_role(uid)
    return role in BOT_ROLE_HIERARCHY

@bot.check
async def global_maintenance_check(ctx: commands.Context) -> bool:
    if ctx.author.id == bot.owner_id or not is_maintenance_on():
        return True
    m    = cfg.get("maintenance", {})
    desc = f"**{BOT_NAME}** is under maintenance, please try again later."
    if m.get("reason"):
        desc += f"\n\n**Reason:** {m['reason']}"
    await ctx.send(embed=warning_embed("Under Maintenance", desc), delete_after=10)
    return False

@bot.check
async def global_prefix_premium_check(ctx: commands.Context) -> bool:
    cmd = ctx.command.qualified_name if ctx.command else None
    if not cmd or cmd in OWNER_ONLY_CMDS:
        return True
    if cmd not in cfg.get("premium_commands", []):
        return True
    if ctx.author.id == bot.owner_id:
        return True
    if user_has_premium(ctx.guild, ctx.author):
        return True
    kwargs = {"embed": warning_embed(
        "Premium Required",
        f"Command `{cmd}` is for **Premium** users only.\n"
        "Contact the owner or join the support server to subscribe."
    )}
    view = premium_upsell_view()
    if view:
        kwargs["view"] = view
    await ctx.send(**kwargs)
    return False

# ══════════════════════════════════════════════════════════════════
# MODERATION HELPERS
# ══════════════════════════════════════════════════════════════════

def _is_protected(guild: discord.Guild, member: discord.Member) -> bool:
    """Check whether this member cannot be moderated (guild owner or a role higher than the bot's)."""
    if member.id == guild.owner_id:
        return True
    if guild.me and member.top_role >= guild.me.top_role:
        return True
    return False

async def do_kick(guild, author, member, reason, reply_fn):
    if author.id != bot.owner_id and not author.guild_permissions.kick_members:
        return await reply_fn(embed=error_embed("You don't have permission to use this command."))
    if _is_protected(guild, member):
        return await reply_fn(embed=error_embed("This user can't be kicked."))
    try:
        await member.kick(reason=f"{author} | {reason}")
        await reply_fn(embed=success_embed(f"{member.mention} has been kicked. Reason: {reason}"))
    except discord.Forbidden:
        await reply_fn(embed=error_embed("The bot doesn't have permission to kick."))

async def do_ban(guild, author, member, reason, reply_fn):
    if author.id != bot.owner_id and not author.guild_permissions.ban_members:
        return await reply_fn(embed=error_embed("You don't have permission to use this command."))
    if _is_protected(guild, member):
        return await reply_fn(embed=error_embed("This user can't be banned."))
    try:
        await guild.ban(member, reason=f"{author} | {reason}", delete_message_days=0)
        await reply_fn(embed=success_embed(f"{member.mention} has been banned. Reason: {reason}"))
    except discord.Forbidden:
        await reply_fn(embed=error_embed("The bot doesn't have permission to ban."))

def _parse_timeout_duration(duration: str) -> Optional[datetime.timedelta]:
    """Parse a timeout duration into a timedelta. Accepts a bare number
    (minutes, kept for backwards compatibility with the old `timeout
    @user 60` usage) or a suffixed value: `30s`, `10m`, `2h`, `1d`, `1w`.
    Returns None if the format is invalid or works out to zero/negative.
    Discord itself caps timeouts at 28 days, so anything longer just gets
    clamped down to that instead of failing the command."""
    duration = duration.strip().lower()
    if duration.isdigit():
        amount, unit = int(duration), "m"
    else:
        m = re.fullmatch(r"(\d+)\s*(s|m|h|d|w)", duration)
        if not m:
            return None
        amount, unit = int(m.group(1)), m.group(2)
    if amount <= 0:
        return None
    delta = {
        "s": datetime.timedelta(seconds=amount),
        "m": datetime.timedelta(minutes=amount),
        "h": datetime.timedelta(hours=amount),
        "d": datetime.timedelta(days=amount),
        "w": datetime.timedelta(weeks=amount),
    }[unit]
    return min(delta, datetime.timedelta(days=28))

async def do_timeout(guild, author, member, duration, reason, reply_fn):
    if author.id != bot.owner_id and not author.guild_permissions.moderate_members:
        return await reply_fn(embed=error_embed("You don't have permission to use this command."))
    if _is_protected(guild, member):
        return await reply_fn(embed=error_embed("This user can't be timed out."))
    delta = _parse_timeout_duration(str(duration))
    if not delta:
        return await reply_fn(embed=error_embed(
            "Invalid duration. Use a plain number of minutes, or a suffixed value like "
            "`30s`, `10m`, `2h`, `1d`, `1w` (max 28 days)."
        ))
    try:
        until = discord.utils.utcnow() + delta
        await member.timeout(until, reason=f"{author} | {reason}")
        await reply_fn(embed=success_embed(
            f"{member.mention} has been timed out until {discord.utils.format_dt(until, 'f')} "
            f"({discord.utils.format_dt(until, 'R')})."
        ))
    except discord.Forbidden:
        await reply_fn(embed=error_embed("The bot doesn't have permission to timeout members."))

async def do_warn(guild, author, member, reason, reply_fn):
    if author.id != bot.owner_id and not author.guild_permissions.manage_messages:
        return await reply_fn(embed=error_embed("You don't have permission to use this command."))
    gc = guild_cfg(cfg, guild.id)
    gc.setdefault("warnings", {}).setdefault(str(member.id), []).append({
        "reason": reason, "warned_by": author.id,
        "timestamp": discord.utils.utcnow().isoformat()
    })
    save_config(cfg)
    try:
        dm = base_embed(f"You were warned in {guild.name}", f"Reason: {reason}", color=COLOR_WARNING)
        await member.send(embed=dm)
    except Exception:
        pass
    await reply_fn(embed=success_embed(f"{member.mention} has been warned. Reason: {reason}"))

async def do_addrole(guild, author, member, role, reply_fn):
    if author.id != bot.owner_id and not author.guild_permissions.manage_roles:
        return await reply_fn(embed=error_embed("You don't have permission to use this command."))
    try:
        await member.add_roles(role, reason=f"By {author}")
        await reply_fn(embed=success_embed(f"Role {role.name} added to {member.mention}."))
    except discord.Forbidden:
        await reply_fn(embed=error_embed("The bot doesn't have permission to manage roles."))

async def do_removerole(guild, author, member, role, reply_fn):
    if author.id != bot.owner_id and not author.guild_permissions.manage_roles:
        return await reply_fn(embed=error_embed("You don't have permission to use this command."))
    try:
        await member.remove_roles(role, reason=f"By {author}")
        await reply_fn(embed=success_embed(f"Role {role.name} removed from {member.mention}."))
    except discord.Forbidden:
        await reply_fn(embed=error_embed("The bot doesn't have permission to manage roles."))

async def do_move(guild, author, member, channel, reply_fn):
    if author.id != bot.owner_id and not author.guild_permissions.move_members:
        return await reply_fn(embed=error_embed("You don't have permission to use this command."))
    try:
        await member.move_to(channel, reason=f"By {author}")
        await reply_fn(embed=success_embed(f"{member.mention} moved to {channel.name}."))
    except discord.Forbidden:
        await reply_fn(embed=error_embed("The bot doesn't have permission to move members."))

async def do_userinfo(guild, member, reply_fn):
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"][:10]
    embed = discord.Embed(title=f"{member.display_name}", color=member.color or COLOR_PRIMARY, timestamp=discord.utils.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Username",  value=str(member),                                              inline=True)
    embed.add_field(name="ID",        value=f"`{member.id}`",                                         inline=True)
    embed.add_field(name="Bot",       value="Yes" if member.bot else "No",                             inline=True)
    embed.add_field(name="Joined",    value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "?", inline=True)
    embed.add_field(name="Created",   value=discord.utils.format_dt(member.created_at, "R"),           inline=True)
    embed.add_field(name="Roles",     value=" ".join(roles) if roles else "None",                      inline=False)
    embed.set_footer(text=f"{BOT_NAME} • {guild.name}")
    await reply_fn(embed=embed)

async def do_avatar(member, reply_fn):
    embed = discord.Embed(title=f"{member.display_name}'s Avatar", color=COLOR_PRIMARY)
    embed.set_image(url=member.display_avatar.url)
    embed.set_footer(text=BOT_NAME)

    # Link buttons so the person can grab a full-res static copy straight
    # away, without needing to right-click -> Open Image -> Save manually.
    avatar = member.display_avatar
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Download PNG", style=discord.ButtonStyle.link,
        url=avatar.with_format("png").with_size(1024).url
    ))
    view.add_item(discord.ui.Button(
        label="Download JPG", style=discord.ButtonStyle.link,
        url=avatar.with_format("jpg").with_size(1024).url
    ))
    await reply_fn(embed=embed, view=view)

async def do_ping(reply_fn):
    lat = round(bot.latency * 1000)
    embed = base_embed("Pong!", f"Latency: **{lat}ms**", COLOR_SUCCESS if lat < 100 else COLOR_WARNING)
    await reply_fn(embed=embed)

AFK_FREE_SLOTS = 5  # max concurrently-AFK members per server without voting

async def do_afk_set(guild: discord.Guild, author: discord.abc.User, reason: str, reply_fn):
    """Mark `author` as AFK in this guild. Any message they send afterwards
    (other than re-running `afk`) automatically clears it — see on_message.
    Anyone who @mentions them while AFK gets an embed with their reason.

    Only AFK_FREE_SLOTS members can be AFK at once PER SERVER. Updating an
    already-AFK member's reason never consumes a new slot. Once full, a
    NEW member can still use AFK only if they've voted on top.gg in the
    last ~12h (vote_system's cooldown window — voting temporarily unlocks
    the feature past the cap).
    """
    gc      = guild_cfg(cfg, guild.id)
    afk_map = gc.setdefault("afk_users", {})
    uid_key = str(author.id)

    if uid_key not in afk_map and len(afk_map) >= AFK_FREE_SLOTS:
        voted_recently = TOPGG_VOTE_URL and vote_system.next_vote_time(cfg.get("votes", {}), uid_key) is not None
        if not voted_recently:
            embed = error_embed(
                f"AFK is full on this server ({AFK_FREE_SLOTS}/{AFK_FREE_SLOTS} slots in use). "
                + ("Vote for the bot to unlock it temporarily!" if TOPGG_VOTE_URL else "Ask an admin to free up a slot.")
            )
            kwargs = {"embed": embed}
            if TOPGG_VOTE_URL:
                view = discord.ui.View()
                view.add_item(discord.ui.Button(label="Vote", style=discord.ButtonStyle.link, url=TOPGG_VOTE_URL))
                kwargs["view"] = view
            return await reply_fn(**kwargs)

    reason   = (reason or "").strip()[:200] or "AFK"
    since_ts = int(discord.utils.utcnow().timestamp())
    already_afk = uid_key in afk_map

    # Prefix their nickname with "[AFK] " so it's visible at a glance in the
    # member list / chat, not just when someone mentions them. We store the
    # PRE-AFK nick (None means "no custom nick, was just using their
    # username") so it can be restored exactly when they come back — see
    # the matching restore in on_message. Captured ONLY on a fresh AFK (not
    # when they're just updating their reason while already AFK — at that
    # point display_name is already "[AFK] ...", so re-capturing here would
    # clobber the real original nick with the prefixed one). Silently
    # skipped if the bot can't manage this member's nickname (they outrank
    # the bot, they're the server owner, missing Manage Nicknames, etc.) —
    # AFK still works fine without it, this is just a nice-to-have.
    member = guild.get_member(author.id) or author
    original_nick = afk_map.get(uid_key, {}).get("original_nick") if already_afk else getattr(member, "nick", None)
    if not already_afk and isinstance(member, discord.Member):
        try:
            new_nick = f"[AFK] {member.display_name}"[:32]
            await member.edit(nick=new_nick, reason="AFK status set")
        except (discord.Forbidden, discord.HTTPException):
            pass

    afk_map[uid_key] = {"reason": reason, "since": since_ts, "original_nick": original_nick}
    save_config(cfg)

    embed = base_embed(
        _title_with_icon(ICON_AFK, "💤", "AFK Notice"),
        "-# Sending any message will automatically clear this status.",
        color=COLOR_PRIMARY
    )
    embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
    embed.add_field(name="Reason", value=reason, inline=True)
    embed.add_field(name="Duration", value=f"<t:{since_ts}:R>", inline=True)
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    await reply_fn(embed=embed)

async def do_addemoji(guild, emoji_or_url: str, name: str):
    import aiohttp, io
    try:
        if emoji_or_url.startswith("<") and ":" in emoji_or_url:
            parts  = emoji_or_url.strip("<>").split(":")
            eid    = parts[-1]
            ext    = "gif" if emoji_or_url.startswith("<a:") else "png"
            url    = f"https://cdn.discordapp.com/emojis/{eid}.{ext}"
            name   = name or parts[-2]
        else:
            url = emoji_or_url
        if not name:
            return {"success": False, "error": "An emoji name is required."}
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return {"success": False, "error": f"Failed to fetch image: HTTP {r.status}"}
                data = await r.read()
        emoji = await guild.create_custom_emoji(name=name, image=data)
        return {"success": True, "emoji": emoji}
    except discord.Forbidden:
        return {"success": False, "error": "The bot doesn't have permission to manage emojis."}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ══════════════════════════════════════════════════════════════════
# TICKET HANDLER
# ══════════════════════════════════════════════════════════════════

def _fallback_log_channel(gc: dict) -> Optional[int]:
    """Used by the general honeypot/log system when mod_log_channel isn't set —
    borrows the log channel from the antispam config, then falls back to the
    first ticket panel that has one."""
    if gc.get("antispam", {}).get("log_channel"):
        return gc["antispam"]["log_channel"]
    if gc.get("mod_log_channel"):
        return gc["mod_log_channel"]
    for p in gc["ticket"]["panels"].values():
        if p.get("log_channel"):
            return p["log_channel"]
    return None

def _find_active_ticket(gc: dict, channel_id: int):
    """Find an active ticket by channel_id.
    Returns (uid_str, ticket_dict, panel_dict) or (None, None, None)."""
    for uid, tickets in gc["active_tickets"].items():
        for tk in tickets:
            if tk.get("channel_id") == channel_id:
                panel = gc["ticket"]["panels"].get(tk.get("panel_id"), {})
                return uid, tk, panel
    return None, None, None

async def handle_open_ticket(interaction: discord.Interaction, panel_id: str, type_key: str = None):
    gc    = guild_cfg(cfg, interaction.guild.id)
    panel = gc["ticket"]["panels"].get(panel_id)
    uid   = str(interaction.user.id)

    if not panel or not (panel.get("category") or panel.get("types")):
        return await interaction.response.send_message(
            embed=error_embed("This ticket panel hasn't been configured properly."), ephemeral=True)

    type_cfg = ticket_types.get_type_config(panel, type_key)
    if not type_cfg.get("category"):
        return await interaction.response.send_message(
            embed=error_embed("This ticket type hasn't been configured with a category yet."), ephemeral=True)

    tickets    = gc["active_tickets"].setdefault(uid, [])
    # Tracked per (panel, type) so different ticket types under the same
    # panel each get their own max-open-tickets limit, instead of sharing
    # one counter across totally different categories.
    scope_key  = f"{panel_id}:{type_key}" if type_key else panel_id
    same_scope = [tk for tk in tickets if tk.get("scope_key", tk.get("panel_id")) == scope_key and interaction.guild.get_channel(tk.get("channel_id"))]
    max_t      = type_cfg.get("max_tickets", 1)
    if len(same_scope) >= max_t:
        ch  = interaction.guild.get_channel(same_scope[0]["channel_id"]) if same_scope else None
        msg = "You already have an open ticket." if max_t == 1 else \
            f"You already have {len(same_scope)}/{max_t} open tickets for **{type_cfg['label']}**."
        if ch:
            msg += f"\n{ch.mention}"
        return await interaction.response.send_message(embed=error_embed(msg), ephemeral=True)

    category = interaction.guild.get_channel(type_cfg["category"])
    if not category:
        return await interaction.response.send_message(
            embed=error_embed("Ticket category not found."), ephemeral=True)

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user:               discord.PermissionOverwrite(view_channel=True, send_messages=True),
        interaction.guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    role_id = type_cfg.get("support_role")
    if role_id:
        role = interaction.guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    ch = await category.create_text_channel(
        name=f"ticket-{interaction.user.name}",
        overwrites=overwrites,
        topic=f"Ticket [{panel_id}/{type_key or '-'}] for {interaction.user} ({interaction.user.id})"
    )
    tickets.append({
        "channel_id": ch.id,
        "panel_id":   panel_id,
        "type_key":   type_key,
        "scope_key":  scope_key,
        "opened_at":  discord.utils.utcnow().isoformat(),
        "claimed_by": None,
    })
    save_config(cfg)

    welcome_text = (panel.get("welcome_message") or "Thanks for reaching out, {user}! Our support team will be with you shortly.")
    welcome_text = (welcome_text
                    .replace("{user}",   interaction.user.mention)
                    .replace("{server}", interaction.guild.name)
                    .replace("{panel}",  panel.get("title") or panel_id)
                    .replace("{type}",   type_cfg["label"]))
    welcome_embed = base_embed(
        type_cfg["label"] if type_key else (panel.get("title") or f"Ticket — {interaction.user.display_name}"),
        welcome_text,
        color=panel.get("color") or COLOR_PRIMARY
    )
    await ch.send(content=interaction.user.mention, embed=welcome_embed, view=TicketControlView())

    log_id = type_cfg.get("log_channel")
    if log_id:
        log_ch = interaction.guild.get_channel(log_id)
        if log_ch:
            log_emb = base_embed("Ticket Opened", None, color=COLOR_PRIMARY)
            log_emb.add_field(name="User",    value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=True)
            log_emb.add_field(name="Channel", value=ch.mention, inline=True)
            log_emb.add_field(name="Type",    value=type_cfg["label"], inline=True)
            try:
                await log_ch.send(embed=log_emb)
            except Exception:
                pass

    await interaction.response.send_message(
        embed=success_embed(f"Your ticket has been created: {ch.mention}"),
        ephemeral=True
    )

async def close_ticket_channel(guild: discord.Guild, channel: discord.abc.GuildChannel,
                                closer: discord.abc.User, reason: str, send_confirmation) -> bool:
    """Core ticket-closing logic, shared by the Close button and the `ticket close` command.
    The log is sent to the log channel belonging to the ticket's PANEL, not a global log channel."""
    gc = guild_cfg(cfg, guild.id)
    uid, tk, panel = _find_active_ticket(gc, channel.id)
    if not tk:
        await send_confirmation(embed=error_embed("This channel isn't an active ticket."))
        return False

    is_owner_  = closer.id == bot.owner_id
    can_manage = getattr(closer, "guild_permissions", None) and closer.guild_permissions.manage_channels
    if not (is_owner_ or can_manage or str(closer.id) == uid):
        await send_confirmation(embed=error_embed("You can't close this ticket."))
        return False

    gc["active_tickets"][uid] = [x for x in gc["active_tickets"][uid] if x.get("channel_id") != channel.id]
    if not gc["active_tickets"][uid]:
        del gc["active_tickets"][uid]
    save_config(cfg)

    reason = reason or "Closed via command."
    await send_confirmation(embed=base_embed(
        "Ticket Closing", f"Closed by {closer.mention}.\n{reason}\n\nChannel will be deleted in 5 seconds.", color=COLOR_ERROR))

    log_id = panel.get("log_channel")
    if log_id:
        log_ch = guild.get_channel(log_id)
        if log_ch:
            duration_str = "?"
            try:
                opened_dt = datetime.datetime.fromisoformat(tk.get("opened_at"))
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=datetime.timezone.utc)
                mins = int((discord.utils.utcnow() - opened_dt).total_seconds() // 60)
                duration_str = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
            except Exception:
                pass
            owner_member = guild.get_member(int(uid))
            claimed_by   = tk.get("claimed_by")
            claimer      = guild.get_member(claimed_by) if claimed_by else None
            log_emb = base_embed("Ticket Closed", None, color=COLOR_ERROR)
            log_emb.add_field(name="Ticket Owner", value=f"{owner_member.mention if owner_member else '<@'+uid+'>'} (`{uid}`)", inline=True)
            log_emb.add_field(name="Closed By",    value=closer.mention, inline=True)
            log_emb.add_field(name="Claimed By",   value=claimer.mention if claimer else "*(unclaimed)*", inline=True)
            log_emb.add_field(name="Panel",        value=panel.get("title") or tk.get("panel_id", "?"), inline=True)
            log_emb.add_field(name="Duration",     value=duration_str, inline=True)
            log_emb.add_field(name="Reason",       value=reason, inline=False)
            try:
                await log_ch.send(embed=log_emb)
            except Exception:
                pass

    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {closer}")
    except Exception:
        pass
    return True

# ══════════════════════════════════════════════════════════════════
# VERIFICATION SYSTEM — captcha gate for new members
# ══════════════════════════════════════════════════════════════════
# Fully opt-in per guild: nothing happens on join until an admin sets a
# channel + an Unverified role + a Verified role, then runs
# `verification enable`. Until then this whole system stays dormant.
#
# Pending captchas are kept in memory only (a few minutes at most) —
# same reasoning as antinuke's sliding-window tracker: short-lived by
# nature, no benefit to persisting them across a bot restart.

_PENDING_CAPTCHAS: dict = {}   # uid -> {"code","guild_id","expires","attempts"}
CAPTCHA_TTL     = 300   # seconds a generated code stays valid
CAPTCHA_MAX_TRY = 3     # wrong guesses allowed before a fresh code is required

async def _apply_unverified_role(member: discord.Member):
    """Hooked into on_member_join for every guild the bot is in — a no-op
    unless that specific guild has verification configured and enabled."""
    gc = guild_cfg(cfg, member.guild.id)
    vc = gc.get("verification", {})
    if not vc.get("enabled"):
        return
    role = member.guild.get_role(vc.get("unverified_role_id") or 0)
    if not role:
        return
    try:
        await member.add_roles(role, reason="Verification system — pending captcha")
    except (discord.Forbidden, discord.HTTPException):
        pass

async def _complete_verification(member: discord.Member, gc: dict) -> bool:
    """Swap Unverified -> Verified and post an optional log entry. Returns
    False if the Verified role is missing/unassignable so the caller can
    tell the member to ping staff instead of silently doing nothing."""
    vc         = gc.get("verification", {})
    ver_role   = member.guild.get_role(vc.get("verified_role_id") or 0)
    unver_role = member.guild.get_role(vc.get("unverified_role_id") or 0)
    if not ver_role:
        return False
    try:
        if ver_role not in member.roles:
            await member.add_roles(ver_role, reason="Verification system — captcha passed")
        if unver_role and unver_role in member.roles:
            await member.remove_roles(unver_role, reason="Verification system — captcha passed")
    except (discord.Forbidden, discord.HTTPException):
        return False

    log_ch = member.guild.get_channel(vc.get("log_channel_id") or 0)
    if log_ch:
        emb = base_embed(f"{e(ICON_VERIFICATION, '🔐')} Member Verified", f"{member.mention} completed the captcha and was verified.", color=COLOR_SUCCESS)
        emb.set_thumbnail(url=member.display_avatar.url)
        emb.set_footer(text=BOT_NAME)
        try:
            await log_ch.send(embed=emb)
        except Exception:
            pass
    return True

def _verification_result_embed(user: discord.abc.User, success: bool, gc: dict) -> discord.Embed:
    """Detailed result card shown after a verification attempt concludes
    (either passed, or exhausted its attempts/expired) — username, a clear
    pass/fail status, the exact time, and the server's custom message.
    Always dark red regardless of outcome, per how this bot's brand embeds
    are themed."""
    vc     = gc.get("verification", {})
    words  = vc.get("result_message") or "Thanks for verifying — enjoy your stay!"
    now_ts = int(discord.utils.utcnow().timestamp())
    embed  = discord.Embed(
        title=f"{e(ICON_VERIFICATION, '🔐')} Verification Result",
        color=COLOR_PRIMARY,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Username", value=str(user), inline=True)
    embed.add_field(name="Status", value="✅ Verified" if success else "❌ Failed", inline=True)
    embed.add_field(name="Verification Time", value=f"<t:{now_ts}:F>", inline=False)
    embed.add_field(name="Message", value=words, inline=False)
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text=BOT_NAME)
    return embed

class CaptchaModal(discord.ui.Modal, title="Enter the Verification Code"):
    code_input = discord.ui.TextInput(
        label="Type the code shown in the image",
        placeholder="e.g. NHR3K4",
        min_length=4, max_length=8, required=True
    )

    def __init__(self):
        super().__init__(timeout=180)

    async def on_submit(self, interaction: discord.Interaction):
        pending = _PENDING_CAPTCHAS.get(interaction.user.id)
        if not pending:
            return await interaction.response.send_message(
                embed=error_embed("That code expired or wasn't found — go back to the server and click **Verify** again to get a new one.")
            )

        # This modal is submitted from a DM, where interaction.guild is
        # always None — the guild/member have to be resolved from what we
        # captured back when the Verify button was first clicked.
        guild = bot.get_guild(pending["guild_id"])
        if not guild:
            _PENDING_CAPTCHAS.pop(interaction.user.id, None)
            return await interaction.response.send_message(
                embed=error_embed("Couldn't reach that server anymore — please try again from the server.")
            )
        member = guild.get_member(interaction.user.id)
        if not member:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except discord.NotFound:
                member = None
        if not member:
            _PENDING_CAPTCHAS.pop(interaction.user.id, None)
            return await interaction.response.send_message(
                embed=error_embed("You don't appear to be a member of that server anymore.")
            )
        gc = guild_cfg(cfg, guild.id)

        if time.monotonic() > pending["expires"]:
            _PENDING_CAPTCHAS.pop(interaction.user.id, None)
            return await interaction.response.send_message(embed=_verification_result_embed(member, False, gc), view=invite_support_view())
        if self.code_input.value.strip().upper() != pending["code"]:
            pending["attempts"] += 1
            if pending["attempts"] >= CAPTCHA_MAX_TRY:
                _PENDING_CAPTCHAS.pop(interaction.user.id, None)
                return await interaction.response.send_message(embed=_verification_result_embed(member, False, gc), view=invite_support_view())
            left = CAPTCHA_MAX_TRY - pending["attempts"]
            return await interaction.response.send_message(
                embed=error_embed(f"That's not quite right. **{left}** attempt(s) left before you'll need a new code.")
            )

        _PENDING_CAPTCHAS.pop(interaction.user.id, None)
        ok = await _complete_verification(member, gc)
        if ok:
            await interaction.response.send_message(embed=_verification_result_embed(member, True, gc), view=invite_support_view())
        else:
            await interaction.response.send_message(
                embed=error_embed(
                    "The Verified role couldn't be applied — the bot may be missing permissions, its role "
                    "may be positioned too low, or the role was deleted. Please ping staff for help."
                )
            )

class CaptchaEnterView(discord.ui.View):
    """One-off view attached to a single captcha image sent via DM — not
    persistent (unlike VerificationView below), since it's only ever
    valid for the short life of that specific code anyway."""
    def __init__(self):
        super().__init__(timeout=CAPTCHA_TTL)

    @discord.ui.button(label="Enter Code", style=discord.ButtonStyle.success, emoji="⌨️")
    async def enter_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        pending = _PENDING_CAPTCHAS.get(interaction.user.id)
        if not pending:
            return await interaction.response.send_message(
                embed=error_embed("This code expired. Go back to the server and click **Verify** again.")
            )
        await interaction.response.send_modal(CaptchaModal())

class VerificationView(discord.ui.View):
    """Persistent, static view — one shared custom_id, re-registered via
    bot.add_view() in on_ready so the button keeps working across bot
    restarts (same pattern as TicketControlView below)."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.secondary, custom_id="vx_verify_start")
    async def verify_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        gc = guild_cfg(cfg, interaction.guild.id)
        vc = gc.get("verification", {})
        if not vc.get("enabled"):
            return await interaction.response.send_message(
                embed=error_embed("Verification isn't set up on this server."), ephemeral=True
            )
        ver_role = interaction.guild.get_role(vc.get("verified_role_id") or 0)
        if ver_role and ver_role in interaction.user.roles:
            return await interaction.response.send_message(
                embed=info_embed("Already Verified", "You're already verified — no need to do this again!"),
                ephemeral=True
            )

        code = rank_card.generate_captcha_code()
        img  = rank_card.render_captcha_image(code)
        file = discord.File(img, filename="captcha.png")
        embed = base_embed(
            f"{e(ICON_VERIFICATION, '🔐')} Verify You're Human",
            "Type the code shown below, then hit **Enter Code**.\n"
            f"-# This code expires in {CAPTCHA_TTL // 60} minutes. Sent because you clicked Verify in "
            f"**{interaction.guild.name}**.",
            color=COLOR_PRIMARY
        )
        embed.set_image(url="attachment://captcha.png")

        # DM-only by design — keeps the captcha off a public/verifiable
        # channel entirely, out of reach of anything scraping messages in
        # the server itself.
        try:
            await interaction.user.send(embed=embed, file=file, view=CaptchaEnterView())
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed(
                    "I couldn't DM you the captcha — please enable **Direct Messages from server members** "
                    "in your Privacy Settings for this server, then click **Verify** again."
                ),
                ephemeral=True
            )

        _PENDING_CAPTCHAS[interaction.user.id] = {
            "code": code, "guild_id": interaction.guild.id,
            "expires": time.monotonic() + CAPTCHA_TTL, "attempts": 0
        }
        await interaction.response.send_message(
            embed=success_embed("Check your DMs — I've sent you a captcha to complete verification."),
            ephemeral=True
        )

class TicketControlView(discord.ui.View):
    """Persistent, static view — one custom_id shared by every ticket, safe to use
    across bot restarts via bot.add_view() in on_ready. Holds both Claim and
    Close so staff always have both actions in the same place."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary,
                        emoji="🙋", custom_id="vx_ticket_claim")
    async def claim_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        gc = guild_cfg(cfg, interaction.guild.id)
        uid, tk, panel = _find_active_ticket(gc, interaction.channel.id)
        if not tk:
            return await interaction.response.send_message(embed=error_embed("This channel isn't an active ticket."), ephemeral=True)

        claimed_by = tk.get("claimed_by")
        if claimed_by:
            # Already claimed. Re-lock the button on this message too, in case
            # it's showing stale/clickable state (e.g. a bot restart happened
            # before it got edited) — nobody, including the original claimer,
            # should ever be able to click this again once it's claimed.
            claimer = interaction.guild.get_member(claimed_by)
            btn.disabled = True
            btn.style    = discord.ButtonStyle.secondary
            btn.label    = f"Claimed by {claimer.display_name}" if claimer else "Claimed"
            try:
                await interaction.response.edit_message(view=self)
            except discord.InteractionResponded:
                pass
            return await interaction.followup.send(
                embed=error_embed(f"This ticket is already claimed by {claimer.mention if claimer else 'someone else'}."),
                ephemeral=True
            )

        is_owner_ = interaction.user.id == bot.owner_id
        role_id   = panel.get("support_role")
        has_role  = bool(role_id and interaction.guild.get_role(role_id) in interaction.user.roles)
        can_claim = is_owner_ or interaction.user.guild_permissions.manage_channels or has_role
        if not can_claim:
            return await interaction.response.send_message(embed=error_embed("Only support staff can claim tickets."), ephemeral=True)

        tk["claimed_by"] = interaction.user.id
        save_config(cfg)

        # One claim, permanently — disable + relabel the button right away so
        # it can never be pressed again by anyone, staff or otherwise.
        btn.disabled = True
        btn.style    = discord.ButtonStyle.secondary
        btn.label    = f"Claimed by {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=success_embed(f"🙋 Ticket claimed by {interaction.user.mention} — they'll be handling this from here."))

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger,
                        emoji=ICON_TICKET_CLOSE if ICON_TICKET_CLOSE else "🔒",
                        custom_id="vx_ticket_close")
    async def close_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        async def _respond(**kw):
            try:
                await interaction.response.send_message(**kw)
            except discord.InteractionResponded:
                await interaction.followup.send(**kw)
        await close_ticket_channel(interaction.guild, interaction.channel, interaction.user, "Closed via button.", _respond)

BUTTON_STYLES = {
    "primary":   discord.ButtonStyle.primary,     # blurple
    "secondary": discord.ButtonStyle.secondary,   # gray
    "success":   discord.ButtonStyle.success,      # green
    "danger":    discord.ButtonStyle.danger,       # red
}

def _build_ticket_panel_embed(panel: dict) -> discord.Embed:
    """Shared by the prefix `ticket panel`/`ticket edit` commands, the
    `/ticketpanel` builder's live preview, and the final posted message —
    one source of truth so all three can never drift out of sync."""
    embed = discord.Embed(
        title=panel.get("title") or "Support Tickets",
        description=panel.get("description") or "Click the button below to open a support ticket.",
        color=panel.get("color") or COLOR_PRIMARY,
        timestamp=discord.utils.utcnow()
    )
    if panel.get("thumbnail"):
        embed.set_thumbnail(url=panel["thumbnail"])
    if panel.get("image"):
        embed.set_image(url=panel["image"])
    embed.set_footer(text=BOT_NAME)
    return embed

class TicketPanelLayout(discord.ui.LayoutView):
    """Components V2 rendering of the LIVE ticket panel message — title,
    description, thumbnail, banner, and the open control (button or
    dropdown) all live in ONE Container, replacing the old
    _build_ticket_panel_embed() + legacy embed/View combo for whatever
    actually gets posted/edited in a server channel. The `/ticketpanel`
    builder's own ephemeral preview is a separate admin-only tool and
    still uses the plain-embed _ticket_render_kwargs — this class is only
    for the final send/edit call sites.

    Three possible open-control layouts, checked in order (same logic the
    old view used):
    1. Multi-category dropdown — if the panel has 2+ configured `types`
       (see ticket_types.py), one option per type; each opens with that
       type's own category/log/role.
    2. Single-option dropdown — `open_type` is "dropdown" but no real
       types configured; a purely cosmetic choice (still opens the panel's
       one category).
    3. Plain button — the classic default.

    Persistent custom_id scheme unchanged, so bot.add_view() in on_ready
    keeps it working after a restart."""
    def __init__(self, panel_id: str, panel: dict = None, guild: discord.Guild = None):
        super().__init__(timeout=None)
        self.panel_id = panel_id
        panel = panel or {}

        title       = panel.get("title") or "Support Tickets"
        description = panel.get("description") or "Click the button below to open a support ticket."
        color       = panel.get("color") or COLOR_PRIMARY
        thumbnail   = panel.get("thumbnail")
        banner      = panel.get("image")

        label     = panel.get("button_label") or "Open Ticket"
        emoji     = ticket_types.safe_emoji_for_guild(guild, panel.get("button_emoji")) or (ICON_TICKET_OPEN if ICON_TICKET_OPEN else "🎫")
        style     = BUTTON_STYLES.get(panel.get("button_style"), discord.ButtonStyle.danger)
        open_type = panel.get("open_type", "button")

        text_parts = [heading_md(title), description]
        content_item = (
            discord.ui.Section(*text_parts, accessory=discord.ui.Thumbnail(thumbnail))
            if thumbnail else discord.ui.TextDisplay("\n\n".join(text_parts))
        )

        row = discord.ui.ActionRow()
        type_options = ticket_types.build_type_select_options(panel, guild=guild)
        if type_options:
            select = discord.ui.Select(
                placeholder=label, options=type_options,
                custom_id=f"vx_ticket_open_select:{panel_id}",
                min_values=1, max_values=1,
            )
            select.callback = self._open_callback
            row.add_item(select)
        elif open_type == "dropdown":
            select = discord.ui.Select(
                placeholder=label,
                options=[discord.SelectOption(label=label, value="open", emoji=emoji or None, description="Select to open a ticket")],
                custom_id=f"vx_ticket_open_select:{panel_id}",
                min_values=1, max_values=1,
            )
            select.callback = self._open_callback
            row.add_item(select)
        else:
            btn = discord.ui.Button(label=label, style=style, emoji=emoji or None, custom_id=f"vx_ticket_open:{panel_id}")
            btn.callback = self._open_callback
            row.add_item(btn)

        items = [content_item]
        if banner:
            items.append(discord.ui.Separator())
            items.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner)))
        items.append(discord.ui.Separator())
        items.append(row)

        self.add_item(discord.ui.Container(*items, accent_color=discord.Color(color)))

    async def _open_callback(self, interaction: discord.Interaction):
        type_key = None
        if isinstance(interaction.data, dict):
            values = interaction.data.get("values")
            if values and values[0] != "open":
                type_key = values[0]
        await handle_open_ticket(interaction, self.panel_id, type_key)

# ══════════════════════════════════════════════════════════════════
# GIVEAWAY SYSTEM
# ══════════════════════════════════════════════════════════════════

import random
active_giveaways: dict[int, dict] = {}

def save_giveaways():
    """Mirror the in-memory active_giveaways dict to disk (cfg["active_giveaways"],
    keyed by message_id as a string since JSON keys must be strings) — called
    after every change (create, entry add/remove, end) so a giveaway survives
    a bot restart instead of vanishing silently the moment the process bounces."""
    cfg["active_giveaways"] = {str(mid): gw for mid, gw in active_giveaways.items() if not gw.get("ended")}
    save_config(cfg)

def schedule_giveaway_end(gw: dict):
    """Schedule (or reschedule, after a restart) the timer that ends this
    giveaway, based on its stored ends_ts rather than a fixed duration —
    so a giveaway that had, say, 3 hours left when the bot restarted still
    ends 3 hours from now, not from whenever the process happened to come
    back up. Safe to call more than once for the same giveaway: end_giveaway()
    is a no-op once gw['ended'] is True."""
    remaining = gw["ends_ts"] - discord.utils.utcnow().timestamp()
    async def _timer():
        if remaining > 0:
            await asyncio.sleep(remaining)
        current = active_giveaways.get(gw["message_id"])
        if current:
            await end_giveaway(current)
    asyncio.create_task(_timer())

_giveaways_restored = False

async def restore_giveaways():
    """Called once from on_ready — reloads every still-active giveaway from
    disk into active_giveaways and reschedules (or, if the bot was down past
    the deadline, immediately runs) its end timer. Without this, anything
    saved by save_giveaways() would load back into memory on the next
    save_giveaways() call but never actually re-arm its end timer, so it'd
    just sit there forever never ending.

    Guarded to run only once per process: on_ready can fire again on a
    gateway reconnect (not just a fresh process start), and re-restoring
    then would schedule a second, redundant end-timer for every giveaway
    still running."""
    global _giveaways_restored
    if _giveaways_restored:
        return
    _giveaways_restored = True
    stored = cfg.get("active_giveaways", {})
    if not stored:
        return
    restored = 0
    for mid_str, gw in list(stored.items()):
        if gw.get("ended"):
            continue
        try:
            mid = int(mid_str)
        except (TypeError, ValueError):
            continue
        gw["message_id"] = mid
        active_giveaways[mid] = gw
        schedule_giveaway_end(gw)
        bot.add_view(GiveawayView(mid))
        restored += 1
    if restored:
        logging.info(f"[{BOT_NAME}] Restored {restored} active giveaway(s) from disk.")

def build_giveaway_embed(gw: dict) -> discord.Embed:
    ends_dt = datetime.datetime.utcfromtimestamp(gw["ends_ts"]).replace(tzinfo=datetime.timezone.utc)
    entry_count = len(set(gw.get("entries", [])))
    embed   = discord.Embed(
        title=f"🎉 {gw['prize']}",
        description=(
            (gw.get("description", "") + "\n\n" if gw.get("description") else "") +
            "Click **Join Giveaway** below to enter!\n\n"
            f"**Winners:** {gw['winner_count']}\n"
            f"**Entries:** {entry_count}\n"
            f"**Ends:** {discord.utils.format_dt(ends_dt, 'R')}\n"
            f"**Hosted by:** <@{gw['host_id']}>"
        ),
        color=COLOR_ERROR,
        timestamp=ends_dt
    )
    if gw.get("required_role"):
        embed.add_field(name="Required Role", value=f"<@&{gw['required_role']}>", inline=True)
    if gw.get("winner_role_id"):
        embed.add_field(name="Winner Role", value=f"<@&{gw['winner_role_id']}>", inline=True)
    embed.set_footer(text=f"{BOT_NAME} Giveaway")
    return embed

class GiveawayView(discord.ui.View):
    """Persistent, per-giveaway view — the message_id is baked into each
    button's custom_id (buttons are built by hand in __init__ instead of
    the @discord.ui.button decorator, since that only supports a fixed
    custom_id at class-definition time and we need a different one per
    giveaway). Re-registered for every still-active giveaway on startup
    via restore_giveaways(), so Join/Participants keep working across a
    bot restart exactly like the giveaway timer itself does."""
    def __init__(self, message_id: int, ended: bool = False):
        super().__init__(timeout=None)
        self.message_id = message_id

        join_btn = discord.ui.Button(
            label="Join Giveaway", emoji=(ICON_GIVEAWAY_REACT if ICON_GIVEAWAY_REACT else "🎉"),
            style=discord.ButtonStyle.danger,
            custom_id=f"vx_gw_join:{message_id}",
            disabled=ended,
        )
        join_btn.callback = self.join_callback
        self.add_item(join_btn)

        participants_btn = discord.ui.Button(
            label="Participants", emoji=(ICON_GIVEAWAY_PARTICIPANTS if ICON_GIVEAWAY_PARTICIPANTS else "👥"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"vx_gw_participants:{message_id}",
        )
        participants_btn.callback = self.participants_callback
        self.add_item(participants_btn)

    async def join_callback(self, interaction: discord.Interaction):
        gw = active_giveaways.get(self.message_id)
        if not gw or gw.get("ended"):
            return await interaction.response.send_message(embed=error_embed("This giveaway has ended."), ephemeral=True)
        member = interaction.user
        req_role_id = gw.get("required_role")
        if req_role_id and not any(r.id == req_role_id for r in member.roles):
            role = interaction.guild.get_role(req_role_id) if interaction.guild else None
            return await interaction.response.send_message(
                embed=error_embed(f"You need the {role.mention if role else 'required'} role to enter this giveaway."),
                ephemeral=True
            )
        if member.id in gw["entries"]:
            gw["entries"].remove(member.id)
            save_giveaways()
            await interaction.response.send_message(embed=info_embed("Left Giveaway", f"You've left the giveaway for **{gw['prize']}**."), ephemeral=True)
        else:
            gw["entries"].append(member.id)
            save_giveaways()
            await interaction.response.send_message(embed=success_embed(f"You're in! Good luck winning **{gw['prize']}**."), ephemeral=True)
        try:
            await interaction.message.edit(embed=build_giveaway_embed(gw))
        except Exception:
            pass

    async def participants_callback(self, interaction: discord.Interaction):
        gw = active_giveaways.get(self.message_id) or cfg.get("giveaway_history", {}).get(str(self.message_id))
        if not gw:
            return await interaction.response.send_message(embed=error_embed("This giveaway has ended."), ephemeral=True)
        entries = list(dict.fromkeys(gw.get("entries", [])))  # dedupe, keep join order
        if not entries:
            return await interaction.response.send_message(embed=info_embed("Participants", "No one has joined yet — be the first!"), ephemeral=True)
        shown = entries[:40]
        lines = "\n".join(f"{i+1}. <@{uid}>" for i, uid in enumerate(shown))
        if len(entries) > 40:
            lines += f"\n*... and {len(entries) - 40} more*"
        await interaction.response.send_message(embed=info_embed(f"Participants ({len(entries)})", lines), ephemeral=True)

def save_giveaway_history(gw: dict):
    """Keeps an ended giveaway's final entries/winners around (capped to
    the most recent 50) so `giveaway reroll` still has something to work
    with after it's over — reactions used to double as that record, but
    buttons don't leave anything on the message itself to read back."""
    hist = cfg.setdefault("giveaway_history", {})
    hist[str(gw["message_id"])] = gw
    if len(hist) > 50:
        oldest = sorted(hist.values(), key=lambda g: g.get("ends_ts", 0))[:len(hist) - 50]
        for old in oldest:
            hist.pop(str(old["message_id"]), None)
    save_config(cfg)

async def end_giveaway(gw: dict):
    if gw.get("ended"):
        return
    gw["ended"] = True
    active_giveaways.pop(gw["message_id"], None)
    save_giveaways()
    save_giveaway_history(gw)
    channel = bot.get_channel(gw["channel_id"])
    if not channel:
        return
    try:
        msg = await channel.fetch_message(gw["message_id"])
    except Exception:
        return
    ended_view = GiveawayView(gw["message_id"], ended=True)
    entries = list(set(gw.get("entries", [])))
    if not entries:
        ended_embed = build_giveaway_embed(gw)
        ended_embed.description = "**Giveaway Ended**\n\nNo entries."
        ended_embed.color = 0x4B5563
        try:
            await msg.edit(embed=ended_embed, view=ended_view)
        except Exception:
            pass
        await channel.send(embed=info_embed("Giveaway Ended", f"No winners for **{gw['prize']}**."))
        return
    count   = min(gw["winner_count"], len(entries))
    winners = random.sample(entries, count)
    gw["winners"] = winners
    ended_embed = build_giveaway_embed(gw)
    winner_str  = " ".join(f"<@{w}>" for w in winners)
    ended_embed.description = f"**Giveaway Ended!**\n\n**Winners:** {winner_str}"
    ended_embed.color = 0x4B5563
    try:
        await msg.edit(embed=ended_embed, view=ended_view)
    except Exception:
        pass
    role_note = ""
    win_role_id = gw.get("winner_role_id")
    if win_role_id:
        win_role = channel.guild.get_role(win_role_id)
        if win_role:
            assigned = 0
            for wid in winners:
                m = channel.guild.get_member(wid)
                if m:
                    try:
                        await m.add_roles(win_role, reason=f"Giveaway winner: {gw['prize']}")
                        assigned += 1
                    except Exception:
                        pass
            if assigned:
                role_note = f"\nRole {win_role.mention} was granted to {assigned} winner(s)."
    win_embed = discord.Embed(
        title=f"{e(ICON_WINNER, '🏆')} Giveaway Winners!".strip(),
        description=f"Congratulations {winner_str}!\n\n**Prize:** {gw['prize']}{role_note}",
        color=COLOR_SUCCESS,
        timestamp=discord.utils.utcnow()
    )
    win_embed.set_footer(text=f"{BOT_NAME} Giveaway")
    await channel.send(content=winner_str, embed=win_embed)

# ══════════════════════════════════════════════════════════════════
# TASKS
# ══════════════════════════════════════════════════════════════════

@tasks.loop(minutes=10)
async def premium_expiry_task():
    await check_premium_expiry()
    await check_no_prefix_expiry()

@tasks.loop(minutes=30)
async def cleanup_spam_cache():
    now    = discord.utils.utcnow().timestamp()
    to_del = [key for key, t in spam_cleanup_times.items() if now - t > 120]
    for key in to_del:
        spam_tracker.pop(key, None)
        spam_cleanup_times.pop(key, None)
    stale_flood = [key for key, ts in flood_tracker.items() if not ts or now - ts[-1] > 120]
    for key in stale_flood:
        flood_tracker.pop(key, None)
    now_mono = time.monotonic()
    for gid, entries in list(_recent_boost_starts.items()):
        entries[:] = [(uid, ts) for uid, ts in entries if now_mono - ts < 60]
        if not entries:
            _recent_boost_starts.pop(gid, None)

@tasks.loop(minutes=5)
async def rotate_status():
    if is_maintenance_on():
        try:
            await bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.playing, name="Under Maintenance ⚠️"),
                status=discord.Status.dnd
            )
        except Exception:
            pass
        return
    statuses = [
        discord.Activity(type=discord.ActivityType.watching, name="Hyper moderation."),
        discord.Activity(type=discord.ActivityType.listening, name="!vx help"),
        discord.Activity(type=discord.ActivityType.playing, name="VALLENT EXS v1.2"),
        discord.Activity(type=discord.ActivityType.listening, name="Protect your server now!"),
        discord.Activity(type=discord.ActivityType.watching, name=f"{len(bot.guilds)} servers"),
    ]
    import random as _r
    await bot.change_presence(activity=_r.choice(statuses), status=discord.Status.dnd)

async def sync_premium_descriptions():
    """Prepend a [💎] prefix to the descriptions of slash commands that are
    Premium-locked, then re-sync to Discord so it shows up in the slash-command UI."""
    locked  = set(cfg.get("premium_commands", []))
    changed = False
    for cmd in bot.tree.get_commands():
        base   = ORIGINAL_CMD_DESCRIPTIONS.get(cmd.name, cmd.description.removeprefix("[💎] "))
        wanted = f"[💎] {base}" if cmd.name in locked else base
        if cmd.description != wanted:
            cmd.description = wanted
            changed = True
        if hasattr(cmd, "commands"):
            for sub in cmd.commands:
                key      = f"{cmd.name} {sub.name}"
                base_sub = ORIGINAL_CMD_DESCRIPTIONS.get(key, sub.description.removeprefix("[💎] "))
                wanted_sub = f"[💎] {base_sub}" if key in locked else base_sub
                if sub.description != wanted_sub:
                    sub.description = wanted_sub
                    changed = True
    if changed:
        try:
            await bot.tree.sync()
        except Exception as e:
            logging.error(f"[{BOT_NAME}] Failed to sync premium descriptions: {e}")

# ══════════════════════════════════════════════════════════════════
# BOT EVENTS
# ══════════════════════════════════════════════════════════════════

@bot.event
async def on_error(event_method: str, *args, **kwargs):
    """Catches anything unhandled inside an event listener (on_message,
    on_member_join, reaction handlers, etc.) — the class of bug that
    on_command_error/on_app_command_error never sees since it isn't
    inside a command at all. Discord.py's default behavior here is just
    to print the traceback and move on; this keeps that (via report_error's
    own logging) and ALSO reports it to the error log channel."""
    exc_type, exc, _ = sys.exc_info()
    if exc is None:
        return
    await report_error(exc, location=f"event:{event_method}")

@bot.event
async def on_ready():
    print(f"[{BOT_NAME}] Ready as {bot.user} (ID: {bot.user.id})")

    # Cache the bot account's Discord banner (if it has one) once at startup
    # instead of hitting the API on every help/mention/commandlist call —
    # gateway-cached bot.user often doesn't include banner data, so a single
    # HTTP fetch here is worth it.
    global BOT_BANNER_URL
    try:
        fetched = await bot.fetch_user(bot.user.id)
        BOT_BANNER_URL = fetched.banner.url if fetched.banner else None
    except Exception:
        BOT_BANNER_URL = None

    for cmd in bot.tree.get_commands():
        ORIGINAL_CMD_DESCRIPTIONS[cmd.name] = cmd.description.removeprefix("[💎] ")
        if hasattr(cmd, "commands"):
            for sub in cmd.commands:
                ORIGINAL_CMD_DESCRIPTIONS[f"{cmd.name} {sub.name}"] = sub.description.removeprefix("[💎] ")
    await sync_premium_descriptions()
    try:
        synced = await bot.tree.sync()
        print(f"[{BOT_NAME}] Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"[{BOT_NAME}] Sync error: {e}")

    # Re-register persistent views (tickets) so buttons keep working after a restart.
    bot.add_view(TicketControlView())
    for gid_str, gcfg in cfg.get("guilds", {}).items():
        panels = gcfg.get("ticket", {}).get("panels", {})
        if not panels:
            continue
        guild_obj = bot.get_guild(int(gid_str))
        for pid, panel in panels.items():
            bot.add_view(TicketPanelLayout(pid, panel, guild=guild_obj))
    bot.add_view(VerificationView())

    # Re-register persistent views for /component messages (only response
    # buttons actually need this — link buttons work statelessly forever).
    for gcfg in cfg.get("guilds", {}).values():
        for cid, comp in gcfg.get("message_components", {}).items():
            bot.add_view(MessageComponentLayout(cid, comp))

    await restore_giveaways()

    if not cleanup_spam_cache.is_running():
        cleanup_spam_cache.start()
    if not rotate_status.is_running():
        rotate_status.start()
    if not premium_expiry_task.is_running():
        premium_expiry_task.start()
    await start_vote_webhook_server()
    print(f"[{BOT_NAME}] Online — {len(bot.guilds)} guild(s).")

@bot.event
async def on_command_completion(ctx: commands.Context):
    """Called by discord.py every time a prefix command runs SUCCESSFULLY.
    This is the source of truth for the 'Commands Runned' stat on the profile."""
    uid_str  = str(ctx.author.id)
    cmds_run = cfg.setdefault("commands_run", {})
    cmds_run[uid_str] = cmds_run.get(uid_str, 0) + 1
    save_config(cfg)

@bot.event
async def on_audit_log_entry_create(entry: discord.AuditLogEntry):
    """The heart of anti-nuke — called by Discord in real time whenever a new
    audit log entry appears, no polling needed. Requires the 'View Audit Log'
    permission on the bot's role."""
    guild = entry.guild
    gc    = guild_cfg(cfg, guild.id)
    ac    = gc.get("antinuke", {})
    if not ac.get("enabled"):
        return
    if not entry.user or entry.user.id == bot.user.id:
        return
    if antinuke.is_whitelisted(guild, entry.user.id, bot.owner_id, ac.get("whitelist", [])):
        return

    action = antinuke.classify_entry(entry)
    if not action:
        return

    triggered = False
    if action in antinuke.INSTANT_ACTIONS:
        triggered = True
    else:
        th = antinuke.DEFAULT_THRESHOLDS.get(action)
        if th:
            triggered = antinuke._record_and_check(guild.id, entry.user.id, action, th["count"], th["seconds"])

    if not triggered:
        return

    member = guild.get_member(entry.user.id)
    if not member:
        return

    punishment = ac.get("punishment", "strip_roles")
    result = await antinuke.punish(guild, member, punishment, f"[Anti-Nuke] Detected: {antinuke.ACTION_LABELS.get(action, action)}")
    antinuke.reset_tracker(guild.id, entry.user.id)

    log_id = ac.get("log_channel")
    log_ch = guild.get_channel(log_id) if log_id else None
    if log_ch:
        emb = discord.Embed(
            title=f"{e(ICON_ANTINUKE, '🛡️')} Anti-Nuke Triggered".strip(),
            description=(
                f"**Culprit:** {member.mention} (`{member.id}`)\n"
                f"**Detected:** {antinuke.ACTION_LABELS.get(action, action)}\n"
                f"**Action:** {result}"
            ),
            color=COLOR_ERROR,
            timestamp=discord.utils.utcnow()
        )
        emb.set_thumbnail(url=member.display_avatar.url)
        emb.set_footer(text=BOT_NAME)
        try:
            await log_ch.send(embed=emb)
        except Exception:
            pass
    logging.warning(f"[{BOT_NAME}] Anti-Nuke triggered in {guild.name}: {member} -> {action} -> {result}")

@bot.event
async def on_guild_join(guild: discord.Guild):
    guild_cfg(cfg, guild.id)
    logging.info(f"[{BOT_NAME}] Joined: {guild.name} ({guild.id})")
    bl = cfg.get("blacklisted_guilds", [])
    if guild.id in bl:
        try:
            await guild.leave()
            logging.info(f"[{BOT_NAME}] Left blacklisted guild: {guild.name}")
        except Exception:
            pass

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not message.guild:
        return

    # ── Ignored channel — bot stays fully silent, no processing at all ─────
    gc_ignore = guild_cfg(cfg, message.guild.id)
    if message.channel.id in gc_ignore.get("ignored_channels", []):
        return

    # ── AFK system ───────────────────────────────────────────────────────────
    gc_afk  = guild_cfg(cfg, message.guild.id)
    afk_map = gc_afk.get("afk_users", {})

    # -- Returning from AFK: any message from a currently-AFK member clears
    #    their status, EXCEPT when the message is them re-running the `afk`
    #    command itself (that's setting a new status, not "coming back").
    author_key = str(message.author.id)
    if author_key in afk_map:
        low_afk = message.content.strip().lower()
        is_afk_cmd = (
            low_afk in ("!vx afk", "!v afk") or
            low_afk.startswith(("!vx afk ", "!v afk ")) or
            (user_has_no_prefix(message.guild, message.author) and (low_afk == "afk" or low_afk.startswith("afk ")))
        )
        if not is_afk_cmd:
            entry = afk_map.pop(author_key, None)
            save_config(cfg)
            if entry:
                # Restore whatever nick they had before "[AFK] " got
                # prepended — None means they had no custom nick, so
                # setting nick=None just reverts them to their username.
                try:
                    await message.author.edit(nick=entry.get("original_nick"), reason="AFK status cleared")
                except (discord.Forbidden, discord.HTTPException, AttributeError):
                    pass
                since_ts = entry.get("since")
                since_txt = f" (AFK since <t:{since_ts}:R>)" if since_ts else ""
                try:
                    await message.channel.send(
                        embed=success_embed(f"Welcome back, {message.author.mention}! Your AFK status has been removed{since_txt}."),
                        delete_after=8
                    )
                except Exception:
                    pass

    # -- Notify the sender if this message @mentions someone who is AFK
    if message.mentions:
        afk_hits = []
        seen_ids = set()
        for m in message.mentions:
            if m.id == message.author.id or m.bot or m.id in seen_ids:
                continue
            entry = afk_map.get(str(m.id))
            if entry:
                afk_hits.append((m, entry))
                seen_ids.add(m.id)
        if afk_hits:
            if len(afk_hits) == 1:
                m, entry = afk_hits[0]
                reason   = entry.get("reason") or "AFK"
                since_ts = entry.get("since")
                duration = f"<t:{since_ts}:R>" if since_ts else "Unknown"
                emb = base_embed(
                    _title_with_icon(ICON_AFK, "💤", "User is AFK"),
                    f"{m.mention} is currently AFK — they won't see this ping until they're back.",
                    color=COLOR_PRIMARY
                )
                emb.set_author(name=m.display_name, icon_url=m.display_avatar.url)
                emb.add_field(name="Reason", value=reason, inline=True)
                emb.add_field(name="Duration", value=duration, inline=True)
                emb.set_thumbnail(url=m.display_avatar.url)
            else:
                emb = base_embed(
                    _title_with_icon(ICON_AFK, "💤", "User is AFK"),
                    "The following mentioned members are currently AFK:",
                    color=COLOR_PRIMARY
                )
                for m, entry in afk_hits[:5]:
                    reason   = entry.get("reason") or "AFK"
                    since_ts = entry.get("since")
                    duration = f"<t:{since_ts}:R>" if since_ts else "Unknown"
                    emb.add_field(name=m.display_name, value=f"**Reason:** {reason}\n**Duration:** {duration}", inline=False)
                if bot.user:
                    emb.set_thumbnail(url=bot.user.display_avatar.url)
            try:
                await message.channel.send(embed=emb, reference=message, mention_author=False)
            except Exception:
                pass

    # ── Honeypot channel check ──────────────────────────────────────────────
    gc_trap  = guild_cfg(cfg, message.guild.id)
    ac_trap  = gc_trap.get("antispam", {})
    trap_ch  = ac_trap.get("trap_channel")
    if trap_ch and message.channel.id == trap_ch:
        if isinstance(message.author, discord.Member) and _antispam_is_ignored(message.author, ac_trap):
            return
        try:
            await message.delete()
        except Exception:
            pass
        result = await _antispam_punish(
            message.guild, message.author, ac_trap.get("punishment", "ban"),
            f"[{BOT_NAME}] Sent message in honeypot channel."
        )
        log_id = ac_trap.get("log_channel") or _fallback_log_channel(gc_trap)
        if log_id:
            log_ch = message.guild.get_channel(log_id)
            if log_ch:
                emb = base_embed("Honeypot Triggered", None, color=COLOR_ERROR)
                emb.add_field(name="User",      value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
                emb.add_field(name="Channel",   value=f"<#{trap_ch}>", inline=True)
                emb.add_field(name="Action",    value=result, inline=True)
                snippet = (message.content or "")[:200]
                if snippet:
                    emb.add_field(name="Content", value=f"```{snippet}```", inline=False)
                try:
                    await log_ch.send(embed=emb)
                except Exception:
                    pass
        return

    # ── XP system ────────────────────────────────────────────────────────
    gc = guild_cfg(cfg, message.guild.id)
    ignore_role_ids = set(gc.get("xp_ignore_roles", []))
    author_role_ids = {r.id for r in message.author.roles} if isinstance(message.author, discord.Member) else set()
    if gc.get("leveling_enabled", True) and not (ignore_role_ids & author_role_ids):
        import time
        uid  = str(message.author.id)
        data = get_member_xp(gc, uid)
        now  = time.time()
        cd   = gc.get("xp_cooldown", 60)
        if now - data.get("last_msg_ts", 0) >= cd:
            xp_min, xp_max = gc.get("xp_per_message", [15, 25])
            gain           = round(random.randint(xp_min, xp_max) * get_xp_multiplier(message.author.id))
            old_level      = data["level"]
            data["xp"]    += gain
            data["level"]  = level_from_xp(data["xp"], gc.get("xp_difficulty", 1.0))
            data["last_msg_ts"] = now
            data["messages"]    = data.get("messages", 0) + 1
            save_config(cfg)
            if data["level"] > old_level:
                granted_roles = await apply_level_roles(message.guild, message.author, gc, data["level"])
                lvl_ch_id = gc.get("level_channel")
                lvl_ch    = message.guild.get_channel(lvl_ch_id) if lvl_ch_id else message.channel
                if lvl_ch:
                    roles_txt = ("🎁 Unlocked: " + " ".join(r.mention for r in granted_roles)) if granted_roles else ""
                    template  = gc.get("levelup_message") or "{mention} just leveled up to **Level {level}**! Keep chatting in {server} to climb even higher. {roles}"
                    content   = (template
                                 .replace("{mention}", message.author.mention)
                                 .replace("{user}",    message.author.name)
                                 .replace("{level}",   str(data["level"]))
                                 .replace("{server}",  message.guild.name)
                                 .replace("{roles}",   roles_txt))
                    try:
                        avatar_url = str(message.author.display_avatar.with_format("png").with_size(256))
                        async with aiohttp.ClientSession() as session:
                            async with session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                avatar_bytes = await resp.read()
                        is_prem = user_has_premium(message.guild, message.author)
                        role_names = [r.name for r in granted_roles] if granted_roles else None
                        bg_bytes = await fetch_rank_bg_bytes(is_prem, message.author.id)
                        accent_colors = get_premium_accent(is_prem, message.author.id)
                        buf = await asyncio.to_thread(
                            rank_card.render_levelup_card,
                            avatar_bytes, message.author.name, old_level, data["level"], is_prem, role_names, bg_bytes, accent_colors
                        )
                        file = discord.File(buf, filename="levelup.png")
                        await lvl_ch.send(content=content, file=file)
                    except Exception as e:
                        logging.error(f"[{BOT_NAME}] Failed to render level-up card: {e}")
                        lvl_emb = discord.Embed(description=content, color=COLOR_ERROR)
                        lvl_emb.set_author(name="Level Up!", icon_url=message.author.display_avatar.url)
                        lvl_emb.set_footer(text=BOT_NAME)
                        try:
                            await lvl_ch.send(embed=lvl_emb)
                        except Exception:
                            pass

    # ── Anti cross-channel spam + flood ─────────────────────────────────────
    ac = gc.get("antispam", {})
    if isinstance(message.author, discord.Member) and not _antispam_is_ignored(message.author, ac):
        uid    = message.author.id
        gid    = message.guild.id
        key    = (gid, uid)
        now_ts = discord.utils.utcnow().timestamp()

        # -- Flood: many rapid-fire messages in the same channel within a short window
        flood_count  = ac.get("flood_count", 5)
        flood_window = ac.get("flood_window", 4)
        fl = flood_tracker[key]
        fl.append(now_ts)
        while fl and now_ts - fl[0] > flood_window:
            fl.pop(0)
        if len(fl) >= flood_count:
            flood_tracker.pop(key, None)
            result = await _antispam_punish(message.guild, message.author, ac.get("punishment", "ban"),
                                             f"[{BOT_NAME}] Message flood detected ({flood_count}+ messages within {flood_window}s).")
            await _antispam_log(message.guild, gc, message.author, "Message Flood",
                                 f"{flood_count}+ messages within {flood_window} seconds in {message.channel.mention}", result)
            return

        # -- Cross-channel: an identical message/link/attachment spammed across many different channels
        fingerprint = _spam_fingerprint(message)
        if fingerprint != "empty":
            threshold = ac.get("threshold", SPAM_THRESHOLD)
            window    = ac.get("window", SPAM_WINDOW)
            spam_cleanup_times[key] = now_ts
            tracker = spam_tracker[key]
            if fingerprint not in tracker:
                tracker[fingerprint] = {"channels": set(), "messages": [], "first_seen": now_ts}
            entry = tracker[fingerprint]
            if now_ts - entry["first_seen"] > window:
                tracker[fingerprint] = {"channels": {message.channel.id}, "messages": [(message.channel.id, message.id)], "first_seen": now_ts}
                entry = tracker[fingerprint]
            else:
                entry["channels"].add(message.channel.id)
                entry["messages"].append((message.channel.id, message.id))
            if len(entry["channels"]) >= threshold:
                del tracker[fingerprint]
                for ch_id, msg_id in entry["messages"]:
                    try:
                        ch  = message.guild.get_channel(ch_id)
                        msg = await ch.fetch_message(msg_id) if ch else None
                        if msg:
                            await msg.delete()
                    except Exception:
                        pass
                result = await _antispam_punish(message.guild, message.author, ac.get("punishment", "ban"),
                                                 f"[{BOT_NAME}] Cross-channel spam detected ({threshold}+ channels within {window}s).")
                await _antispam_log(message.guild, gc, message.author, "Cross-Channel Spam",
                                     f"The same message/link appeared in {len(entry['channels'])} channels within {window} seconds.", result)
                return

    # ── Bot mention auto-reply — only when the message is JUST the mention ──
    stripped = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
    if bot.user in message.mentions and not stripped:
        return await message.reply(embed=bot_info_embed(message.author.mention, message.guild.id), view=invite_support_view(), mention_author=False)

    # ── Keyword auto-responses ────────────────────────────────────────────
    if gc.get("autoresponses_enabled", True) and gc.get("autoresponses"):
        content_lower = message.content.lower()
        for entry in gc["autoresponses"].values():
            trigger = entry["trigger"] if entry.get("case_sensitive") else entry["trigger"].lower()
            haystack = message.content if entry.get("case_sensitive") else content_lower
            match_type = entry.get("match", "contains")
            hit = (
                (match_type == "contains"   and trigger in haystack) or
                (match_type == "exact"      and haystack == trigger) or
                (match_type == "startswith" and haystack.startswith(trigger))
            )
            if hit:
                try:
                    await message.channel.send(entry["response"], reference=message, mention_author=False)
                except Exception:
                    pass
                break

    # ── Prefix routing + no-prefix ───────────────────────────────────────
    low = message.content.lower().strip()
    if low.startswith("!vx ") or low == "!vx":
        message.content = "!vx " + message.content[len("!vx"):].lstrip()
    elif low.startswith("!v ") or low == "!v":
        message.content = "!vx " + message.content[len("!v"):].lstrip()
    elif not message.content.startswith("!vx "):
        if user_has_no_prefix(message.guild, message.author):
            text  = message.content.strip()
            first = text.split()[0].lower() if text.split() else ""
            known = set()
            for c in bot.commands:
                known.add(c.name)
                known.update(c.aliases)
            if first in known:
                message.content = "!vx " + text

    await bot.process_commands(message)

# ══════════════════════════════════════════════════════════════════
# GIVEAWAY — REACTION HANDLER
# ══════════════════════════════════════════════════════════════════

@bot.event
async def on_member_remove(member: discord.Member):
    """Revoke the USER badge when a user leaves the support server."""
    support_server_id = int(os.getenv("SUPPORT_SERVER_ID", "0"))
    if member.guild.id != support_server_id:
        return
    support_members = cfg.get("support_server_members", [])
    if member.id in support_members:
        support_members.remove(member.id)
        save_config(cfg)

_recent_boost_starts: dict = defaultdict(list)  # guild_id -> [(member_id, monotonic_ts), ...]
_last_attributed_booster: dict = {}  # guild_id -> (member_id, monotonic_ts) — see fallback below
LAST_BOOSTER_FALLBACK_WINDOW = 600  # seconds (10 min)

async def handle_new_boost(guild: discord.Guild, member: Optional[discord.Member], boost_number: int):
    """Send a notification for ONE individual boost. Fires once per boost,
    even if the same member contributes multiple boosts to the same server
    (Discord only exposes a per-member 'started boosting' transition once —
    guild.premium_subscription_count is the reliable signal that counts
    every single boost, which is why detection is driven from there)."""
    gc    = guild_cfg(cfg, guild.id)
    bc    = gc.get("boost", {})
    ch_id = bc.get("channel")
    if not ch_id:
        return
    channel = guild.get_channel(ch_id)
    if not channel:
        return

    mention_txt = member.mention if member else "Someone"
    name_txt    = member.display_name if member else "Someone"
    avatar_url  = member.display_avatar.url if member else guild.icon.url if guild.icon else None

    def fill(template: str) -> str:
        return (template
                .replace("{mention}", mention_txt)
                .replace("{user}",    name_txt)
                .replace("{server}",  guild.name)
                .replace("{count}",   str(boost_number))
                .replace("{tier}",    str(guild.premium_tier)))

    title = fill(bc.get("title") or "New Server Boost!")
    emoji_str = bc.get("emoji") or e(ICON_BOOST, "🎉")
    desc  = fill(bc.get("description") or "{mention} just boosted **{server}**! Thanks for the support 💜")

    embed = discord.Embed(
        title=f"{emoji_str} {title}".strip(),
        description=desc,
        color=0xF47FFF,
        timestamp=discord.utils.utcnow()
    )
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text=f"{guild.name} • Boost #{boost_number}")
    try:
        await channel.send(embed=embed)
    except Exception:
        pass

@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    """Authoritative trigger for boost notifications. guild.premium_subscription_count
    increments by exactly 1 for EVERY individual boost — including a repeat
    boost from someone who's already boosting, unlike member.premium_since
    which only transitions once per member. This guarantees one notification
    per boost, professionally handling Discord's API limitation on
    per-member repeat-boost attribution."""
    before_count = before.premium_subscription_count or 0
    after_count  = after.premium_subscription_count or 0
    diff = after_count - before_count
    if diff <= 0:
        return

    # Give a slightly-delayed on_member_update a moment to land so we can
    # attribute the boost to whoever actually triggered it when possible.
    await asyncio.sleep(1.5)

    now = time.monotonic()
    pending = _recent_boost_starts.get(after.id, [])
    pending[:] = [(uid, ts) for uid, ts in pending if now - ts < 15]

    for i in range(diff):
        member = None
        if pending:
            uid, _ts = pending.pop(0)
            member = after.get_member(uid)
        if member is None:
            # Discord only flips premium_since the very first time a member
            # boosts — adding extra boost slots while already boosting (or
            # boosting again later without ever un-boosting) never retriggers
            # it, so there's no fresh on_member_update signal for those. The
            # best available attribution is whoever we last confirmed
            # boosting in this guild recently, since that's overwhelmingly
            # the real explanation (same person contributing another slot),
            # rather than silently falling back to an anonymous "Someone".
            last = _last_attributed_booster.get(after.id)
            if last and (now - last[1]) < LAST_BOOSTER_FALLBACK_WINDOW:
                member = after.get_member(last[0])
        if member is not None:
            _last_attributed_booster[after.id] = (member.id, now)
        await handle_new_boost(after, member, boost_number=before_count + i + 1)

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # ── Server boost attribution cache — the actual notification is fired
    # from on_guild_update (see above); this just records WHO started
    # boosting so that event can credit the right member. ──────────────────
    if not before.premium_since and after.premium_since:
        _recent_boost_starts[after.guild.id].append((after.id, time.monotonic()))

    # ── Badge role-sync is computed live (straight from the Discord role every
    # time get_bot_role() is called), so nothing needs updating here. This is
    # just to send a congratulatory DM when someone gains a role that's synced
    # to a badge — so they notice their badge went up. Support server only. ──
    support_server_id = int(os.getenv("SUPPORT_SERVER_ID", "0"))
    if after.guild.id != support_server_id or after.bot:
        return
    if before.roles == after.roles:
        return
    role_sync = cfg.get("role_sync", {})
    if not role_sync:
        return
    before_ids = {r.id for r in before.roles}
    after_ids  = {r.id for r in after.roles}
    gained     = after_ids - before_ids
    for tier in reversed(BOT_ROLE_HIERARCHY):
        role_id = role_sync.get(tier)
        if role_id and role_id in gained:
            info      = BOT_ROLE_BADGES[tier]
            badge_tag = (info["emoji"] + " ") if info.get("emoji") else ""
            try:
                await after.send(embed=base_embed(
                    "Badge Updated!",
                    f"You just got a role in **{after.guild.name}** and now automatically have the "
                    f"{badge_tag}**{info['label']}** badge on {BOT_NAME}!\nCheck your profile: `profile`",
                    color=info["color"]
                ))
            except Exception:
                pass
            break


# ══════════════════════════════════════════════════════════════════
# PREFIX COMMANDS — MODERATION
# ══════════════════════════════════════════════════════════════════

@bot.command(name="kick", aliases=["k"])
async def pfx_kick(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    await do_kick(ctx.guild, ctx.author, member, reason, ctx.send)

@bot.command(name="ban", aliases=["b"])
async def pfx_ban(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    await do_ban(ctx.guild, ctx.author, member, reason, ctx.send)

@bot.command(name="unban", aliases=["ub"])
async def pfx_unban(ctx, user_id: str):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.ban_members:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    try:
        uid  = int(user_id.strip("<@!>"))
        await ctx.guild.unban(discord.Object(id=uid), reason=f"By {ctx.author}")
        await ctx.send(embed=success_embed(f"User `{uid}` has been unbanned."))
    except discord.NotFound:
        await ctx.send(embed=error_embed("That user isn't on the ban list."))
    except discord.Forbidden:
        await ctx.send(embed=error_embed("The bot doesn't have permission."))

@bot.command(name="timeout", aliases=["to", "mute"])
async def pfx_timeout(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided."):
    await do_timeout(ctx.guild, ctx.author, member, duration, reason, ctx.send)

@bot.command(name="untimeout", aliases=["unmute", "unto"])
async def pfx_untimeout(ctx, member: discord.Member):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.moderate_members:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    try:
        await member.timeout(None, reason=f"By {ctx.author}")
        await ctx.send(embed=success_embed(f"Timeout removed from {member.mention}."))
    except discord.Forbidden:
        await ctx.send(embed=error_embed("The bot doesn't have permission."))

@bot.command(name="warn", aliases=["w"])
async def pfx_warn(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    await do_warn(ctx.guild, ctx.author, member, reason, ctx.send)

@bot.command(name="warnings", aliases=["warns"])
async def pfx_warnings(ctx, member: discord.Member = None):
    target = member or ctx.author
    gc     = guild_cfg(cfg, ctx.guild.id)
    warns  = gc.get("warnings", {}).get(str(target.id), [])
    if not warns:
        return await ctx.send(embed=info_embed(f"Warnings — {target.display_name}", "No warnings."))
    lines = [
        f"**{i+1}.** {w.get('reason','?')} *(by <@{w.get('warned_by','?')}> — {w.get('timestamp','')[:10]})*"
        for i, w in enumerate(warns)
    ]
    embed = discord.Embed(title=f"Warnings — {target.display_name}", description="\n".join(lines), color=COLOR_WARNING)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text=f"Total: {len(warns)} warning(s) • {BOT_NAME}")
    await ctx.send(embed=embed)

@bot.command(name="unwarn", aliases=["uw"])
async def pfx_unwarn(ctx, member: discord.Member, number: int):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_messages:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    gc    = guild_cfg(cfg, ctx.guild.id)
    warns = gc.get("warnings", {}).get(str(member.id), [])
    if not warns:
        return await ctx.send(embed=error_embed(f"{member.display_name} has no warnings."))
    if not 1 <= number <= len(warns):
        return await ctx.send(embed=error_embed(f"Invalid number (1–{len(warns)})."))
    removed = warns.pop(number - 1)
    save_config(cfg)
    await ctx.send(embed=success_embed(f"Warning #{number} `{removed.get('reason','?')}` removed from {member.mention}."))

@bot.command(name="clearwarnings", aliases=["cw", "clearwarns"])
async def pfx_clearwarnings(ctx, member: discord.Member):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_messages:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    gc = guild_cfg(cfg, ctx.guild.id)
    gc.setdefault("warnings", {})[str(member.id)] = []
    save_config(cfg)
    await ctx.send(embed=success_embed(f"All warnings for {member.mention} have been cleared."))

@bot.command(name="purge", aliases=["clear", "prune"])
async def pfx_purge(ctx, amount: int = 10):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_messages:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."), delete_after=5)
    amount  = max(1, min(100, amount))
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(embed=success_embed(f"Deleted {max(0, len(deleted)-1)} message(s)."))
    await asyncio.sleep(4)
    try:
        await msg.delete()
    except Exception:
        pass

@bot.command(name="lock", aliases=["lockdown"])
async def pfx_lock(ctx, channel: discord.TextChannel = None):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_channels:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    ch = channel or ctx.channel
    ow = ch.overwrites_for(ctx.guild.default_role)
    ow.send_messages = False
    try:
        await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"Locked by {ctx.author}")
        await ctx.send(embed=success_embed(f"{ch.mention} has been locked."))
    except discord.Forbidden:
        await ctx.send(embed=error_embed("The bot doesn't have permission."))

@bot.command(name="unlock", aliases=["unlockdown"])
async def pfx_unlock(ctx, channel: discord.TextChannel = None):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_channels:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    ch = channel or ctx.channel
    ow = ch.overwrites_for(ctx.guild.default_role)
    ow.send_messages = None
    try:
        await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"Unlocked by {ctx.author}")
        await ctx.send(embed=success_embed(f"{ch.mention} has been unlocked."))
    except discord.Forbidden:
        await ctx.send(embed=error_embed("The bot doesn't have permission."))

@bot.command(name="slowmode", aliases=["sm"])
async def pfx_slowmode(ctx, seconds: int = 0, channel: discord.TextChannel = None):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_channels:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    ch = channel or ctx.channel
    seconds = max(0, min(21600, seconds))
    try:
        await ch.edit(slowmode_delay=seconds, reason=f"By {ctx.author}")
        msg = f"Slowmode disabled in {ch.mention}." if seconds == 0 else f"Slowmode in {ch.mention} → **{seconds}s**."
        await ctx.send(embed=success_embed(msg))
    except discord.Forbidden:
        await ctx.send(embed=error_embed("The bot doesn't have permission."))

@bot.command(name="hide", aliases=["hidechannel", "hc"])
async def pfx_hide(ctx, channel: discord.TextChannel = None):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_channels:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    ch = channel or ctx.channel
    ow = ch.overwrites_for(ctx.guild.default_role)
    ow.view_channel = False
    try:
        await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"Hidden by {ctx.author}")
        await ctx.send(embed=success_embed(f"{ch.mention} has been hidden from everyone."))
    except discord.Forbidden:
        await ctx.send(embed=error_embed("The bot doesn't have permission."))

@bot.command(name="unhide", aliases=["unhidechannel", "uhc", "showchannel"])
async def pfx_unhide(ctx, channel: discord.TextChannel = None):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_channels:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    ch = channel or ctx.channel
    ow = ch.overwrites_for(ctx.guild.default_role)
    ow.view_channel = None
    try:
        await ch.set_permissions(ctx.guild.default_role, overwrite=ow, reason=f"Unhidden by {ctx.author}")
        await ctx.send(embed=success_embed(f"{ch.mention} is visible to everyone again."))
    except discord.Forbidden:
        await ctx.send(embed=error_embed("The bot doesn't have permission."))

# ── ROLE & VOICE ──────────────────────────────────────────────────

@bot.command(name="addrole", aliases=["ar"])
async def pfx_addrole(ctx, member: discord.Member, role: discord.Role):
    await do_addrole(ctx.guild, ctx.author, member, role, ctx.send)

@bot.command(name="removerole", aliases=["rr"])
async def pfx_removerole(ctx, member: discord.Member, role: discord.Role):
    await do_removerole(ctx.guild, ctx.author, member, role, ctx.send)

@bot.command(name="move", aliases=["mv"])
async def pfx_move(ctx, member: discord.Member, channel: discord.VoiceChannel):
    await do_move(ctx.guild, ctx.author, member, channel, ctx.send)

# ── INFO ──────────────────────────────────────────────────────────

@bot.command(name="userinfo", aliases=["ui", "whois"])
async def pfx_userinfo(ctx, member: discord.Member = None):
    await do_userinfo(ctx.guild, member or ctx.author, ctx.send)

@bot.command(name="serverinfo", aliases=["si"])
async def pfx_serverinfo(ctx):
    g = ctx.guild
    embed = discord.Embed(title=g.name, description=g.description or "", color=COLOR_PRIMARY, timestamp=discord.utils.utcnow())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner",      value=f"<@{g.owner_id}>",                           inline=True)
    embed.add_field(name="Members",    value=f"{g.member_count:,}",                         inline=True)
    embed.add_field(name="Created",    value=g.created_at.strftime("%d %b %Y"),              inline=True)
    embed.add_field(name="Channels",   value=str(len(g.text_channels)),                     inline=True)
    embed.add_field(name="Voice",      value=str(len(g.voice_channels)),                    inline=True)
    embed.add_field(name="Roles",      value=str(len(g.roles)),                             inline=True)
    embed.add_field(name="Emojis",     value=str(len(g.emojis)),                            inline=True)
    embed.add_field(name="Boost Tier", value=str(g.premium_tier),                           inline=True)
    embed.add_field(name="Boosts",     value=str(g.premium_subscription_count or 0),        inline=True)
    embed.set_footer(text=f"{BOT_NAME} • ID: {g.id}")
    await ctx.send(embed=embed)

@bot.command(name="avatar", aliases=["av", "pfp"])
async def pfx_avatar(ctx, member: discord.Member = None):
    await do_avatar(member or ctx.author, ctx.send)

@bot.command(name="ping", aliases=["pong", "latency"])
async def pfx_ping(ctx):
    await do_ping(ctx.send)

@bot.command(name="afk", aliases=["away"])
async def pfx_afk(ctx, *, reason: str = ""):
    await do_afk_set(ctx.guild, ctx.author, reason, ctx.send)

@bot.command(name="addemoji", aliases=["ae"])
async def pfx_addemoji(ctx, emoji_or_url: str = "", *, name: str = ""):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_emojis:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    if not emoji_or_url:
        return await ctx.send(embed=error_embed("Usage: `!vx addemoji <:emoji:id>` or `!vx addemoji <url> <name>`"))
    result = await do_addemoji(ctx.guild, emoji_or_url, name)
    if result["success"]:
        emoji = result["emoji"]
        await ctx.send(embed=success_embed(f"Emoji **{emoji.name}** added! {emoji}"))
    else:
        await ctx.send(embed=error_embed(result["error"]))

# ── PROFILE ───────────────────────────────────────────────────────

@bot.command(name="profile", aliases=["p", "pf"])
@commands.cooldown(1, 10, commands.BucketType.user)
async def pfx_profile(ctx, member: discord.Member = None):
    target = member or ctx.author
    async with ctx.typing():
        try:
            file = await build_profile_card_file(target)
            await ctx.send(file=file)
        except Exception:
            logging.exception(f"[{BOT_NAME}] profile card render gagal")
            await ctx.send(embed=error_embed("Couldn't generate that profile card right now — try again in a bit."))

# ── RANK & LEADERBOARD ────────────────────────────────────────────

async def _build_leaderboard_entries(guild: discord.Guild, all_d: list) -> list:
    """Fetch each top-10 member's avatar in parallel (not one at a time) so
    generating the leaderboard card doesn't stall waiting on 10 sequential HTTP requests."""
    async def fetch_one(idx, uid, data):
        m    = guild.get_member(int(uid))
        name = m.name if m else f"User ({uid[:6]})"
        avatar_url = str((m.display_avatar if m else guild.me.display_avatar).with_format("png").with_size(128))
        avatar_bytes = b""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    avatar_bytes = await resp.read()
        except Exception:
            pass
        return {
            "rank": idx + 1, "avatar_bytes": avatar_bytes, "name": name,
            "level": data.get("level", 0), "xp": data.get("xp", 0),
        }
    tasks = [fetch_one(idx, uid, data) for idx, (uid, data) in enumerate(all_d)]
    return await asyncio.gather(*tasks)

def _format_remaining(target: datetime.datetime) -> str:
    """Plain-text countdown for a disabled button's label (Discord buttons
    can't render live timestamps like message content can with <t:...:R>,
    so this is a static snapshot valid as of when the message was sent)."""
    delta = target - datetime.datetime.now(datetime.timezone.utc)
    total_minutes = max(1, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"

def _support_boost_promo(uid: int):
    """Return (content_text, view) combining the two ways to get an XP
    Boost: joining the support server (+15%, 60 min) and voting for the
    bot on top.gg (+10%, 20 min, ~12h cooldown between votes). Either
    half is skipped if its config isn't set (SUPPORT_INVITE /
    TOPGG_VOTE_URL). The Vote button is disabled with a "Vote again in
    Xh Ym" label while the user is on cooldown, instead of just always
    linking out."""
    lines = []
    view  = discord.ui.View()

    remaining = xp_boost_remaining(uid)
    if remaining:
        pct = round((get_xp_multiplier(uid) - 1) * 100)
        lines.append(f"Your **+{pct}%** XP Boost is active until {discord.utils.format_dt(remaining, 'R')}!")

    if SUPPORT_INVITE and SUPPORT_INVITE.startswith(("http://", "https://")):
        if not remaining:
            lines.append("**Join the support server** and get a **+15% XP Boost** for 60 minutes!")
        view.add_item(discord.ui.Button(label="Join Support Server", style=discord.ButtonStyle.link, url=SUPPORT_INVITE))

    if TOPGG_VOTE_URL:
        next_vote = vote_system.next_vote_time(cfg.get("votes", {}), str(uid))
        if next_vote:
            lines.append(f"You can **vote** again {discord.utils.format_dt(next_vote, 'R')} for another **+10%** Boost (20 min).")
            view.add_item(discord.ui.Button(label=f"Vote again in {_format_remaining(next_vote)}", style=discord.ButtonStyle.secondary, disabled=True))
        else:
            lines.append("**Vote for the bot** and get a **+10% XP Boost** for 20 minutes!")
            view.add_item(discord.ui.Button(label="Vote", style=discord.ButtonStyle.link, url=TOPGG_VOTE_URL))

    if not lines and not view.children:
        return None, None
    return ("\n".join(lines) if lines else None), (view if view.children else None)

@bot.command(name="rank", aliases=["r"])
@commands.cooldown(1, 6, commands.BucketType.user)
async def pfx_rank(ctx, member: discord.Member = None):
    import aiohttp
    target      = member or ctx.author
    gc          = guild_cfg(cfg, ctx.guild.id)
    data        = get_member_xp(gc, str(target.id))
    lvl, cx, nx = xp_progress(data["xp"], gc.get("xp_difficulty", 1.0))
    all_m       = sorted(gc["members_xp"].items(), key=lambda x: x[1].get("xp", 0), reverse=True)
    rank        = next((i+1 for i, (uid, _) in enumerate(all_m) if uid == str(target.id)), 1)
    is_prem     = user_has_premium(ctx.guild, target)
    avatar_url  = str(target.display_avatar.with_format("png").with_size(256))

    async with ctx.typing():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    avatar_bytes = await resp.read()
            bg_bytes = await fetch_rank_bg_bytes(is_prem, target.id)
            accent_colors = get_premium_accent(is_prem, target.id)
            buf = await asyncio.to_thread(
                rank_card.render_rank_card,
                avatar_bytes, target.name, lvl, rank, cx, nx,
                data["xp"], is_prem, data.get("messages", 0), bg_bytes, accent_colors
            )
            file = discord.File(buf, filename="rank.png")
        except Exception:
            logging.exception(f"[{BOT_NAME}] Failed to render rank card")
            file = None

        if file:
            kwargs = {"file": file}
            try:
                content, view = _support_boost_promo(ctx.author.id)
                if content: kwargs["content"] = content
                if view:    kwargs["view"] = view
            except Exception:
                logging.exception(f"[{BOT_NAME}] Failed to build boost promo (rank card still sent)")
            return await ctx.send(**kwargs)
    # Fallback text embed if image rendering fails entirely
    pct   = int((cx / max(nx, 1)) * 100)
    bar   = "▰" * int(pct/100*16) + "▱" * (16-int(pct/100*16))
    embed = discord.Embed(
        description=(
            f"**@{target.name}**\n\n"
            f"**Level: {lvl}** | **XP: {cx:,}/{nx:,}** | **Rank: #{rank}**\n\n"
            f"`{bar}` {pct}%\n\n"
            f"*Total XP: {data['xp']:,} | Messages: {data.get('messages',0):,}*"
        ),
        color=COLOR_PRIMARY, timestamp=discord.utils.utcnow()
    )
    embed.set_author(name="Rank Card", icon_url=target.display_avatar.url)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text=BOT_NAME)
    await ctx.send(embed=embed)

@bot.command(name="rankbg", aliases=["rankbackground", "cardbg"])
async def pfx_rankbg(ctx, url: str = ""):
    if not user_has_premium(ctx.guild, ctx.author):
        msg = "Custom backgrounds (rank card + level-up card) are a **Premium** perk — ask the bot owner about getting Premium."
        if SUPPORT_INVITE:
            msg += f"\n[Join the support server]({SUPPORT_INVITE}) to ask about it."
        return await ctx.send(embed=error_embed(msg))
    backgrounds = cfg.setdefault("premium_backgrounds", {})
    uid = str(ctx.author.id)
    if not url:
        if uid in backgrounds:
            backgrounds.pop(uid, None)
            save_config(cfg)
            return await ctx.send(embed=success_embed("Custom background removed — your rank card and level-up card are back to the default look."))
        return await ctx.send(embed=info_embed(
            "Custom Background",
            "`rankbg <image url>` — set a custom background for your `rank` card and level-up card (must end in `.png`, `.jpg`, `.jpeg`, or `.webp`)\n"
            "`rankbg` (no url) — remove your current custom background\n\n"
            "-# Looking for your `profile` ID card instead? Use `idcardbg` — it has its own separate background."
        ))
    if not _BG_URL_RE.match(url.strip()):
        return await ctx.send(embed=error_embed("That doesn't look like a valid direct image URL — it needs to start with `http(s)://` and end in `.png`, `.jpg`, `.jpeg`, or `.webp`."))
    async with ctx.typing():
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url.strip(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return await ctx.send(embed=error_embed(f"Couldn't fetch that URL (HTTP {resp.status}) — double-check it's a direct, public image link."))
                    test_bytes = await resp.read()
        except Exception:
            return await ctx.send(embed=error_embed("Couldn't fetch that URL — double-check it's a direct, public image link."))
        rendered = await asyncio.to_thread(rank_card.cover_image, test_bytes, (934, 300))
        if rendered is None:
            return await ctx.send(embed=error_embed("That URL didn't decode as a valid image — try a different link."))
    backgrounds[uid] = url.strip()
    save_config(cfg)
    await ctx.send(embed=success_embed("Custom background set! It'll show on your `rank` card and level-up card."))

@bot.command(name="rankcolor", aliases=["rankcolors", "cardcolor"])
async def pfx_rankcolor(ctx, color1: str = "", color2: str = "", color3: str = ""):
    if not user_has_premium(ctx.guild, ctx.author):
        msg = "A custom gradient color (rank card + level-up card) is a **Premium** perk — ask the bot owner about getting Premium."
        if SUPPORT_INVITE:
            msg += f"\n[Join the support server]({SUPPORT_INVITE}) to ask about it."
        return await ctx.send(embed=error_embed(msg))
    colors = cfg.setdefault("premium_colors", {})
    uid = str(ctx.author.id)
    if not color1:
        if uid in colors:
            colors.pop(uid, None)
            save_config(cfg)
            return await ctx.send(embed=success_embed("Custom gradient removed — back to the default gold premium look."))
        return await ctx.send(embed=info_embed(
            "Rank Card Gradient",
            "`rankcolor <hex1> <hex2> [hex3]` — set a custom 2 or 3-color gradient for your `rank` card & level-up card (e.g. `rankcolor #a672ff #20dcd2` or add a 3rd color)\n"
            "`rankcolor` (no args) — remove your gradient, back to default gold\n\n"
            "-# Looking for your `profile` ID card instead? Use `idcardcolor` — it has its own separate gradient."
        ))
    if not color2:
        return await ctx.send(embed=error_embed("Give at least two hex colors, e.g. `rankcolor #a672ff #20dcd2` (a 3rd is optional)."))
    stops = [color1, color2] + ([color3] if color3 else [])
    parsed = [parse_hex_color(c) for c in stops]
    if not all(parsed):
        return await ctx.send(embed=error_embed("That doesn't look like a valid hex color — use 6-digit hex codes like `#a672ff` or `a672ff`."))
    colors[uid] = [c.strip().lstrip("#") for c in stops]
    save_config(cfg)
    embed = success_embed("Custom gradient set! It'll show on your `rank` card and level-up card.")
    embed.add_field(name="Preview", value=" → ".join(f"`#{c}`" for c in colors[uid]))
    embed.color = discord.Color.from_rgb(*parsed[0])
    await ctx.send(embed=embed)

@bot.command(name="idcardbg", aliases=["profilebg", "idbg"])
async def pfx_idcardbg(ctx, url: str = ""):
    if not user_has_premium(ctx.guild, ctx.author):
        msg = "A custom ID card background is a **Premium** perk — ask the bot owner about getting Premium."
        if SUPPORT_INVITE:
            msg += f"\n[Join the support server]({SUPPORT_INVITE}) to ask about it."
        return await ctx.send(embed=error_embed(msg))
    backgrounds = cfg.setdefault("profile_backgrounds", {})
    uid = str(ctx.author.id)
    if not url:
        if uid in backgrounds:
            backgrounds.pop(uid, None)
            save_config(cfg)
            return await ctx.send(embed=success_embed("Custom ID card background removed — back to the default look."))
        return await ctx.send(embed=info_embed(
            "ID Card Background",
            "`idcardbg <image url>` — set a custom background for your `profile` ID card only (must end in `.png`, `.jpg`, `.jpeg`, or `.webp`)\n"
            "`idcardbg` (no url) — remove your current custom background\n\n"
            "-# This is separate from `rankbg` — the ID card is a different shape/size, so it gets its own background."
        ))
    if not _BG_URL_RE.match(url.strip()):
        return await ctx.send(embed=error_embed("That doesn't look like a valid direct image URL — it needs to start with `http(s)://` and end in `.png`, `.jpg`, `.jpeg`, or `.webp`."))
    async with ctx.typing():
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url.strip(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return await ctx.send(embed=error_embed(f"Couldn't fetch that URL (HTTP {resp.status}) — double-check it's a direct, public image link."))
                    test_bytes = await resp.read()
        except Exception:
            return await ctx.send(embed=error_embed("Couldn't fetch that URL — double-check it's a direct, public image link."))
        rendered = await asyncio.to_thread(rank_card.cover_image, test_bytes, (100, 100))
        if rendered is None:
            return await ctx.send(embed=error_embed("That URL didn't decode as a valid image — try a different link."))
    backgrounds[uid] = url.strip()
    save_config(cfg)
    await ctx.send(embed=success_embed("Custom ID card background set! Run `profile` to see it."))

@bot.command(name="idcardcolor", aliases=["profilecolor", "idcolor"])
async def pfx_idcardcolor(ctx, color1: str = "", color2: str = "", color3: str = ""):
    if not user_has_premium(ctx.guild, ctx.author):
        msg = "A custom ID card gradient is a **Premium** perk — ask the bot owner about getting Premium."
        if SUPPORT_INVITE:
            msg += f"\n[Join the support server]({SUPPORT_INVITE}) to ask about it."
        return await ctx.send(embed=error_embed(msg))
    colors = cfg.setdefault("profile_colors", {})
    uid = str(ctx.author.id)
    if not color1:
        if uid in colors:
            colors.pop(uid, None)
            save_config(cfg)
            return await ctx.send(embed=success_embed("Custom ID card gradient removed — back to the default gold premium look."))
        return await ctx.send(embed=info_embed(
            "ID Card Gradient",
            "`idcardcolor <hex1> <hex2> [hex3]` — set a custom 2 or 3-color gradient for your `profile` ID card only (e.g. `idcardcolor #a672ff #20dcd2` or add a 3rd color)\n"
            "`idcardcolor` (no args) — remove your gradient, back to default gold\n\n"
            "-# This is separate from `rankcolor` — set them differently if you want the two cards to look distinct."
        ))
    if not color2:
        return await ctx.send(embed=error_embed("Give at least two hex colors, e.g. `idcardcolor #a672ff #20dcd2` (a 3rd is optional)."))
    stops = [color1, color2] + ([color3] if color3 else [])
    parsed = [parse_hex_color(c) for c in stops]
    if not all(parsed):
        return await ctx.send(embed=error_embed("That doesn't look like a valid hex color — use 6-digit hex codes like `#a672ff` or `a672ff`."))
    colors[uid] = [c.strip().lstrip("#") for c in stops]
    save_config(cfg)
    embed = success_embed("Custom ID card gradient set! Run `profile` to see it.")
    embed.add_field(name="Preview", value=" → ".join(f"`#{c}`" for c in colors[uid]))
    embed.color = discord.Color.from_rgb(*parsed[0])
    await ctx.send(embed=embed)

def _vote_command_kwargs(uid: int) -> dict:
    """Shared embed+view builder for /vote and !vote."""
    if not TOPGG_VOTE_URL:
        return {"embed": error_embed("Voting isn't set up yet — ask the bot owner to configure `TOPGG_VOTE_URL`.")}
    entry     = cfg.get("votes", {}).get(str(uid), {})
    next_vote = vote_system.next_vote_time(cfg.get("votes", {}), str(uid))
    embed = discord.Embed(
        title="Vote for the Bot",
        description=(
            f"Vote on top.gg and get a **+10% XP Boost** for **{vote_system.BOOST_MINUTES} minutes**, every time you vote!\n\n"
            + (f"⏳ You can vote again {discord.utils.format_dt(next_vote, 'R')}." if next_vote else "✅ You can vote right now!")
        ),
        color=COLOR_PRIMARY,
    )
    if entry.get("total_votes"):
        embed.add_field(name="Total Votes", value=str(entry["total_votes"]), inline=True)
    if entry.get("streak"):
        embed.add_field(name="Current Streak", value=str(entry["streak"]), inline=True)
    embed.set_footer(text=BOT_NAME)
    view = discord.ui.View()
    if next_vote:
        view.add_item(discord.ui.Button(label=f"Vote again in {_format_remaining(next_vote)}", style=discord.ButtonStyle.secondary, disabled=True))
    else:
        view.add_item(discord.ui.Button(label="Vote Now", style=discord.ButtonStyle.link, url=TOPGG_VOTE_URL))
    return {"embed": embed, "view": view}

@bot.command(name="vote", aliases=["v"])
async def pfx_vote(ctx):
    await ctx.send(**_vote_command_kwargs(ctx.author.id))

@bot.command(name="leaderboard", aliases=["lb"])
async def pfx_leaderboard(ctx):
    gc    = guild_cfg(cfg, ctx.guild.id)
    all_d = sorted(gc["members_xp"].items(), key=lambda x: x[1].get("xp", 0), reverse=True)[:10]
    if not all_d:
        return await ctx.send(embed=info_embed("Leaderboard", "No XP data yet."))

    async with ctx.typing():
        try:
            entries = await _build_leaderboard_entries(ctx.guild, all_d)
            buf  = await asyncio.to_thread(rank_card.render_leaderboard_card, ctx.guild.name, entries)
            file = discord.File(buf, filename="leaderboard.png")
            return await ctx.send(file=file)
        except Exception as e:
            logging.error(f"[{BOT_NAME}] Failed to render leaderboard card: {e}")

    # Fallback text if image rendering fails entirely
    lines = []
    for idx, (uid, data) in enumerate(all_d):
        m     = ctx.guild.get_member(int(uid))
        name  = m.name if m else f"User ({uid[:6]})"
        medal = ["#1","#2","#3"][idx] if idx < 3 else f"#{idx+1}"
        lines.append(f"**{medal} {name}** — Level **{data.get('level',0)}** · {data.get('xp',0):,} XP")
    embed = discord.Embed(title="XP Leaderboard", description="\n".join(lines), color=COLOR_PRIMARY, timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"{BOT_NAME} · {ctx.guild.name}")
    await ctx.send(embed=embed)

@bot.command(name="level", aliases=["lvl"])
async def pfx_level(ctx, sub: str = "", *args):
    sub = sub.lower()
    gc  = guild_cfg(cfg, ctx.guild.id)

    if sub == "rank":
        m = None
        if args:
            try: m = ctx.guild.get_member(int(args[0].strip("<@!>")))
            except Exception: pass
        await pfx_rank(ctx, m)

    elif sub == "leaderboard":
        await pfx_leaderboard(ctx)

    elif sub == "message":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        action = args[0].lower() if args else ""
        if action == "set":
            template = " ".join(args[1:]).strip()
            if not template:
                return await ctx.send(embed=error_embed(
                    "Usage: `level message set <text>`\n\n"
                    "Placeholders: `{mention}` `{user}` `{level}` `{server}` `{roles}`\n"
                    "Example: `level message set {mention} just hit **Level {level}**! 🔥 {roles}`"
                ))
            gc["levelup_message"] = template
            save_config(cfg)
            preview = (template
                       .replace("{mention}", ctx.author.mention)
                       .replace("{user}",    ctx.author.name)
                       .replace("{level}",   "27")
                       .replace("{server}",  ctx.guild.name)
                       .replace("{roles}",   "🎁 Unlocked: @Elite"))
            embed = success_embed(f"Level-up message updated.\n\n**Preview:**\n{preview}")
            return await ctx.send(embed=embed)
        elif action == "reset":
            gc["levelup_message"] = "{mention} just leveled up to **Level {level}**! Keep chatting in {server} to climb even higher. {roles}"
            save_config(cfg)
            return await ctx.send(embed=success_embed("Level-up message reset to default."))
        elif action == "show":
            return await ctx.send(embed=info_embed("Level-Up Message Template", f"```{gc.get('levelup_message','')}```"))
        else:
            return await ctx.send(embed=info_embed("Level-Up Message", (
                "`level message set <text>` — change the level-up notification message\n"
                "`level message show` — view the current template\n"
                "`level message reset` — revert to the default\n\n"
                "Available placeholders: `{mention}` `{user}` `{level}` `{server}` `{roles}`\n"
                "(`{roles}` is automatically empty if no role reward was earned)"
            )))

    elif sub == "toggle":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        current = gc.get("leveling_enabled", True)
        gc["leveling_enabled"] = not current
        save_config(cfg)
        state = "enabled" if gc["leveling_enabled"] else "disabled"
        color = COLOR_SUCCESS if gc["leveling_enabled"] else COLOR_ERROR
        await ctx.send(embed=base_embed("Leveling System",
            "The leveling system is now **" + state + "** in this server.", color=color))

    elif sub == "xp":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        parts = list(args)
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            cur_min, cur_max = gc.get("xp_per_message", [15, 25])
            return await ctx.send(embed=error_embed(
                f"Usage: `level xp <min> <max>`\nCurrent: **{cur_min}-{cur_max} XP** per message"))
        xp_min, xp_max = int(parts[0]), int(parts[1])
        if xp_min < 1 or xp_max < xp_min or xp_max > 1000:
            return await ctx.send(embed=error_embed("Values must be positive, min ≤ max, and max ≤ 1000."))
        gc["xp_per_message"] = [xp_min, xp_max]
        save_config(cfg)
        await ctx.send(embed=success_embed(f"XP per message set to **{xp_min}-{xp_max} XP**."))

    elif sub == "cooldown":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        if not args or not args[0].isdigit():
            return await ctx.send(embed=error_embed(
                f"Usage: `level cooldown <seconds>`\nCurrent: **{gc.get('xp_cooldown', 60)}s** between XP gains"))
        seconds = int(args[0])
        if not 0 <= seconds <= 3600:
            return await ctx.send(embed=error_embed("Must be between 0 and 3600 seconds."))
        gc["xp_cooldown"] = seconds
        save_config(cfg)
        await ctx.send(embed=success_embed(f"XP cooldown set to **{seconds} seconds** between messages that count."))

    elif sub == "difficulty":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        if not args:
            cur = gc.get("xp_difficulty", 1.0)
            return await ctx.send(embed=info_embed("Level Difficulty", (
                f"Current multiplier: **{cur}x**\n\n"
                "`level difficulty <number>` — scales how much XP every level requires.\n"
                "`1.0` = default · `2.0` = twice as slow · `0.5` = twice as fast\n\n"
                f"Example at 1.0x: Level 10 needs {xp_for_level(9, 1.0):,} XP for that step.\n"
                f"At **{cur}x**: Level 10 needs {xp_for_level(9, cur):,} XP for that step."
            )))
        try:
            mult = float(args[0])
        except ValueError:
            return await ctx.send(embed=error_embed("Must be a number, e.g. `1.5`."))
        if not 0.1 <= mult <= 10:
            return await ctx.send(embed=error_embed("Must be between 0.1 and 10."))
        gc["xp_difficulty"] = mult
        for uid, data in gc["members_xp"].items():
            data["level"] = level_from_xp(data["xp"], mult)
        save_config(cfg)
        await ctx.send(embed=success_embed(
            f"Level difficulty set to **{mult}x**. Everyone's level has been recalculated to match "
            f"(their XP totals are untouched — only how much XP each level requires changed)."
        ))

    elif sub == "noxp":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        ignore_roles = gc.setdefault("xp_ignore_roles", [])
        action = args[0].lower() if args else ""
        if action == "add" and ctx.message.role_mentions:
            r = ctx.message.role_mentions[0]
            if r.id not in ignore_roles:
                ignore_roles.append(r.id)
                save_config(cfg)
            await ctx.send(embed=success_embed(f"Members with {r.mention} will no longer gain XP or level up."))
        elif action == "remove" and ctx.message.role_mentions:
            r = ctx.message.role_mentions[0]
            if r.id in ignore_roles:
                ignore_roles.remove(r.id)
                save_config(cfg)
            await ctx.send(embed=success_embed(f"{r.mention} can gain XP again."))
        elif action == "list":
            lines = []
            for rid in ignore_roles:
                role = ctx.guild.get_role(rid)
                lines.append(role.mention if role else f"`{rid}` (role no longer exists)")
            await ctx.send(embed=info_embed("No-XP Roles", "\n".join(lines) or "*(none — everyone gains XP normally)*"))
        else:
            await ctx.send(embed=info_embed("No-XP Roles", (
                "`level noxp add @role` — members with this role never gain XP or level up\n"
                "`level noxp remove @role` — let them gain XP again\n"
                "`level noxp list` — view all no-XP roles\n\n"
                "Useful for muted/timeout roles, bot-adjacent roles, or a dedicated \"NOXP\" role for people who opt out."
            )))

    elif sub == "setchannel":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        if not args:
            gc["level_channel"] = None
            save_config(cfg)
            return await ctx.send(embed=success_embed("Level channel disabled. Notifications will be sent to the active channel."))
        ch = None
        if ctx.message.channel_mentions:
            ch = ctx.message.channel_mentions[0]
        elif args[0].isdigit():
            ch = ctx.guild.get_channel(int(args[0]))
        if not ch:
            return await ctx.send(embed=error_embed("Channel not found. Use a #mention or channel ID."))
        gc["level_channel"] = ch.id
        save_config(cfg)
        await ctx.send(embed=success_embed("Level-up notifications will be sent to " + ch.mention + "."))

    elif sub == "role":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        level_roles = gc.setdefault("level_roles", {})
        action = args[0].lower() if args else ""

        if action == "set":
            if len(args) < 3 or not args[1].isdigit():
                return await ctx.send(embed=error_embed("Usage: `level role set <level> <@role/role_id>`"))
            lvl = int(args[1])
            role = None
            if ctx.message.role_mentions:
                role = ctx.message.role_mentions[0]
            elif args[2].isdigit():
                role = ctx.guild.get_role(int(args[2]))
            if not role:
                return await ctx.send(embed=error_embed("Role not found."))
            level_roles[str(lvl)] = role.id
            save_config(cfg)
            return await ctx.send(embed=success_embed(f"Members who reach **Level {lvl}** will now automatically get the {role.mention} role."))

        elif action == "remove":
            if len(args) < 2 or not args[1].isdigit():
                return await ctx.send(embed=error_embed("Usage: `level role remove <level>`"))
            lvl = args[1]
            if lvl not in level_roles:
                return await ctx.send(embed=error_embed(f"There's no role reward set for level {lvl}."))
            level_roles.pop(lvl, None)
            save_config(cfg)
            return await ctx.send(embed=success_embed(f"Role reward for level {lvl} removed. Roles members already have won't be revoked."))

        elif action == "list":
            if not level_roles:
                return await ctx.send(embed=info_embed("Level Role Rewards", "No role rewards have been set yet."))
            lines = []
            for lvl in sorted(level_roles, key=lambda x: int(x)):
                role = ctx.guild.get_role(level_roles[lvl])
                lines.append(f"**Level {lvl}** → {role.mention if role else '*(role not found)*'}")
            return await ctx.send(embed=info_embed("Level Role Rewards", "\n".join(lines)))

        else:
            await ctx.send(embed=info_embed("Level Role Rewards", (
                "`level role set <level> <@role>` — auto-grant a role once a member reaches that level\n"
                "`level role remove <level>` — remove that level's reward\n"
                "`level role list` — view all active rewards\n\n"
                "Roles stack — once granted, they won't be revoked even if the reward is later changed/removed."
            )))

    elif sub == "status":
        enabled  = gc.get("leveling_enabled", True)
        lvl_ch   = ctx.guild.get_channel(gc["level_channel"]) if gc.get("level_channel") else None
        xp_range = gc.get("xp_per_message", [15, 25])
        cooldown = gc.get("xp_cooldown", 60)
        difficulty = gc.get("xp_difficulty", 1.0)
        embed = base_embed("Leveling Status", None, COLOR_SUCCESS if enabled else COLOR_ERROR)
        embed.add_field(name="Status",     value="Enabled" if enabled else "Disabled",             inline=True)
        embed.add_field(name="Channel",    value=lvl_ch.mention if lvl_ch else "Current channel",  inline=True)
        embed.add_field(name="XP/Message", value=str(xp_range[0]) + "-" + str(xp_range[1]) + " XP", inline=True)
        embed.add_field(name="Cooldown",   value=str(cooldown) + " seconds",                       inline=True)
        embed.add_field(name="Difficulty", value=f"{difficulty}x",                                 inline=True)
        embed.add_field(name="No-XP Roles", value=str(len(gc.get("xp_ignore_roles", []))) + " role(s)", inline=True)
        await ctx.send(embed=embed)

    else:
        enabled = gc.get("leveling_enabled", True)
        status  = "Enabled" if enabled else "Disabled"
        await ctx.send(embed=info_embed("Level System",
            "Status: **" + status + "**\n\n"
            "`level toggle` - turn leveling on/off\n"
            "`level setchannel #channel` - set the notification channel\n"
            "`level setchannel` - disable the channel override\n"
            "`level xp <min> <max>` - set XP earned per message\n"
            "`level cooldown <seconds>` - set time between XP gains\n"
            "`level difficulty <multiplier>` - scale how much XP each level needs\n"
            "`level noxp add/remove/list @role` - exclude a role from gaining XP entirely\n"
            "`level role set/remove/list` - manage per-level role rewards\n"
            "`level message set/show/reset` - customize the level-up notification\n"
            "`level status` - view current configuration\n"
            "`level rank [@user]` - view rank\n"
            "`level leaderboard` - top 10"))
@bot.command(name="xp", aliases=["exp"])
async def pfx_xp(ctx, sub: str = "", *args):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    sub  = sub.lower()
    gc   = guild_cfg(cfg, ctx.guild.id)
    VALID = ("add","remove","set","setlevel","reset")
    if sub not in VALID:
        return await ctx.send(embed=info_embed("XP", "`xp add/remove/set @user <amount>` · `xp setlevel @user <lvl>` · `xp reset @user`"))
    if not args:
        return await ctx.send(embed=error_embed(f"Usage: `xp {sub} @user [amount]`"))
    try:
        member = ctx.guild.get_member(int(args[0].strip("<@!>")))
        if not member: return await ctx.send(embed=error_embed("Member not found."))
    except ValueError:
        return await ctx.send(embed=error_embed("Please provide a valid mention or ID."))
    data = get_member_xp(gc, str(member.id))
    if sub == "reset":
        gc["members_xp"][str(member.id)] = {"xp":0,"level":0,"last_msg_ts":0.0,"messages":0}
        save_config(cfg)
        return await ctx.send(embed=success_embed(f"XP for {member.mention} has been reset."))
    if len(args) < 2:
        return await ctx.send(embed=error_embed(f"Usage: `xp {sub} @user <amount>`"))
    try:
        amount = int(args[1])
    except ValueError:
        return await ctx.send(embed=error_embed("Amount must be a number."))
    diff = gc.get("xp_difficulty", 1.0)
    if sub == "add":
        data["xp"] = max(0, data["xp"] + amount)
        data["level"] = level_from_xp(data["xp"], diff)
        save_config(cfg)
        await ctx.send(embed=success_embed(f"+{amount} XP to {member.mention} (Total: {data['xp']:,} · Level {data['level']})"))
    elif sub == "remove":
        data["xp"] = max(0, data["xp"] - amount)
        data["level"] = level_from_xp(data["xp"], diff)
        save_config(cfg)
        await ctx.send(embed=success_embed(f"-{amount} XP from {member.mention} (Total: {data['xp']:,} · Level {data['level']})"))
    elif sub == "set":
        data["xp"] = max(0, amount)
        data["level"] = level_from_xp(data["xp"], diff)
        save_config(cfg)
        await ctx.send(embed=success_embed(f"XP {member.mention} → {amount:,} (Level {data['level']})"))
    elif sub == "setlevel":
        if not 0 <= amount <= 999: return await ctx.send(embed=error_embed("Level must be between 0 and 999."))
        total = sum(xp_for_level(lv, diff) for lv in range(amount))
        data["xp"] = total; data["level"] = amount
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Level {member.mention} → **{amount}** ({total:,} XP)"))

# ── TICKET ────────────────────────────────────────────────────────

@bot.command(name="ticket", aliases=["tix"])
async def pfx_ticket(ctx, sub: str = "", *, rest: str = ""):
    sub    = sub.lower()
    gc     = guild_cfg(cfg, ctx.guild.id)
    panels = gc["ticket"]["panels"]

    def is_manager() -> bool:
        return ctx.author.id == bot.owner_id or ctx.author.guild_permissions.manage_guild

    def parse_title_desc(body: str, fallback_title: str, fallback_desc: str):
        if "|" in body:
            title, desc = body.split("|", 1)
            title, desc = title.strip(), desc.strip()
        else:
            title, desc = body.strip(), ""
        return (title or fallback_title), (desc or fallback_desc)

    if sub == "setup":
        if not is_manager():
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        parts = rest.split()
        if len(parts) < 3:
            return await ctx.send(embed=error_embed(
                "Usage: `ticket setup <panel_id> <category_id> <log_id> [role_id] [max]`\n"
                "Example: `ticket setup support 123456 654321 999999 3`"))
        panel_id = parts[0].lower()
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", panel_id):
            return await ctx.send(embed=error_embed("Panel ID can only contain lowercase letters, numbers, `-`, `_` (max 32 characters)."))
        try:
            cat_id  = int(parts[1]); log_id = int(parts[2])
            role_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
            max_t   = int(parts[4]) if len(parts) > 4 else 1
        except ValueError:
            return await ctx.send(embed=error_embed("Category ID / Log ID / Role ID must be numbers."))
        cat    = ctx.guild.get_channel(cat_id)
        log_ch = ctx.guild.get_channel(log_id)
        if not isinstance(cat, discord.CategoryChannel):
            return await ctx.send(embed=error_embed("Category channel not found."))
        if not log_ch:
            return await ctx.send(embed=error_embed("Log channel not found."))
        panel = panels.setdefault(panel_id, {
            "title": "Support Tickets", "description": "Click the button below to open a support ticket.",
            "message_id": None, "channel_id": None
        })
        panel.update({
            "category": cat_id, "log_channel": log_id,
            "support_role": role_id, "max_tickets": max(1, min(5, max_t))
        })
        save_config(cfg)
        embed = base_embed(f"Ticket Panel `{panel_id}` Configured", None)
        embed.add_field(name="Category",     value=cat.name,                                inline=True)
        embed.add_field(name="Log Channel",  value=log_ch.mention,                          inline=True)
        embed.add_field(name="Support Role", value=f"<@&{role_id}>" if role_id else "None",  inline=True)
        embed.add_field(name="Max Tickets",  value=str(panel["max_tickets"]),                inline=True)
        embed.set_footer(text=f"Next: ticket panel {panel_id} <title> | <description>")
        await ctx.send(embed=embed)

    elif sub == "panel":
        if not is_manager():
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        parts = rest.split(maxsplit=1)
        if not parts:
            return await ctx.send(embed=error_embed("Usage: `ticket panel <panel_id> <title> | <description>`"))
        panel_id = parts[0].lower()
        panel    = panels.get(panel_id)
        if not panel or not panel.get("category"):
            return await ctx.send(embed=error_embed(f"Panel `{panel_id}` hasn't been set up yet. Run `ticket setup` first."))
        title, desc = parse_title_desc(parts[1] if len(parts) > 1 else "", panel["title"], panel["description"])
        panel["title"], panel["description"] = title, desc
        msg = await ctx.send(view=TicketPanelLayout(panel_id, panel, guild=ctx.guild))
        panel["message_id"], panel["channel_id"] = msg.id, msg.channel.id
        save_config(cfg)

    elif sub == "edit":
        if not is_manager():
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return await ctx.send(embed=error_embed("Usage: `ticket edit <panel_id> <title> | <description>`"))
        panel_id = parts[0].lower()
        panel    = panels.get(panel_id)
        if not panel:
            return await ctx.send(embed=error_embed(f"Panel `{panel_id}` not found."))
        title, desc = parse_title_desc(parts[1], panel["title"], panel["description"])
        panel["title"], panel["description"] = title, desc
        save_config(cfg)
        edited = False
        if panel.get("message_id") and panel.get("channel_id"):
            ch = ctx.guild.get_channel(panel["channel_id"])
            if ch:
                try:
                    msg = await ch.fetch_message(panel["message_id"])
                    await msg.edit(view=TicketPanelLayout(panel_id, panel, guild=ctx.guild))
                    edited = True
                except Exception:
                    pass
        note = "The panel message was updated too." if edited else "Config saved, but the old panel message wasn't found/was deleted — resend it with `ticket panel`."
        await ctx.send(embed=success_embed(f"Panel `{panel_id}` updated.\n{note}"))

    elif sub == "welcome":
        if not is_manager():
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            panel_id = parts[0].lower() if parts else ""
            panel    = panels.get(panel_id)
            current  = panel.get("welcome_message") if panel else None
            return await ctx.send(embed=info_embed("Ticket Welcome Message", (
                "Usage: `ticket welcome <panel_id> <message>`\n\n"
                "This is shown INSIDE the ticket channel once it's opened — separate from the panel's "
                "public description, so you're not stuck repeating the same text twice.\n"
                "Placeholders: `{user}` `{server}` `{panel}`\n\n"
                + (f"Current for `{panel_id}`:\n```{current}```" if current else "")
            )))
        panel_id = parts[0].lower()
        panel    = panels.get(panel_id)
        if not panel:
            return await ctx.send(embed=error_embed(f"Panel `{panel_id}` not found."))
        panel["welcome_message"] = parts[1].strip()
        save_config(cfg)
        preview = (parts[1]
                   .replace("{user}", ctx.author.mention)
                   .replace("{server}", ctx.guild.name)
                   .replace("{panel}", panel.get("title") or panel_id))
        await ctx.send(embed=success_embed(f"Welcome message for `{panel_id}` updated.\n\n**Preview:**\n{preview}"))

    elif sub == "list":
        if not panels:
            return await ctx.send(embed=info_embed("Ticket Panels", "No panels have been set up yet."))
        embed = base_embed("Ticket Panels", None)
        for pid, p in panels.items():
            cat = ctx.guild.get_channel(p.get("category"))    if p.get("category")    else None
            log = ctx.guild.get_channel(p.get("log_channel")) if p.get("log_channel") else None
            embed.add_field(
                name=f"`{pid}` — {p.get('title', '?')}",
                value=f"Category: {cat.mention if cat else '—'} · Log: {log.mention if log else '—'} · Max: {p.get('max_tickets', 1)}",
                inline=False
            )
        await ctx.send(embed=embed)

    elif sub == "delete":
        if not is_manager():
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        panel_id = rest.strip().lower()
        if panel_id not in panels:
            return await ctx.send(embed=error_embed(f"Panel `{panel_id}` not found."))
        del panels[panel_id]
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Panel `{panel_id}` deleted. Tickets still open from this panel won't be closed automatically."))

    elif sub == "close":
        async def _respond(**kw):
            await ctx.send(**kw)
        await close_ticket_channel(ctx.guild, ctx.channel, ctx.author, rest.strip(), _respond)

    else:
        await ctx.send(embed=info_embed("Ticket", (
            "`ticket setup <panel_id> <cat_id> <log_id> [role_id] [max]`\n"
            "`ticket panel <panel_id> <title> | <description>`\n"
            "`ticket edit <panel_id> <title> | <description>`\n"
            "`ticket welcome <panel_id> <message>` — customize the message shown INSIDE the ticket (separate from the panel's public description)\n"
            "`ticket list`\n"
            "`ticket delete <panel_id>`\n"
            "`ticket close [reason]`\n\n"
            "Each panel has its own category, log channel, and support role — "
            "so every ticket type can have its logs kept separate.\n"
            "Every ticket has **Claim** and **Close** buttons, so staff can mark who's handling it."
        )))

# ── GIVEAWAY ─────────────────────────────────────────────────────

@bot.command(name="giveaway", aliases=["gw"])
async def pfx_giveaway(ctx, sub: str = "", *args):
    sub = sub.lower()
    if sub == "list":
        gws = [gw for gw in active_giveaways.values() if gw.get("guild_id") == ctx.guild.id]
        if not gws: return await ctx.send(embed=info_embed("Giveaways", "No active giveaways."))
        embed = discord.Embed(title="Active Giveaways", color=COLOR_PRIMARY, timestamp=discord.utils.utcnow())
        for gw in gws[:10]:
            ends_dt = datetime.datetime.utcfromtimestamp(gw["ends_ts"]).replace(tzinfo=datetime.timezone.utc)
            ch = bot.get_channel(gw["channel_id"])
            embed.add_field(name=gw["prize"],
                value=f"Channel: {ch.mention if ch else '?'} · Ends: {discord.utils.format_dt(ends_dt,'R')}\nWinners: {gw['winner_count']} · Entries: {len(gw['entries'])} · ID: `{gw['message_id']}`",
                inline=False)
        await ctx.send(embed=embed)
    elif sub == "end":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        if not args: return await ctx.send(embed=error_embed("Usage: `giveaway end <message_id>`"))
        try: mid = int(args[0])
        except ValueError: return await ctx.send(embed=error_embed("ID must be a number."))
        gw = active_giveaways.get(mid)
        if not gw or gw["guild_id"] != ctx.guild.id: return await ctx.send(embed=error_embed("Giveaway not found."))
        await ctx.send(embed=info_embed("", f"Ending **{gw['prize']}**..."))
        await end_giveaway(gw)
    elif sub == "reroll":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        if not args: return await ctx.send(embed=error_embed("Usage: `giveaway reroll <message_id> [count]`"))
        try: mid = int(args[0]); count = int(args[1]) if len(args) > 1 else 1
        except ValueError: return await ctx.send(embed=error_embed("ID and count must be numbers."))
        gw = cfg.get("giveaway_history", {}).get(str(mid))
        if not gw or gw.get("guild_id") != ctx.guild.id:
            return await ctx.send(embed=error_embed("No ended giveaway found with that message ID — `giveaway reroll` only works on giveaways that already finished."))
        entries = list(set(gw.get("entries", [])))
        if not entries: return await ctx.send(embed=error_embed("That giveaway had no entries."))
        count   = max(1, min(count, len(entries)))
        winners = random.sample(entries, count)
        ws      = " ".join(f"<@{w}>" for w in winners)
        embed   = discord.Embed(title=f"{e(ICON_WINNER, '🏆')} Giveaway Rerolled!".strip(), description=f"**{gw['prize']}**\n\nNew winner(s): {ws}", color=COLOR_SUCCESS, timestamp=discord.utils.utcnow())
        embed.set_footer(text=BOT_NAME)
        await ctx.send(content=ws, embed=embed)
    elif sub == "start":
        if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
            return await ctx.send(embed=error_embed("You don't have permission to use this command."))
        if len(args) < 3:
            return await ctx.send(embed=error_embed("Usage: `giveaway start <duration> <winners> <prize>`\nOptional: `--role <id>` `--winrole <id>`"))
        dur_str = args[0].lower()
        m_dur   = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?", dur_str)
        if not m_dur or not any(m_dur.group(x) for x in (1,2,3)):
            return await ctx.send(embed=error_embed("Duration format: `1h`, `30m`, `2h30m`, `1d`"))
        dur_secs = int(m_dur.group(1) or 0)*86400 + int(m_dur.group(2) or 0)*3600 + int(m_dur.group(3) or 0)*60
        if not dur_secs or dur_secs > 7*86400:
            return await ctx.send(embed=error_embed("Duration must be between 1 minute and 7 days."))
        try:
            winner_count = int(args[1])
            if not 1 <= winner_count <= 20: raise ValueError
        except ValueError:
            return await ctx.send(embed=error_embed("Winners must be between 1 and 20."))
        rest = list(args[2:]); req_role_id = win_role_id = None; prize_parts = []; i = 0
        while i < len(rest):
            if rest[i] == "--role" and i+1 < len(rest):
                try: req_role_id = int(rest[i+1])
                except ValueError: pass
                i += 2
            elif rest[i] == "--winrole" and i+1 < len(rest):
                try: win_role_id = int(rest[i+1])
                except ValueError: pass
                i += 2
            else:
                prize_parts.append(rest[i]); i += 1
        prize = " ".join(prize_parts).strip()
        if not prize: return await ctx.send(embed=error_embed("Prize name can't be empty."))
        req_role = ctx.guild.get_role(req_role_id) if req_role_id else None
        win_role = ctx.guild.get_role(win_role_id) if win_role_id else None
        ends_ts  = discord.utils.utcnow().timestamp() + dur_secs
        gw = {"prize": prize, "description": "", "winner_count": winner_count,
              "host_id": ctx.author.id, "channel_id": ctx.channel.id, "guild_id": ctx.guild.id,
              "ends_ts": ends_ts, "entries": [], "winners": [], "ended": False, "message_id": 0,
              "required_role": req_role.id if req_role else None,
              "winner_role_id": win_role.id if win_role else None}
        gw_embed = build_giveaway_embed(gw)
        try:
            msg = await ctx.channel.send(embed=gw_embed)
        except discord.Forbidden:
            return await ctx.send(embed=error_embed("The bot can't send messages in this channel."))
        gw["message_id"] = msg.id
        view = GiveawayView(msg.id)
        try:
            await msg.edit(view=view)
        except Exception:
            pass
        bot.add_view(view)
        active_giveaways[msg.id] = gw
        save_giveaways()
        schedule_giveaway_end(gw)
        ends_dt = datetime.datetime.utcfromtimestamp(ends_ts).replace(tzinfo=datetime.timezone.utc)
        confirm = success_embed(f"Giveaway started!\n\nPrize: {prize}\nWinners: {winner_count}\nEnds: {discord.utils.format_dt(ends_dt,'R')}")
        if req_role: confirm.add_field(name="Required Role", value=req_role.mention, inline=True)
        if win_role: confirm.add_field(name="Winner Role",   value=win_role.mention, inline=True)
        await ctx.send(embed=confirm)
    else:
        await ctx.send(embed=info_embed("Giveaway",
            "`giveaway start <duration> <winners> <prize>`\n"
            "  Optional: `--role <id>` `--winrole <id>`\n"
            "`giveaway end <msg_id>` · `giveaway reroll <msg_id>` · `giveaway list`"))

# ── ANTISPAM HONEYPOT ─────────────────────────────────────────────

@bot.command(name="autoresponse", aliases=["arp", "autoreply"])
async def pfx_autoresponse(ctx, action: str = "", *, rest: str = ""):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    gc      = guild_cfg(cfg, ctx.guild.id)
    entries = gc.setdefault("autoresponses", {})
    action  = action.lower()

    if action == "add":
        parts = rest.split(maxsplit=1)
        if len(parts) < 2 or "|" not in parts[1]:
            return await ctx.send(embed=error_embed(
                "Usage: `autoresponse add <trigger> | <response>`\n"
                "Example: `autoresponse add discord.gg/ | Please don't post invite links here.`"
            ))
        trigger_raw = parts[0]
        response    = parts[1].split("|", 1)[1].strip()
        if not response:
            return await ctx.send(embed=error_embed("The response text can't be empty."))
        key = trigger_raw.lower()
        entries[key] = {"trigger": trigger_raw, "response": response, "match": "contains", "case_sensitive": False}
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Auto-response added for trigger `{trigger_raw}`."))

    elif action == "remove":
        key = rest.strip().lower()
        if key not in entries:
            return await ctx.send(embed=error_embed(f"No auto-response found for trigger `{rest.strip()}`. Check `autoresponse list` for exact triggers."))
        del entries[key]
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Auto-response for `{rest.strip()}` removed."))

    elif action == "match":
        parts = rest.split()
        if len(parts) != 2 or parts[1].lower() not in ("contains", "exact", "startswith"):
            return await ctx.send(embed=error_embed("Usage: `autoresponse match <trigger> <contains/exact/startswith>`"))
        key = parts[0].lower()
        if key not in entries:
            return await ctx.send(embed=error_embed(f"No auto-response found for trigger `{parts[0]}`."))
        entries[key]["match"] = parts[1].lower()
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Match type for `{parts[0]}` set to **{parts[1].lower()}**."))

    elif action == "list":
        if not entries:
            return await ctx.send(embed=info_embed("Auto-Responses", "No auto-responses configured yet."))
        lines = [f"**`{v['trigger']}`** ({v.get('match','contains')}) → {v['response'][:80]}" for v in entries.values()]
        await ctx.send(embed=info_embed(f"Auto-Responses ({len(entries)})", "\n".join(lines)))

    elif action == "toggle":
        gc["autoresponses_enabled"] = not gc.get("autoresponses_enabled", True)
        save_config(cfg)
        state = "enabled" if gc["autoresponses_enabled"] else "disabled"
        await ctx.send(embed=success_embed(f"Auto-responses are now **{state}** in this server."))

    else:
        await ctx.send(embed=info_embed("Auto-Response", (
            "`autoresponse add <trigger> | <response>` — reply automatically when a message contains the trigger\n"
            "`autoresponse remove <trigger>` — delete one\n"
            "`autoresponse match <trigger> <contains/exact/startswith>` — change how it matches (default: contains)\n"
            "`autoresponse list` — view all configured triggers\n"
            "`autoresponse toggle` — turn the whole system on/off\n\n"
            "Matching is case-insensitive by default and checks every message (not just commands)."
        )))

@bot.command(name="ignorechannel", aliases=["ignorech", "ic"])
async def pfx_ignorechannel(ctx, action: str = "", *, rest: str = ""):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    gc     = guild_cfg(cfg, ctx.guild.id)
    ignored = gc.setdefault("ignored_channels", [])
    action  = action.lower()

    def resolve_channel():
        if ctx.message.channel_mentions:
            return ctx.message.channel_mentions[0]
        target = rest.strip()
        if target.isdigit():
            return ctx.guild.get_channel(int(target))
        return ctx.channel if not target else None

    if action == "add":
        ch = resolve_channel()
        if not ch: return await ctx.send(embed=error_embed("Channel not found. Mention the channel or give its ID."))
        if ch.id not in ignored:
            ignored.append(ch.id)
            save_config(cfg)
        warn = ""
        if ch.id == ctx.channel.id:
            warn = f"\n\n⚠️ This is the channel you're using right now — no command will be responded to here anymore starting now, **including** `ignorechannel remove`. To re-enable it, run the command from another channel: `ignorechannel remove #{ch.name}`."
        await ctx.send(embed=success_embed(f"The bot is now completely silent in {ch.mention} — no commands, no XP, no responses at all.{warn}"))

    elif action == "remove":
        ch = resolve_channel()
        if not ch: return await ctx.send(embed=error_embed("Channel not found. Mention the channel or give its ID."))
        if ch.id in ignored:
            ignored.remove(ch.id)
            save_config(cfg)
            await ctx.send(embed=success_embed(f"The bot is active again in {ch.mention}."))
        else:
            await ctx.send(embed=error_embed(f"{ch.mention} isn't being ignored anyway."))

    elif action == "list":
        lines = []
        for cid in ignored:
            ch = ctx.guild.get_channel(cid)
            lines.append(ch.mention if ch else f"`{cid}` (channel no longer exists)")
        await ctx.send(embed=info_embed("Ignored Channels", "\n".join(lines) or "*(empty — the bot is active in every channel)*"))

    else:
        await ctx.send(embed=info_embed("Ignore Channel", (
            "`ignorechannel add [#channel]` — makes the bot fully silent in this channel (defaults to the current channel)\n"
            "`ignorechannel remove [#channel]` — re-enables it\n"
            "`ignorechannel list` — lists every ignored channel\n\n"
            "Different from `antispam ignore` — that skips PEOPLE from spam detection, "
            "this makes the bot not respond at all in a specific CHANNEL."
        )))

@bot.command(name="antispam", aliases=["as"])
async def pfx_antispam(ctx, sub: str = "", *, rest: str = ""):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    gc  = guild_cfg(cfg, ctx.guild.id)
    ac  = gc.setdefault("antispam", {})
    sub = sub.lower()

    if sub == "setchannel":
        if not rest.strip():
            ac["trap_channel"] = None
            save_config(cfg)
            return await ctx.send(embed=success_embed("Honeypot channel disabled."))
        ch = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else (ctx.guild.get_channel(int(rest.strip())) if rest.strip().isdigit() else None)
        if not ch: return await ctx.send(embed=error_embed("Channel not found."))
        ac["trap_channel"] = ch.id
        save_config(cfg)
        await ctx.send(embed=base_embed("Honeypot Active", ch.mention + " — anyone who sends a message here gets punished immediately.", color=COLOR_ERROR))

    elif sub == "logchannel":
        if not rest.strip():
            ac["log_channel"] = None
            save_config(cfg)
            return await ctx.send(embed=success_embed("Antispam log channel cleared."))
        ch = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else (ctx.guild.get_channel(int(rest.strip())) if rest.strip().isdigit() else None)
        if not ch: return await ctx.send(embed=error_embed("Channel not found."))
        ac["log_channel"] = ch.id
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Antispam reports (honeypot, cross-channel spam, flood) will be sent to {ch.mention}."))

    elif sub == "punishment":
        choice = rest.strip().lower()
        if choice not in ("ban", "kick", "timeout"):
            return await ctx.send(embed=error_embed("Choices: `ban`, `kick`, or `timeout`."))
        ac["punishment"] = choice
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Antispam punishment set to **{choice}**."))

    elif sub == "threshold":
        parts = rest.split()
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return await ctx.send(embed=error_embed(
                f"Usage: `antispam threshold <channel_count> <seconds>`\nCurrent: **{ac.get('threshold', SPAM_THRESHOLD)} channels / {ac.get('window', SPAM_WINDOW)}s**"))
        ac["threshold"], ac["window"] = int(parts[0]), int(parts[1])
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Cross-channel spam will now trigger when the same message/link appears in **{ac['threshold']} channels** within **{ac['window']} seconds**."))

    elif sub == "flood":
        parts = rest.split()
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return await ctx.send(embed=error_embed(
                f"Usage: `antispam flood <message_count> <seconds>`\nCurrent: **{ac.get('flood_count', 5)} messages / {ac.get('flood_window', 4)}s**"))
        ac["flood_count"], ac["flood_window"] = int(parts[0]), int(parts[1])
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Flood detection will now trigger on **{ac['flood_count']} messages** within **{ac['flood_window']} seconds** in the same channel."))

    elif sub == "ignore":
        parts  = rest.split(maxsplit=1)
        action = parts[0].lower() if parts else ""
        target_str = parts[1] if len(parts) > 1 else ""
        users = ac.setdefault("ignore_users", [])
        roles = ac.setdefault("ignore_roles", [])
        if action == "add":
            if ctx.message.mentions:
                u = ctx.message.mentions[0]
                if u.id not in users: users.append(u.id)
                save_config(cfg)
                return await ctx.send(embed=success_embed(f"{u.mention} is now skipped from all antispam detection."))
            if ctx.message.role_mentions:
                r = ctx.message.role_mentions[0]
                if r.id not in roles: roles.append(r.id)
                save_config(cfg)
                return await ctx.send(embed=success_embed(f"Role {r.mention} is now skipped from all antispam detection."))
            return await ctx.send(embed=error_embed("Mention the user or role you want to ignore."))
        elif action == "remove":
            if ctx.message.mentions:
                u = ctx.message.mentions[0]
                if u.id in users: users.remove(u.id)
                save_config(cfg)
                return await ctx.send(embed=success_embed(f"{u.mention} removed from the ignore list."))
            if ctx.message.role_mentions:
                r = ctx.message.role_mentions[0]
                if r.id in roles: roles.remove(r.id)
                save_config(cfg)
                return await ctx.send(embed=success_embed(f"Role {r.mention} removed from the ignore list."))
            return await ctx.send(embed=error_embed("Mention the user or role you want to remove from the ignore list."))
        elif action == "list":
            u_lines = [f"<@{uid}>" for uid in users] or ["*(empty)*"]
            r_lines = [f"<@&{rid}>" for rid in roles] or ["*(empty)*"]
            embed = base_embed("Antispam Ignore List", None)
            embed.add_field(name="Users", value="\n".join(u_lines), inline=False)
            embed.add_field(name="Roles", value="\n".join(r_lines), inline=False)
            embed.set_footer(text="The bot owner and anyone with Manage Server are always skipped too.")
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=info_embed("Antispam Ignore", "`antispam ignore add @user/@role`\n`antispam ignore remove @user/@role`\n`antispam ignore list`"))

    elif sub == "status":
        trap_ch = ctx.guild.get_channel(ac.get("trap_channel")) if ac.get("trap_channel") else None
        log_ch  = ctx.guild.get_channel(ac.get("log_channel"))  if ac.get("log_channel")  else None
        embed = base_embed("Antispam Status", None, color=COLOR_ERROR if trap_ch else COLOR_INFO)
        embed.add_field(name="Honeypot Channel", value=trap_ch.mention if trap_ch else "*(inactive)*", inline=True)
        embed.add_field(name="Log Channel", value=log_ch.mention if log_ch else "*(not set)*", inline=True)
        embed.add_field(name="Punishment", value=f"`{ac.get('punishment', 'ban')}`", inline=True)
        embed.add_field(name="Cross-Channel Threshold", value=f"{ac.get('threshold', SPAM_THRESHOLD)} channels / {ac.get('window', SPAM_WINDOW)}s", inline=True)
        embed.add_field(name="Flood Threshold", value=f"{ac.get('flood_count', 5)} messages / {ac.get('flood_window', 4)}s", inline=True)
        embed.add_field(name="Ignore List", value=f"{len(ac.get('ignore_users', []))} user, {len(ac.get('ignore_roles', []))} role", inline=True)
        await ctx.send(embed=embed)

    else:
        await ctx.send(embed=info_embed("Antispam", (
            "`antispam setchannel #channel` — honeypot trap (omit the argument to disable)\n"
            "`antispam logchannel #channel` — where reports get sent\n"
            "`antispam punishment ban/kick/timeout` — action taken against offenders\n"
            "`antispam threshold <channels> <seconds>` — cross-channel spam sensitivity\n"
            "`antispam flood <messages> <seconds>` — message flood sensitivity\n"
            "`antispam ignore add/remove/list @user/@role` — skip from detection\n"
            "`antispam status` — view the current configuration"
        )))

# ── ANTI-NUKE ────────────────────────────────────────────────────

@bot.command(name="automod", aliases=["am"])
async def pfx_automod(ctx, sub: str = "", *, rest: str = ""):
    """Bikin AutoMod Rule Discord asli lewat API (bukan deteksi manual bot).
    Sekali bot ini berhasil bikin 1 rule di server manapun, Discord otomatis
    kasih badge 'Uses AutoMod' di profile bot — permanen, gak perlu diulang."""
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
        return await ctx.send(embed=error_embed("You don't have permission to use this command."))
    if not ctx.guild.me.guild_permissions.manage_guild:
        return await ctx.send(embed=error_embed("The bot needs the **Manage Server** permission to create AutoMod rules."))
    sub = sub.lower()

    if sub == "setup":
        try:
            existing = await ctx.guild.fetch_automod_rules()
        except Exception:
            existing = []
        if any(r.creator_id == bot.user.id for r in existing):
            return await ctx.send(embed=error_embed("This server already has an AutoMod rule created by this bot. Use `automod list` to see it."))

        actions = [discord.AutoModRuleAction(type=discord.AutoModRuleActionType.block_message)]
        try:
            rule = await ctx.guild.create_automod_rule(
                name=f"{BOT_NAME} — Blocked Content",
                event_type=discord.AutoModRuleEventType.message_send,
                trigger=discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.keyword, presets=discord.AutoModPresets(profanity=True, sexual_content=True, slurs=True)),
                actions=actions,
                enabled=True,
                reason=f"[{BOT_NAME}] AutoMod setup"
            )
        except discord.Forbidden:
            return await ctx.send(embed=error_embed("The bot doesn't have permission to create AutoMod rules in this server."))
        except Exception as e:
            logging.exception(f"[{BOT_NAME}] Failed to create AutoMod rule")
            return await ctx.send(embed=error_embed(f"Failed to create the rule: {e}"))

        await ctx.send(embed=success_embed(
            f"AutoMod rule **{rule.name}** created and enabled — blocks profanity, sexual content, and slurs automatically.\n\n"
            "This uses Discord's native AutoMod (not the bot's own spam detection), so it also unlocks the "
            "**\"Uses AutoMod\"** badge on this bot's profile."
        ))

    elif sub == "list":
        try:
            rules = await ctx.guild.fetch_automod_rules()
        except Exception:
            rules = []
        if not rules:
            return await ctx.send(embed=info_embed("AutoMod Rules", "No AutoMod rules exist in this server yet."))
        mine   = [r for r in rules if r.creator_id == bot.user.id]
        others = [r for r in rules if r.creator_id != bot.user.id]
        embed  = base_embed("AutoMod Rules", None)
        if mine:
            embed.add_field(name=f"Created by {BOT_NAME}", value="\n".join(
                f"**{r.name}** — {'enabled' if r.enabled else 'disabled'} (`{r.id}`)" for r in mine
            ), inline=False)
        if others:
            embed.add_field(name="Other rules in this server (not made by this bot)", value="\n".join(
                f"**{r.name}** — {'enabled' if r.enabled else 'disabled'} (`{r.id}`) · creator: <@{r.creator_id}>" for r in others
            ), inline=False)
            embed.set_footer(text="These weren't created by this bot — they're Discord defaults, another bot, or set up manually via Server Settings.")
        await ctx.send(embed=embed)

    elif sub == "remove":
        rule_id = rest.strip()
        if not rule_id.isdigit():
            return await ctx.send(embed=error_embed("Usage: `automod remove <rule_id>` — get the ID from `automod list`."))
        try:
            rules = await ctx.guild.fetch_automod_rules()
            rule = next((r for r in rules if r.id == int(rule_id)), None)
            if not rule:
                return await ctx.send(embed=error_embed("Rule not found."))
            if rule.creator_id != bot.user.id:
                return await ctx.send(embed=error_embed(
                    f"**{rule.name}** wasn't created by this bot (creator: <@{rule.creator_id}>), "
                    "so it won't be removed from here — manage it directly in Server Settings > AutoMod instead."
                ))
            await rule.delete(reason=f"[{BOT_NAME}] Removed via automod remove")
            await ctx.send(embed=success_embed(f"AutoMod rule **{rule.name}** removed."))
        except Exception as e:
            await ctx.send(embed=error_embed(f"Failed to remove the rule: {e}"))

    else:
        await ctx.send(embed=info_embed("AutoMod", (
            "`automod setup` — create a native Discord AutoMod rule (blocks profanity/sexual content/slurs) "
            "and unlocks the bot's \"Uses AutoMod\" profile badge\n"
            "`automod list` — view every AutoMod rule in this server (including ones not made by this bot)\n"
            "`automod remove <rule_id>` — delete a rule this bot created\n\n"
            "This is different from `antispam`/`antinuke` — those are the bot's own custom detection. "
            "This uses Discord's built-in AutoMod system directly."
        )))

@bot.command(name="antinuke", aliases=["an"])
async def pfx_antinuke(ctx, sub: str = "", *, rest: str = ""):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.administrator:
        return await ctx.send(embed=error_embed("Only Administrators or the owner can configure anti-nuke."))
    gc  = guild_cfg(cfg, ctx.guild.id)
    ac  = gc.setdefault("antinuke", {"enabled": False, "log_channel": None, "whitelist": [], "punishment": "strip_roles"})
    sub = sub.lower()

    if sub == "enable":
        me = ctx.guild.me
        if not me.guild_permissions.view_audit_log:
            return await ctx.send(embed=error_embed("The bot needs the **View Audit Log** permission before anti-nuke can be enabled."))
        ac["enabled"] = True
        save_config(cfg)
        await ctx.send(embed=success_embed(
            "Anti-Nuke is now **ENABLED**.\nDetects: mass channel delete/create, mass role delete, mass ban/kick, "
            "mass webhook create, and sudden Administrator permission grants.\n\n"
            "Don't forget: `antinuke logchannel #channel` so you get a report when it triggers."
        ))

    elif sub == "disable":
        ac["enabled"] = False
        save_config(cfg)
        await ctx.send(embed=success_embed("Anti-Nuke disabled."))

    elif sub == "logchannel":
        ch = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else None
        if not ch:
            ac["log_channel"] = None
            save_config(cfg)
            return await ctx.send(embed=success_embed("Anti-Nuke log channel cleared."))
        ac["log_channel"] = ch.id
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Anti-Nuke reports will be sent to {ch.mention}."))

    elif sub == "punishment":
        choice = rest.strip().lower()
        if choice not in ("strip_roles", "kick", "ban"):
            return await ctx.send(embed=error_embed("Choices: `strip_roles`, `kick`, or `ban`."))
        ac["punishment"] = choice
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Anti-Nuke punishment set to **{choice}**."))

    elif sub == "whitelist":
        parts  = rest.split(maxsplit=1)
        action = parts[0].lower() if parts else ""
        wl     = ac.setdefault("whitelist", [])
        if action == "add" and ctx.message.mentions:
            u = ctx.message.mentions[0]
            if u.id not in wl:
                wl.append(u.id)
                save_config(cfg)
            await ctx.send(embed=success_embed(f"{u.mention} is now whitelisted from anti-nuke."))
        elif action == "remove" and ctx.message.mentions:
            u = ctx.message.mentions[0]
            if u.id in wl:
                wl.remove(u.id)
                save_config(cfg)
            await ctx.send(embed=success_embed(f"{u.mention} removed from the whitelist."))
        elif action == "list":
            lines = [f"<@{uid}>" for uid in wl] or ["*(empty)*"]
            await ctx.send(embed=info_embed("Anti-Nuke Whitelist", "\n".join(lines)))
        else:
            await ctx.send(embed=info_embed("Anti-Nuke Whitelist", "`antinuke whitelist add @user`\n`antinuke whitelist remove @user`\n`antinuke whitelist list`"))

    elif sub == "status":
        status = "🟢 Enabled" if ac.get("enabled") else "🔴 Disabled"
        log_ch = ctx.guild.get_channel(ac.get("log_channel")) if ac.get("log_channel") else None
        embed = base_embed("Anti-Nuke Status", None, color=COLOR_ERROR if ac.get("enabled") else COLOR_INFO)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Punishment", value=f"`{ac.get('punishment','strip_roles')}`", inline=True)
        embed.add_field(name="Log Channel", value=log_ch.mention if log_ch else "*(not set)*", inline=True)
        embed.add_field(name="Whitelist", value=str(len(ac.get("whitelist", []))) + " user(s)", inline=True)
        embed.add_field(name="Detection", value="\n".join(f"• {v}" for v in antinuke.ACTION_LABELS.values()), inline=False)
        await ctx.send(embed=embed)

    else:
        await ctx.send(embed=info_embed("Anti-Nuke", (
            "`antinuke enable` — turn on protection\n"
            "`antinuke disable` — turn it off\n"
            "`antinuke logchannel #channel` — where reports get sent\n"
            "`antinuke punishment strip_roles/kick/ban` — action taken against offenders\n"
            "`antinuke whitelist add/remove/list @user` — people skipped from detection\n"
            "`antinuke status` — view the current configuration\n\n"
            "The bot owner and server owner are automatically whitelisted — no need to add them manually."
        )))

@bot.command(name="verification", aliases=["verify", "captcha"])
async def pfx_verification(ctx, sub: str = "", *, rest: str = ""):
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
        return await ctx.send(embed=error_embed("Only members with **Manage Server** or the owner can configure verification."))
    gc  = guild_cfg(cfg, ctx.guild.id)
    vc  = gc.setdefault("verification", {
        "enabled": False, "channel_id": None, "unverified_role_id": None,
        "verified_role_id": None, "log_channel_id": None, "message_id": None,
        "panel_message": "Click **Verify** below — I'll DM you a short captcha to unlock the rest of the server. Make sure your DMs are open!",
        "result_message": "Thanks for verifying — enjoy your stay!",
    })
    sub = sub.lower()

    if sub in ("channel", "setchannel"):
        ch = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else None
        if not ch:
            return await ctx.send(embed=error_embed("Mention a channel: `verification channel #channel`"))
        vc["channel_id"] = ch.id
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Verification channel set to {ch.mention}."))

    elif sub in ("unverifiedrole", "urole", "unverified"):
        role = ctx.message.role_mentions[0] if ctx.message.role_mentions else None
        if not role:
            return await ctx.send(embed=error_embed("Mention a role: `verification unverifiedrole @role`"))
        vc["unverified_role_id"] = role.id
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Unverified role set to {role.mention}."))

    elif sub in ("verifiedrole", "vrole", "verified"):
        role = ctx.message.role_mentions[0] if ctx.message.role_mentions else None
        if not role:
            return await ctx.send(embed=error_embed("Mention a role: `verification verifiedrole @role`"))
        vc["verified_role_id"] = role.id
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Verified role set to {role.mention}."))

    elif sub == "logchannel":
        ch = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else None
        if not ch:
            vc["log_channel_id"] = None
            save_config(cfg)
            return await ctx.send(embed=success_embed("Verification log channel cleared."))
        vc["log_channel_id"] = ch.id
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Verification logs will be sent to {ch.mention}."))

    elif sub == "message":
        text = rest.strip()
        if not text:
            return await ctx.send(embed=error_embed("Give me the text to show: `verification message <your text>`"))
        vc["panel_message"] = text[:1000]
        save_config(cfg)
        await ctx.send(embed=success_embed("Verification panel message updated. Run `verification send` to repost it with the new text."))

    elif sub == "resultmessage":
        text = rest.strip()
        if not text:
            return await ctx.send(embed=error_embed("Give me the text to show: `verification resultmessage <your text>`"))
        vc["result_message"] = text[:500]
        save_config(cfg)
        await ctx.send(embed=success_embed("Verification result message updated — shown on the detail embed members get after every attempt."))

    elif sub == "enable":
        missing = []
        if not vc.get("channel_id"):          missing.append("`verification channel #channel`")
        if not vc.get("unverified_role_id"):  missing.append("`verification unverifiedrole @role`")
        if not vc.get("verified_role_id"):    missing.append("`verification verifiedrole @role`")
        if missing:
            return await ctx.send(embed=error_embed(
                "Can't enable yet — still missing:\n" + "\n".join(missing) +
                "\n\nRun `verification status` any time to check your progress."
            ))
        me = ctx.guild.me
        if not me.guild_permissions.manage_roles:
            return await ctx.send(embed=error_embed("The bot needs the **Manage Roles** permission before verification can be enabled."))
        unver_role = ctx.guild.get_role(vc["unverified_role_id"])
        ver_role   = ctx.guild.get_role(vc["verified_role_id"])
        if not unver_role or not ver_role:
            return await ctx.send(embed=error_embed("One of the configured roles no longer exists — re-set it with `verification unverifiedrole`/`verifiedrole` first."))
        if unver_role >= me.top_role or ver_role >= me.top_role:
            return await ctx.send(embed=error_embed(
                "The bot's highest role needs to be **above** both the Unverified and Verified roles in the "
                "role list, otherwise it can't assign or remove them. Move the bot's role up and try again."
            ))
        vc["enabled"] = True
        save_config(cfg)
        ch = ctx.guild.get_channel(vc["channel_id"])
        await ctx.send(embed=success_embed(
            f"Verification is now **ENABLED**. New members will get {unver_role.mention} on join.\n"
            f"Run `verification send` in {ch.mention if ch else 'the verification channel'} to post the "
            "Verify button, if you haven't already."
        ))

    elif sub == "disable":
        vc["enabled"] = False
        save_config(cfg)
        await ctx.send(embed=success_embed(
            "Verification disabled. New members will no longer receive the Unverified role.\n"
            "-# Members who already have Unverified/Verified roles keep them — this only stops new assignments."
        ))

    elif sub == "send":
        if not vc.get("enabled"):
            return await ctx.send(embed=error_embed("Verification isn't enabled yet — run `verification enable` first."))
        ch = ctx.guild.get_channel(vc.get("channel_id") or 0)
        if not ch:
            return await ctx.send(embed=error_embed("Verification channel isn't set — run `verification channel #channel` first."))
        embed = base_embed(
            f"{e(ICON_VERIFICATION, '🔐')} Verification Required",
            vc.get("panel_message") or "Click **Verify** below — I'll DM you a short captcha to unlock the rest of the server. Make sure your DMs are open!",
            color=COLOR_PRIMARY
        )
        embed.set_footer(text=BOT_NAME)
        try:
            msg = await ch.send(embed=embed, view=VerificationView())
        except discord.Forbidden:
            return await ctx.send(embed=error_embed("The bot doesn't have permission to send messages in that channel."))
        vc["message_id"] = msg.id
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Verification panel posted in {ch.mention}."))

    elif sub == "status":
        status = "🟢 Enabled" if vc.get("enabled") else "🔴 Disabled"
        ch     = ctx.guild.get_channel(vc.get("channel_id") or 0)
        urole  = ctx.guild.get_role(vc.get("unverified_role_id") or 0)
        vrole  = ctx.guild.get_role(vc.get("verified_role_id") or 0)
        lch    = ctx.guild.get_channel(vc.get("log_channel_id") or 0)
        embed = base_embed("Verification Status", None, color=COLOR_SUCCESS if vc.get("enabled") else COLOR_INFO)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Channel", value=ch.mention if ch else "*(not set)*", inline=True)
        embed.add_field(name="Log Channel", value=lch.mention if lch else "*(not set)*", inline=True)
        embed.add_field(name="Unverified Role", value=urole.mention if urole else "*(not set)*", inline=True)
        embed.add_field(name="Verified Role", value=vrole.mention if vrole else "*(not set)*", inline=True)
        panel_msg  = vc.get("panel_message")  or "*(default)*"
        result_msg = vc.get("result_message") or "*(default)*"
        embed.add_field(name="Panel Message", value=panel_msg[:200], inline=False)
        embed.add_field(name="Result Message", value=result_msg[:200], inline=False)
        await ctx.send(embed=embed)

    else:
        await ctx.send(embed=info_embed("Verification Setup", (
            "`verification channel #channel` — where the Verify button gets posted\n"
            "`verification unverifiedrole @role` — role given to members automatically on join\n"
            "`verification verifiedrole @role` — role given once they solve the captcha\n"
            "`verification logchannel #channel` — *(optional)* log every successful verification\n"
            "`verification message <text>` — custom text shown on the verification panel embed\n"
            "`verification resultmessage <text>` — custom text shown on the pass/fail result embed\n"
            "`verification enable` — turn the feature on (needs the 3 items above set first)\n"
            "`verification disable` — turn it off (doesn't touch roles already given out)\n"
            "`verification send` — post/repost the Verify button in the configured channel\n"
            "`verification status` — view the current setup\n\n"
            "-# Nothing activates automatically — new members only start getting the Unverified "
            "role once you've configured everything above and run `enable`."
        )))

# ══════════════════════════════════════════════════════════════════
# EMBED BUILDER — compose a custom embed piece-by-piece, then send it
# to any channel. Draft state lives in memory per-user (like the AFK/
# captcha stores above) — it's a working scratchpad, not saved data, so
# there's no reason for it to survive a bot restart.
# ══════════════════════════════════════════════════════════════════

_EMBED_DRAFTS: dict = {}   # uid -> draft dict

SEPARATOR_STYLES = {
    "line":  "──────────────────────────────",
    "dots":  "· · · · · · · · · · · · · · · · · · · ·",
    "stars": "✦ ✦ ✦ ✦ ✦ ✦ ✦ ✦ ✦ ✦ ✦ ✦ ✦ ✦ ✦ ✦",
    "wave":  "〜〜〜〜〜〜〜〜〜〜〜〜〜〜〜〜〜〜〜〜〜〜",
}

def _get_embed_draft(uid: int) -> dict:
    return _EMBED_DRAFTS.setdefault(uid, {
        "title": None, "description": "", "thumbnail": None,
        "image": None, "color": COLOR_PRIMARY, "channel_id": None,
        "links": [], "target_message_id": None, "target_channel_id": None,
        "_history": [],
    })

# ── /component draft store — same generic snapshot/undo helpers above ──

_COMPONENT_DRAFTS: dict = {}   # uid -> draft dict

def _get_component_draft(uid: int) -> dict:
    return _COMPONENT_DRAFTS.setdefault(uid, {
        "component_id": None, "title": None, "description": "", "thumbnail": None,
        "image": None, "color": COLOR_PRIMARY, "channel_id": None,
        "buttons": [], "target_message_id": None, "target_channel_id": None,
        "_history": [],
    })

def _snapshot_draft(draft: dict) -> None:
    """Push a copy of the draft's current content onto its undo stack,
    right before applying a new change. Capped so it can't grow forever."""
    hist = draft.setdefault("_history", [])
    hist.append({k: v for k, v in draft.items() if k != "_history"})
    if len(hist) > 15:
        hist.pop(0)

def _undo_draft(draft: dict) -> bool:
    """Pop the last snapshot and restore it. Returns False if there's
    nothing to undo (fresh draft or already at the oldest state)."""
    hist = draft.get("_history", [])
    if not hist:
        return False
    prev = hist.pop()
    for k in list(draft.keys()):
        if k != "_history":
            draft.pop(k, None)
    draft.update(prev)
    return True

# ── /ticketpanel draft store — same generic snapshot/undo helpers above
# are reused as-is (they operate on any dict, regardless of schema) ────

_TICKET_DRAFTS: dict = {}   # uid -> draft dict

def _get_ticket_draft(uid: int) -> dict:
    return _TICKET_DRAFTS.setdefault(uid, {
        "panel_id": None, "category_id": None, "log_channel_id": None,
        "support_role_id": None, "max_tickets": 1,
        "title": "Support Tickets", "description": "Click the button below to open a support ticket.",
        "welcome_message": None, "thumbnail": None, "image": None, "color": COLOR_PRIMARY,
        "button_label": "Open Ticket", "button_emoji": "", "button_style": "danger",
        "open_type": "button", "_history": [],
    })

def _ticket_panel_summary(guild: discord.Guild, draft: dict) -> str:
    cat  = guild.get_channel(draft.get("category_id") or 0)
    log  = guild.get_channel(draft.get("log_channel_id") or 0)
    role = guild.get_role(draft.get("support_role_id") or 0)
    btn_emoji = draft.get("button_emoji") or "🎫"
    return (
        f"**Panel ID:** `{draft.get('panel_id')}`\n"
        f"**Category:** {cat.mention if cat else '*(not set)*'} · "
        f"**Log:** {log.mention if log else '*(not set)*'} · "
        f"**Role:** {role.mention if role else '*(none)*'} · "
        f"**Max tickets:** {draft.get('max_tickets', 1)}\n"
        f"**Control:** {btn_emoji} \"{draft.get('button_label') or 'Open Ticket'}\" "
        f"(`{draft.get('button_style', 'danger')}`) as a **{draft.get('open_type', 'button')}**"
    )

def _ticket_render_kwargs(guild: discord.Guild, draft: dict) -> dict:
    return {"content": _ticket_panel_summary(guild, draft), "embed": _build_ticket_panel_embed(draft)}

def _build_draft_embed(draft: dict) -> discord.Embed:
    embed = discord.Embed(color=draft.get("color") or COLOR_PRIMARY, timestamp=discord.utils.utcnow())
    if draft.get("title"):
        embed.title = draft["title"]
    if draft.get("description"):
        embed.description = draft["description"]
    if draft.get("thumbnail"):
        embed.set_thumbnail(url=draft["thumbnail"])
    if draft.get("image"):
        embed.set_image(url=draft["image"])
    embed.set_footer(text=BOT_NAME)
    return embed

def build_embed_layout(draft: dict) -> discord.ui.LayoutView:
    """Components V2 rendering of the /embed builder's FINAL output —
    title, description, thumbnail, banner, and any link buttons all live
    in ONE Container, replacing the old discord.Embed + link-button View
    combo. This is what actually gets sent/edited to the target channel;
    the builder's own ephemeral configuration UI (_panel_render_kwargs)
    is a separate admin-only tool and still uses a plain embed preview.
    If no link buttons are configured, no ActionRow is added at all — it
    stays a plain text/image container, never an empty button row."""
    title       = draft.get("title")
    description = draft.get("description") or ""
    thumbnail   = draft.get("thumbnail")
    banner      = draft.get("image")
    color       = draft.get("color") or COLOR_PRIMARY
    links       = draft.get("links") or []

    text_parts = ([heading_md(title)] if title else []) + ([description] if description else [])
    if not text_parts:
        text_parts = ["*Nothing set yet.*"]

    content_item = (
        discord.ui.Section(*text_parts, accessory=discord.ui.Thumbnail(thumbnail))
        if thumbnail else discord.ui.TextDisplay("\n\n".join(text_parts))
    )

    items = [content_item]
    if banner:
        items.append(discord.ui.Separator())
        items.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner)))
    if links:
        items.append(discord.ui.Separator())
        items.extend(embed_links.build_link_action_rows(links))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*items, accent_color=discord.Color(color)))
    return view

async def handle_component_button_click(interaction: discord.Interaction):
    """Routes a response-type button click back to its stored response
    text. custom_id shape: vx_msgcomp:{component_id}:{button_index}."""
    custom_id = (interaction.data or {}).get("custom_id", "")
    try:
        _, component_id, idx_str = custom_id.split(":", 2)
        idx = int(idx_str)
    except (ValueError, AttributeError):
        return
    gc  = guild_cfg(cfg, interaction.guild.id)
    comp = gc.get("message_components", {}).get(component_id)
    if not comp or idx >= len(comp.get("buttons", [])):
        return await interaction.response.send_message(embed=error_embed("This button's data couldn't be found — it may have been removed or the message rebuilt."), ephemeral=True)
    btn = comp["buttons"][idx]
    if btn.get("kind") != "response":
        return

    text_parts = ([heading_md(btn['response_title'])] if btn.get("response_title") else []) + \
                 ([btn["response_description"]] if btn.get("response_description") else [])
    if not text_parts:
        text_parts = ["*(nothing set)*"]
    thumbnail = btn.get("response_thumbnail")
    banner    = btn.get("response_banner")

    content_item = (
        discord.ui.Section(*text_parts, accessory=discord.ui.Thumbnail(thumbnail))
        if thumbnail else discord.ui.TextDisplay("\n\n".join(text_parts))
    )
    items = [content_item]
    if banner:
        items.append(discord.ui.Separator())
        items.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner)))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        *items,
        accent_color=discord.Color(comp.get("color") or COLOR_PRIMARY),
    ))
    await interaction.response.send_message(view=view, ephemeral=True)

class MessageComponentLayout(discord.ui.LayoutView):
    """Components V2 rendering of a LIVE /component message — title,
    description, thumbnail, banner, and its link/response buttons all in
    ONE Container. Persistent (timeout=None) and re-registered via
    bot.add_view() in on_ready for every entry in each guild's
    `message_components` store, exactly like TicketPanelLayout, since
    response-type buttons need a live interaction handler to survive a
    bot restart (link-type buttons don't — Discord opens those directly,
    no bot involvement at all)."""
    def __init__(self, component_id: str, comp: dict = None):
        super().__init__(timeout=None)
        comp = comp or {}
        title       = comp.get("title")
        description = comp.get("description") or ""
        thumbnail   = comp.get("thumbnail")
        banner      = comp.get("image")
        color       = comp.get("color") or COLOR_PRIMARY
        buttons     = comp.get("buttons") or []

        text_parts = ([heading_md(title)] if title else []) + ([description] if description else [])
        if not text_parts:
            text_parts = ["*Nothing set yet.*"]
        content_item = (
            discord.ui.Section(*text_parts, accessory=discord.ui.Thumbnail(thumbnail))
            if thumbnail else discord.ui.TextDisplay("\n\n".join(text_parts))
        )

        items = [content_item]
        if banner:
            items.append(discord.ui.Separator())
            items.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner)))
        if buttons:
            items.append(discord.ui.Separator())
            items.extend(message_components.build_action_rows(buttons, component_id, handle_component_button_click))

        self.add_item(discord.ui.Container(*items, accent_color=discord.Color(color)))

def _draft_summary(ctx, draft: dict) -> str:
    ch = ctx.guild.get_channel(draft.get("channel_id") or 0) if draft.get("channel_id") else None
    links = draft.get("links") or []
    return (
        f"**Title:** {draft.get('title') or '*(not set)*'}\n"
        f"**Description:** {'✅ set' if draft.get('description') else '*(not set)*'}\n"
        f"**Thumbnail:** {'✅ set' if draft.get('thumbnail') else '*(not set)*'}\n"
        f"**Banner:** {'✅ set' if draft.get('image') else '*(not set)*'}\n"
        f"**Color:** `#{(draft.get('color') or COLOR_PRIMARY):06X}`\n"
        f"**Link buttons:** {len(links)} configured\n"
        f"**Channel:** {ch.mention if ch else '*(not set)*'}"
        + ("\n**Mode:** editing an existing message — `embed send` will update it, not post new." if draft.get("target_message_id") else "")
    )

_MESSAGE_LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")

async def _resolve_message_ref(guild: discord.Guild, default_channel: Optional[discord.abc.Messageable], ref: str):
    """Resolve a user-given message reference — either a full message
    link (discord.com/channels/guild/channel/message) or a bare message
    ID (searched in `default_channel`) — into a discord.Message.
    Returns (message, error_string). error_string is None on success."""
    ref = (ref or "").strip()
    if not ref:
        return None, "Give a message link or ID."
    m = _MESSAGE_LINK_RE.search(ref)
    if m:
        guild_id, channel_id, message_id = (int(x) for x in m.groups())
        if guild_id != guild.id:
            return None, "That message link is from a different server."
        channel = guild.get_channel(int(channel_id))
        if not channel:
            return None, "Couldn't find that channel — I might not have access to it."
    else:
        if not ref.isdigit():
            return None, "That doesn't look like a message link or ID."
        if not default_channel:
            return None, "Run this in the channel the message is in, or paste the full message link instead."
        channel, message_id = default_channel, int(ref)
    try:
        msg = await channel.fetch_message(message_id)
    except discord.NotFound:
        return None, "Couldn't find that message — check the link/ID and try again."
    except discord.Forbidden:
        return None, "I don't have permission to read messages in that channel."
    return msg, None

def _parse_layout_message(msg: discord.Message) -> Optional[dict]:
    """Best-effort reader for a Components V2 (Container-based) message —
    used by /embed edit once a message was sent as a Container instead of
    a discord.Embed. Component attribute names for READING back a message
    are less firmly documented than for building one, so this stays
    defensive (getattr chains, broad except) and returns None rather than
    guessing wrong if the shape doesn't match what this bot itself sends."""
    try:
        container = next((c for c in (msg.components or []) if type(c).__name__ == "Container"), None)
        if not container:
            return None
        title, desc_lines, thumbnail, banner = None, [], None, None
        for child in getattr(container, "children", []) or []:
            kind = type(child).__name__
            if kind == "Section":
                texts = getattr(child, "components", None) or getattr(child, "children", None) or []
                for t in texts:
                    content = getattr(t, "content", "") or ""
                    if content.startswith("# ") and title is None:
                        title = content[2:].strip()
                    elif content:
                        desc_lines.append(content)
                accessory = getattr(child, "accessory", None)
                media = getattr(accessory, "media", None)
                if media is not None:
                    thumbnail = getattr(media, "url", None) or str(media)
            elif kind == "TextDisplay":
                content = getattr(child, "content", "") or ""
                if content.startswith("# ") and title is None:
                    title = content[2:].strip()
                elif content:
                    desc_lines.append(content)
            elif kind == "MediaGallery":
                items = getattr(child, "items", None) or []
                if items:
                    media = getattr(items[0], "media", None)
                    banner = getattr(media, "url", None) or (str(media) if media else None)
        color = getattr(container, "accent_colour", None) or getattr(container, "accent_color", None)
        return {
            "title": title, "description": "\n\n".join(desc_lines),
            "thumbnail": thumbnail, "image": banner,
            "color": color.value if color is not None else COLOR_PRIMARY,
        }
    except Exception:
        return None

def _load_message_into_draft(uid: int, msg: discord.Message) -> Optional[str]:
    """Replace the user's current embed draft with the contents of an
    already-sent message, so /embed edit can continue where that message
    left off. Returns an error string (draft left untouched) or None on
    success. Only the bot's OWN messages can be edited later (Discord
    restriction — you can never edit another user's/bot's message).
    Handles both the old discord.Embed messages (pre-Components-V2) and
    the new Container-based ones this bot now sends."""
    if msg.author.id != bot.user.id:
        return "I can only edit embeds that this bot sent."

    if msg.embeds:
        em = msg.embeds[0]
        base = {
            "title": em.title, "description": em.description or "",
            "thumbnail": em.thumbnail.url if em.thumbnail else None,
            "image": em.image.url if em.image else None,
            "color": em.color.value if em.color is not None else COLOR_PRIMARY,
        }
    else:
        base = _parse_layout_message(msg)
        if base is None:
            return "Couldn't read this message's content automatically — it may not be something this bot built. Try rebuilding it as a new embed instead."

    _EMBED_DRAFTS[uid] = {
        **base,
        "channel_id": msg.channel.id,
        "links": embed_links.parse_links_from_message(msg),
        "target_message_id": msg.id, "target_channel_id": msg.channel.id,
        "_history": [],
    }
    return None


async def pfx_embed(ctx, sub: str = "", *, rest: str = ""):
    """Build a custom embed piece-by-piece (title, description, thumbnail,
    banner, a divider/separator line, color) and send it to any channel.
    Draft is per-user and stays in memory until you `send` or `reset` it."""
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
        return await ctx.send(embed=error_embed("Only members with **Manage Server** or the owner can use the embed builder."))
    sub   = sub.lower()
    draft = _get_embed_draft(ctx.author.id)

    if sub == "title":
        text = rest.strip()
        if not text:
            return await ctx.send(embed=error_embed("Usage: `embed title <text>`"))
        draft["title"] = text[:256]
        await ctx.send(embed=success_embed("Title set. Run `embed preview` to check it out."))

    elif sub in ("description", "desc", "body"):
        text = rest.strip()
        if not text:
            return await ctx.send(embed=error_embed("Usage: `embed description <text>`"))
        draft["description"] = text[:4096]
        await ctx.send(embed=success_embed("Description set (replaces anything before it)."))

    elif sub == "append":
        text = rest.strip()
        if not text:
            return await ctx.send(embed=error_embed("Usage: `embed append <text>` — adds a new line onto the existing description."))
        draft["description"] = (draft.get("description", "") + "\n" + text).strip()[:4096]
        await ctx.send(embed=success_embed("Added to the description."))

    elif sub == "separator":
        style = rest.strip().lower() or "line"
        if style not in SEPARATOR_STYLES:
            return await ctx.send(embed=error_embed(f"Unknown style. Choices: {', '.join(f'`{s}`' for s in SEPARATOR_STYLES)}"))
        draft["description"] = (draft.get("description", "") + f"\n{SEPARATOR_STYLES[style]}\n").strip("\n")[:4096]
        await ctx.send(embed=success_embed(f"Added a `{style}` separator to the description."))

    elif sub == "thumbnail":
        url = ctx.message.attachments[0].url if ctx.message.attachments else rest.strip()
        if not url or not url.startswith("http"):
            return await ctx.send(embed=error_embed("Attach an image to your message, or give a direct URL: `embed thumbnail <url>`"))
        draft["thumbnail"] = url
        await ctx.send(embed=success_embed("Thumbnail set."))

    elif sub in ("banner", "image"):
        url = ctx.message.attachments[0].url if ctx.message.attachments else rest.strip()
        if not url or not url.startswith("http"):
            return await ctx.send(embed=error_embed("Attach an image to your message, or give a direct URL: `embed banner <url>`"))
        draft["image"] = url
        await ctx.send(embed=success_embed("Banner set."))

    elif sub == "color":
        hex_txt = rest.strip().lstrip("#")
        try:
            draft["color"] = int(hex_txt, 16)
        except ValueError:
            return await ctx.send(embed=error_embed("Give a valid hex color, e.g. `embed color FF0000`"))
        await ctx.send(embed=success_embed(f"Color set to `#{hex_txt.upper()}`."))

    elif sub == "channel":
        ch = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else None
        if not ch:
            return await ctx.send(embed=error_embed("Mention a channel: `embed channel #channel`"))
        draft["channel_id"] = ch.id
        # Clear any leftover edit-mode targeting from a previous `embed edit`
        # — otherwise `embed send` would silently update that old message.
        draft["target_message_id"] = None
        draft["target_channel_id"] = None
        await ctx.send(embed=success_embed(f"Target channel set to {ch.mention}."))

    elif sub == "edit":
        ref = rest.strip()
        if not ref:
            return await ctx.send(embed=error_embed("Usage: `embed edit <message link or ID>` (run it in the same channel if using just an ID)."))
        msg, err = await _resolve_message_ref(ctx.guild, ctx.channel, ref)
        if err:
            return await ctx.send(embed=error_embed(err))
        err = _load_message_into_draft(ctx.author.id, msg)
        if err:
            return await ctx.send(embed=error_embed(err))
        await ctx.send(embed=success_embed(f"Loaded that message into your draft — `embed send` now **updates it in place** instead of posting new."))

    elif sub == "link":
        parts = rest.split(" ", 1)
        action = (parts[0] if parts else "").lower()
        arg    = parts[1] if len(parts) > 1 else ""
        links  = draft.setdefault("links", [])
        if action == "add":
            bits  = [b.strip() for b in arg.split("|")]
            if len(bits) < 2:
                return await ctx.send(embed=error_embed("Usage: `embed link add <label> | <url> [| emoji]`"))
            label, url = bits[0], bits[1]
            emoji = bits[2] if len(bits) > 2 else ""
            err = embed_links.add_link(links, label, url, emoji)
            if err:
                return await ctx.send(embed=error_embed(err))
            await ctx.send(embed=success_embed(f"Added link button **{label}** ({len(links)}/{embed_links.MAX_LINKS})."))
        elif action == "remove":
            if not arg.strip().isdigit() or not (1 <= int(arg.strip()) <= len(links)):
                return await ctx.send(embed=error_embed(f"Usage: `embed link remove <number>` — see `embed link list` for numbers."))
            removed = links.pop(int(arg.strip()) - 1)
            await ctx.send(embed=success_embed(f"Removed link button **{removed['label']}**."))
        elif action == "list":
            if not links:
                return await ctx.send(embed=info_embed("Link Buttons", "None configured yet — `embed link add <label> | <url>`"))
            await ctx.send(embed=info_embed("Link Buttons", "\n".join(f"**{i+1}.** {l['label']} — {l['url']}" for i, l in enumerate(links))))
        else:
            await ctx.send(embed=error_embed("Usage: `embed link add <label> | <url> [| emoji]` / `embed link remove <number>` / `embed link list`"))

    elif sub == "preview":
        if not draft.get("title") and not draft.get("description"):
            return await ctx.send(embed=error_embed("Nothing to preview yet — set a title or description first."))
        await ctx.send("**Preview** *(not sent yet)*:")
        await ctx.send(view=build_embed_layout(draft))

    elif sub == "reset":
        _EMBED_DRAFTS.pop(ctx.author.id, None)
        await ctx.send(embed=success_embed("Embed draft cleared."))

    elif sub == "send":
        if not draft.get("title") and not draft.get("description"):
            return await ctx.send(embed=error_embed("Nothing to send yet — set a title or description first."))
        layout = build_embed_layout(draft)

        target_channel_id = draft.get("target_channel_id")
        target_message_id = draft.get("target_message_id")
        if target_channel_id and target_message_id:
            ch = ctx.guild.get_channel(target_channel_id)
            if not ch:
                return await ctx.send(embed=error_embed("Can't find the original channel anymore — the message may have been deleted."))
            try:
                msg = await ch.fetch_message(target_message_id)
                await msg.edit(view=layout)
            except discord.NotFound:
                return await ctx.send(embed=error_embed("That message doesn't exist anymore — run `embed reset` and send it as new instead."))
            except discord.Forbidden:
                return await ctx.send(embed=error_embed("I don't have permission to edit messages in that channel."))
            _EMBED_DRAFTS.pop(ctx.author.id, None)
            return await ctx.send(embed=success_embed(f"Updated the existing embed in {ch.mention}. Draft cleared."))

        ch = ctx.guild.get_channel(draft.get("channel_id") or 0)
        if not ch:
            return await ctx.send(embed=error_embed("No target channel set yet — run `embed channel #channel` first."))
        try:
            await ch.send(view=layout)
        except discord.Forbidden:
            return await ctx.send(embed=error_embed("I don't have permission to send messages in that channel."))
        _EMBED_DRAFTS.pop(ctx.author.id, None)
        await ctx.send(embed=success_embed(f"Embed sent to {ch.mention}. Draft cleared."))

    else:
        await ctx.send(embed=info_embed("Embed Builder", (
            "`embed title <text>` — set the title\n"
            "`embed description <text>` — set/replace the body text\n"
            "`embed append <text>` — add another line onto the body\n"
            f"`embed separator [{'/'.join(SEPARATOR_STYLES)}]` — insert a divider line\n"
            "`embed thumbnail <url>` *(or attach an image)* — small image, top-right\n"
            "`embed banner <url>` *(or attach an image)* — big image at the bottom\n"
            "`embed color <hex>` — e.g. `embed color FF0000`\n"
            "`embed channel #channel` — where it gets sent (for a NEW embed)\n"
            "`embed edit <message link or ID>` — load an already-sent embed to edit it instead of making a new one\n"
            "`embed link add <label> | <url> [| emoji]` — add a link button (works with or without editing)\n"
            "`embed link remove <number>` / `embed link list` — manage link buttons\n"
            "`embed preview` — see it before sending\n"
            "`embed send` — post it new, or **update** the original message if you used `embed edit`\n"
            "`embed reset` — clear the draft and start over\n\n"
            f"**Current draft:**\n{_draft_summary(ctx, draft)}"
        )))

# ── OWNER COMMANDS ────────────────────────────────────────────────

@bot.command(name="maintenance", aliases=["mnt"])
@is_owner()
async def pfx_maintenance(ctx, action: str = "", *, reason: str = ""):
    action = action.lower()
    m      = cfg.setdefault("maintenance", {"enabled": False, "reason": "", "since": None})
    if action == "on":
        m["enabled"] = True
        m["reason"]  = reason.strip()
        m["since"]   = discord.utils.utcnow().isoformat()
        save_config(cfg)
        try:
            await bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.playing, name="Under Maintenance ⚠️"),
                status=discord.Status.dnd
            )
        except Exception:
            pass
        desc = f"**{BOT_NAME}** is now in **maintenance mode**.\nAll commands are locked for everyone except the owner."
        if m["reason"]:
            desc += f"\n\n**Reason:** {m['reason']}"
        await ctx.send(embed=warning_embed("Maintenance Mode: ON", desc))
    elif action == "off":
        m["enabled"] = False
        m["reason"]  = ""
        m["since"]   = None
        save_config(cfg)
        await ctx.send(embed=success_embed(f"**{BOT_NAME}** is back to normal. All commands are unlocked."))
    elif action == "status":
        if m.get("enabled"):
            since = m.get("since")
            since_txt = discord.utils.format_dt(datetime.datetime.fromisoformat(since), "R") if since else "?"
            desc = f"Status: **ENABLED** — since {since_txt}"
            if m.get("reason"):
                desc += f"\n**Reason:** {m['reason']}"
        else:
            desc = "Status: **Disabled.** The bot is running normally."
        await ctx.send(embed=info_embed("Maintenance Status", desc))
    else:
        await ctx.send(embed=info_embed("Maintenance",
            "`maintenance on [reason]` — lock all commands except for the owner\n"
            "`maintenance off` — unlock again\n"
            "`maintenance status` — check the current status"))

@bot.command(name="errorlog", aliases=["errlog"])
@is_owner()
async def pfx_errorlog(ctx, sub: str = "", channel: discord.TextChannel = None):
    """Owner-only. Configures the channel where unexpected bot errors get
    auto-reported (command bugs, event-listener bugs, etc.) — so real
    issues surface immediately instead of only living in Railway logs."""
    sub = sub.lower()
    if sub == "channel":
        ch = channel or ctx.channel
        cfg["error_log_channel_id"] = ch.id
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Error reports will now be posted to {ch.mention}."))
    elif sub == "off":
        cfg["error_log_channel_id"] = None
        save_config(cfg)
        await ctx.send(embed=success_embed("Error reporting disabled."))
    elif sub == "test":
        try:
            raise RuntimeError("This is a test error triggered by `errorlog test` — not a real bug.")
        except RuntimeError as e:
            await report_error(e, location="errorlog test", user=ctx.author, guild=ctx.guild, channel=ctx.channel)
        await ctx.send(embed=success_embed("Test error sent — check the configured channel."))
    else:
        ch_id = cfg.get("error_log_channel_id")
        current = f"<#{ch_id}>" if ch_id else "*(not set)*"
        await ctx.send(embed=info_embed("Error Log", (
            f"**Current channel:** {current}\n\n"
            "`errorlog channel [#channel]` — set where errors get reported (defaults to this channel)\n"
            "`errorlog off` — disable error reporting\n"
            "`errorlog test` — send a fake error to confirm it's working"
        )))

# ── BOT STATUS UPDATES — posts a NOTHING-style status card to a channel ──
BOT_STATUS_PRESETS = {
    "online":      {"icon": ICON_STATUS_ONLINE,      "fallback": "🟢", "color": COLOR_SUCCESS,
                     "default_text": "is now online and ready to serve!"},
    "offline":     {"icon": ICON_STATUS_OFFLINE,      "fallback": "🔴", "color": COLOR_ERROR,
                     "default_text": "is going offline for a bit — back soon!"},
    "maintenance": {"icon": ICON_STATUS_MAINTENANCE, "fallback": "🟠", "color": COLOR_WARNING,
                     "default_text": "is entering maintenance mode. Some features may be unavailable."},
    "update":      {"icon": ICON_STATUS_UPDATE,       "fallback": "🔵", "color": COLOR_PRIMARY,
                     "default_text": "is being updated. Expect a short restart shortly."},
    "degraded":    {"icon": ICON_STATUS_DEGRADED,     "fallback": "🟡", "color": COLOR_WARNING,
                     "default_text": "is experiencing partial issues. We're looking into it."},
}

@bot.command(name="botstatus", aliases=["status", "bstatus"])
@is_owner()
async def pfx_botstatus(ctx, sub: str = "", *, rest: str = ""):
    """Owner-only. Posts a NOTHING-style status card (title, colored
    indicator, thumbnail, Bot ID + timestamp footer) to a dedicated status
    channel — for announcing online/offline/maintenance/update/degraded,
    or a fully custom one-off message."""
    sub = sub.lower()

    if sub in ("channel", "setchannel"):
        ch = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else None
        if not ch:
            return await ctx.send(embed=error_embed("Mention a channel: `botstatus channel #channel`"))
        cfg["status_channel_id"] = ch.id
        save_config(cfg)
        return await ctx.send(embed=success_embed(f"Status updates will now be posted in {ch.mention}."))

    # short aliases for each status type, so you don't have to type the
    # full word every time — e.g. `botstatus on` == `botstatus online`
    STATUS_TYPE_ALIASES = {
        "on": "online", "up": "online",
        "off": "offline", "down": "offline",
        "maint": "maintenance", "mnt": "maintenance",
        "upd": "update",
        "deg": "degraded", "issue": "degraded", "issues": "degraded", "partial": "degraded",
    }
    sub = STATUS_TYPE_ALIASES.get(sub, sub)

    if sub not in BOT_STATUS_PRESETS and sub != "custom":
        preset_list = ", ".join(f"`{k}`" for k in BOT_STATUS_PRESETS)
        return await ctx.send(embed=info_embed("Bot Status", (
            "`botstatus channel #channel` — set where status cards get posted\n"
            f"`botstatus <type> [custom message]` — post a status update. Types: {preset_list}\n"
            "`botstatus custom <emoji> <message>` — fully custom one-off status\n\n"
            "-# Short aliases work too: `on`/`up`, `off`/`down`, `maint`/`mnt`, `upd`, `deg`/`issue`/`partial`.\n"
            "-# Each type uses its own indicator emoji (set in emoji_config.py) and its own default "
            "message, which you can override by adding your own text after the type."
        )))

    ch_id = cfg.get("status_channel_id")
    ch    = bot.get_channel(ch_id) if ch_id else None
    if not ch:
        return await ctx.send(embed=error_embed("No status channel set yet — run `botstatus channel #channel` first."))

    if sub == "custom":
        parts = rest.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            return await ctx.send(embed=error_embed("Usage: `botstatus custom <emoji> <message>`"))
        indicator, text = parts[0], parts[1].strip()
        color = COLOR_PRIMARY
    else:
        preset    = BOT_STATUS_PRESETS[sub]
        indicator = e(preset["icon"], preset["fallback"])
        text      = rest.strip() or preset["default_text"]
        color     = preset["color"]

    embed = discord.Embed(
        title=f"{BOT_NAME} Status Update",
        description=f"{indicator} : **{bot.user.mention}** {text}",
        color=color,
        timestamp=discord.utils.utcnow()
    )
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text=f"Bot ID: {bot.user.id}")

    try:
        await ch.send(embed=embed)
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("I don't have permission to send messages in that channel."))
    await ctx.send(embed=success_embed(f"Status update posted in {ch.mention}."))

@bot.command(name="synccommands", aliases=["sync", "syncslash"])
@is_owner()
async def pfx_synccommands(ctx, scope: str = "guild"):
    """Owner-only. Force-resync slash commands. Global syncs (what runs
    automatically on startup) can take up to ~1 hour to actually show up
    in Discord's client — a guild-specific sync applies instantly, which
    is the fast way to confirm a newly-added slash command actually
    registered instead of waiting on Discord's global cache."""
    scope = scope.lower()
    try:
        if scope in ("global", "g"):
            synced = await bot.tree.sync()
            await ctx.send(embed=success_embed(
                f"Globally synced **{len(synced)}** slash command(s). "
                "Note: global syncs can take up to ~1 hour to show up for everyone."
            ))
        else:
            bot.tree.copy_global_to(guild=ctx.guild)
            synced = await bot.tree.sync(guild=ctx.guild)
            await ctx.send(embed=success_embed(
                f"Synced **{len(synced)}** slash command(s) to **{ctx.guild.name}** — should show up immediately.\n"
                "-# Run `synccommands global` instead to push the same update everywhere (slower to appear)."
            ))
    except Exception as e:
        await ctx.send(embed=error_embed(f"Sync failed: {e}"))

@bot.command(name="premiumlock", aliases=["plock"])
@is_owner()
async def pfx_premiumlock(ctx, action: str = "", *, cmd_name: str = ""):
    action = action.lower()
    locked = cfg.setdefault("premium_commands", [])
    cmd_name = cmd_name.strip().lower()
    if action == "add":
        if not cmd_name:
            return await ctx.send(embed=error_embed("Specify a command name. Example: `premiumlock add addemoji`"))
        if cmd_name in OWNER_ONLY_CMDS:
            return await ctx.send(embed=error_embed("Owner-only commands can't be Premium-locked."))
        if cmd_name not in locked:
            locked.append(cmd_name)
            save_config(cfg)
        await sync_premium_descriptions()
        await ctx.send(embed=success_embed(f"Command `{cmd_name}` is now **Premium only** (prefix & slash)."))
    elif action == "remove":
        if cmd_name in locked:
            locked.remove(cmd_name)
            save_config(cfg)
            await sync_premium_descriptions()
            await ctx.send(embed=success_embed(f"Command `{cmd_name}` is open to everyone again."))
        else:
            await ctx.send(embed=error_embed(f"Command `{cmd_name}` isn't on the premium lock list."))
    elif action == "list":
        if not locked:
            return await ctx.send(embed=info_embed("Premium Locked Commands", "No commands are Premium-locked yet."))
        await ctx.send(embed=info_embed("Premium Locked Commands", "\n".join(f"`{c}`" for c in locked)))
    else:
        await ctx.send(embed=info_embed("Premium Lock",
            "`premiumlock add <command>` — lock a command to Premium only\n"
            "`premiumlock remove <command>` — unlock a command\n"
            "`premiumlock list` — view every locked command\n\n"
            "Use the command's slash name, e.g. for a subcommand: `ticket setup`."))

@bot.command(name="noprefix", aliases=["np"])
@is_owner_or_staff()
async def pfx_noprefix(ctx, action: str = "", *, rest: str = ""):
    action     = action.lower()
    np_users   = cfg.setdefault("no_prefix_users",  [])
    np_guilds  = cfg.setdefault("no_prefix_guilds", [])
    np_expiry  = cfg.setdefault("no_prefix_expiry", {})

    if action == "list":
        u_lines = []
        for uid in np_users:
            exp_str = np_expiry.get(str(uid))
            if exp_str:
                try:
                    exp = datetime.datetime.fromisoformat(exp_str)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=datetime.timezone.utc)
                    exp_txt = discord.utils.format_dt(exp, "R")
                except Exception:
                    exp_txt = "?"
            else:
                exp_txt = "Permanent"
            u_lines.append(f"<@{uid}> (`{uid}`) — {exp_txt}")
        u_lines = u_lines or ["*(none)*"]
        g_lines = []
        for gid in np_guilds:
            g = bot.get_guild(gid)
            g_lines.append(f"**{g.name}** (`{gid}`)" if g else f"`{gid}`")
        g_lines = g_lines or ["*(none)*"]
        embed = base_embed("No-Prefix Access List", None)
        embed.add_field(name="Users",  value="\n".join(u_lines), inline=False)
        embed.add_field(name="Guilds", value="\n".join(g_lines), inline=False)
        return await ctx.send(embed=embed)

    if action not in ("grant", "revoke"):
        return await ctx.send(embed=info_embed("No-Prefix", (
            "`noprefix grant @user/guild_id [duration]`\n"
            "`noprefix revoke @user/guild_id`\n"
            "`noprefix list`\n\n"
            "Duration only applies to users (not guilds). Example: `7d`, `24h`, `30m`, "
            "or leave it blank/`permanent` for forever."
        )))

    parts = rest.split(maxsplit=1)
    if not parts:
        return await ctx.send(embed=error_embed("Provide an @user or guild ID."))
    target_tok = parts[0]
    duration   = parts[1].strip().lower() if len(parts) > 1 else ""
    uid_match  = re.match(r"<@!?(\d+)>|(\d{17,20})", target_tok.strip())
    if not uid_match:
        return await ctx.send(embed=error_embed("Invalid target."))
    parsed_id = int(uid_match.group(1) or uid_match.group(2))

    g = bot.get_guild(parsed_id)
    if g:
        if action == "grant":
            if parsed_id not in np_guilds: np_guilds.append(parsed_id)
            save_config(cfg)
            await ctx.send(embed=success_embed(f"No-prefix enabled for server **{g.name}**."))
        else:
            if parsed_id in np_guilds: np_guilds.remove(parsed_id)
            save_config(cfg)
            await ctx.send(embed=success_embed(f"No-prefix revoked from server **{g.name}**."))
        return

    try:
        user = await bot.fetch_user(parsed_id)
    except Exception:
        return await ctx.send(embed=error_embed("User/Guild not found."))

    if action == "grant":
        expiry_dt = None
        if duration and duration != "permanent":
            m = re.fullmatch(r"(\d+)(d|h|m)", duration)
            if not m:
                return await ctx.send(embed=error_embed("Duration format: `7d`, `24h`, `30m`, or `permanent`."))
            amount = int(m.group(1)); unit = m.group(2)
            delta  = {"d": datetime.timedelta(days=amount), "h": datetime.timedelta(hours=amount), "m": datetime.timedelta(minutes=amount)}[unit]
            expiry_dt = datetime.datetime.now(datetime.timezone.utc) + delta
        if parsed_id not in np_users:
            np_users.append(parsed_id)
        if expiry_dt:
            np_expiry[str(parsed_id)] = expiry_dt.isoformat()
        else:
            np_expiry.pop(str(parsed_id), None)
        save_config(cfg)
        dur_display = "Permanent" if not expiry_dt else discord.utils.format_dt(expiry_dt, "R")
        try:
            dm = base_embed(
                "No-Prefix Access Granted!",
                f"You can now use {BOT_NAME} commands without a prefix!\nJust type the command name directly.\nExpires: {dur_display}",
                color=COLOR_SUCCESS
            )
            await user.send(embed=dm)
        except Exception:
            pass
        await ctx.send(embed=success_embed(f"No-prefix enabled for {user.mention}.\nExpires: {dur_display}"))
    else:
        if parsed_id in np_users: np_users.remove(parsed_id)
        np_expiry.pop(str(parsed_id), None)
        save_config(cfg)
        await ctx.send(embed=success_embed(f"No-prefix revoked from {user.mention}."))

@bot.command(name="botrole", aliases=["br"])
@is_owner()
async def pfx_botrole(ctx, action: str = "", *args):
    action    = action.lower()
    bot_roles = cfg.setdefault("bot_roles", {})
    role_sync = cfg.setdefault("role_sync", {})
    mk_users  = cfg.setdefault("moonkeeper_users", [])
    valid_tiers = ("staff", "moderator", "server_manager", "management", "developer")
    settable    = valid_tiers + ("moonkeeper",)  # moonkeeper accepted here, stored separately below

    if action == "list":
        lines = []
        for uid_str, r in bot_roles.items():
            user = bot.get_user(int(uid_str))
            name = user.display_name if user else f"ID {uid_str}"
            lines.append(f"**{name}** → {r.capitalize()}")
        for uid in mk_users:
            user = bot.get_user(uid)
            name = user.display_name if user else f"ID {uid}"
            lines.append(f"**{name}** → Moonkeeper")
        if not lines:
            return await ctx.send(embed=info_embed("Bot Roles (Manual)", "No manual assignments yet."))
        return await ctx.send(embed=info_embed("Bot Roles (Manual)", "\n".join(lines)))

    if action == "sync":
        sub = args[0].lower() if args else ""
        if sub == "list":
            guild = get_support_guild()
            lines = []
            for tier in valid_tiers:
                role_id = role_sync.get(tier)
                if not role_id:
                    lines.append(f"**{tier.capitalize()}** → *(not set)*")
                    continue
                role = guild.get_role(role_id) if guild else None
                lines.append(f"**{tier.capitalize()}** → {role.mention if role else f'`{role_id}` (role not found)'}")
            mk_role_id = cfg.get("moonkeeper_sync_role")
            if mk_role_id:
                mk_role = guild.get_role(mk_role_id) if guild else None
                lines.append(f"**Moonkeeper** → {mk_role.mention if mk_role else f'`{mk_role_id}` (role not found)'}")
            else:
                lines.append("**Moonkeeper** → *(not set)*")
            note = "" if guild else "\n\n⚠️ `SUPPORT_SERVER_ID` isn't set in the environment, so sync won't work."
            return await ctx.send(embed=info_embed("Bot Role Sync", "\n".join(lines) + note))
        if sub == "remove":
            tier = args[1].lower() if len(args) > 1 else ""
            if tier == "moonkeeper":
                cfg.pop("moonkeeper_sync_role", None)
                save_config(cfg)
                return await ctx.send(embed=success_embed("Sync role for **Moonkeeper** removed."))
            if tier not in valid_tiers:
                return await ctx.send(embed=error_embed("Valid tiers: `staff`, `moderator`, `server_manager`, `management`, `developer`, `moonkeeper`."))
            role_sync.pop(tier, None)
            save_config(cfg)
            return await ctx.send(embed=success_embed(f"Sync role for **{tier.capitalize()}** removed."))
        # botrole sync <tier> <role_id/mention>
        if len(args) < 2:
            return await ctx.send(embed=info_embed("Bot Role Sync", (
                "`botrole sync <staff/moderator/server_manager/management/developer/moonkeeper> <role_id or @role>` — link a Discord role in the support server to a badge\n"
                "`botrole sync remove <tier>` — unlink it\n"
                "`botrole sync list` — view the current mapping\n\n"
                "Once set, anyone with that role in the support server automatically gets the badge "
                "on `profile` — no need for manual `botrole set` anymore.\n\n"
                "-# Moonkeeper is independent of the other tiers — it stacks alongside whatever "
                "tier someone already has instead of competing with it."
            )))
        tier = args[0].lower()
        if tier not in settable:
            return await ctx.send(embed=error_embed("Valid tiers: `staff`, `moderator`, `server_manager`, `management`, `developer`, `moonkeeper`."))
        role_match = re.match(r"<@&(\d+)>|(\d{17,20})", args[1].strip())
        if not role_match:
            return await ctx.send(embed=error_embed("Provide a valid role ID or role mention."))
        role_id = int(role_match.group(1) or role_match.group(2))
        guild = get_support_guild()
        if not guild:
            return await ctx.send(embed=error_embed("`SUPPORT_SERVER_ID` isn't set in the bot's environment."))
        disc_role = guild.get_role(role_id)
        if not disc_role:
            return await ctx.send(embed=error_embed("Role not found in the support server."))
        if tier == "moonkeeper":
            cfg["moonkeeper_sync_role"] = role_id
        else:
            role_sync[tier] = role_id
        save_config(cfg)
        info = BOT_ROLE_BADGES[tier]
        badge_tag = (info["emoji"] + " ") if info.get("emoji") else ""
        return await ctx.send(embed=success_embed(
            f"Role {disc_role.mention} now automatically grants the {badge_tag}**{info['label']}** badge.\n"
            f"Any support-server member with this role gets their badge updated instantly."
        ))

    if not args:
        return await ctx.send(embed=info_embed("Bot Role", (
            "`botrole set @user <staff/moderator/server_manager/management/developer/moonkeeper>` — manual assignment (for people outside the support server)\n"
            "`botrole remove @user` — remove a manual assignment\n"
            "`botrole list` — view manual assignments\n"
            "`botrole sync <tier> <role_id>` — auto-sync from a Discord role in the support server"
        )))

    member = None
    for tok in args:
        m = re.match(r"<@!?(\d+)>|(\d{17,20})", tok.strip())
        if m:
            uid = int(m.group(1) or m.group(2))
            member = ctx.guild.get_member(uid)
            break
    if not member:
        return await ctx.send(embed=error_embed("User not found in this server."))
    role = next((a.lower() for a in args if a.lower() in settable), "")

    if action == "set":
        if role not in settable:
            return await ctx.send(embed=error_embed("Valid roles: `staff`, `moderator`, `server_manager`, `management`, `developer`, `moonkeeper`"))
        if role == "moonkeeper":
            if member.id not in mk_users:
                mk_users.append(member.id)
                save_config(cfg)
        else:
            bot_roles[str(member.id)] = role
            save_config(cfg)
        info = BOT_ROLE_BADGES[role]
        badge_tag = (info["emoji"] + " ") if info.get("emoji") else ""
        embed = discord.Embed(title="Bot Role Assigned", description=f"{member.mention} → {badge_tag}**{info['label']}**", color=info["color"], timestamp=discord.utils.utcnow())
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            dm = discord.Embed(title="Bot Role Granted!", description=f"You've been given the {badge_tag}**{info['label']}** role on {BOT_NAME}!\nCheck your profile: `profile`", color=info["color"])
            await member.send(embed=dm)
        except Exception: pass
        await ctx.send(embed=embed)
    elif action == "remove":
        removed = []
        if str(member.id) in bot_roles:
            removed.append(bot_roles.pop(str(member.id)).capitalize())
        if member.id in mk_users:
            mk_users.remove(member.id)
            removed.append("Moonkeeper")
        if not removed:
            return await ctx.send(embed=error_embed(f"{member.display_name} doesn't have a manual bot role."))
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Manual bot role(s) **{', '.join(removed)}** removed from {member.mention}."))

@bot.command(name="custombadge", aliases=["cbadge", "cb", "custombadges", "badge", "badges"])
@is_owner()
async def pfx_custombadge(ctx, action: str = "", *args):
    """
    Owner-only. Create fully custom, free-form badges — any name, any emoji
    (including this server's custom emoji) — and give/remove them on any
    specific user, any time. Completely separate from the built-in bot-role
    badges (Founder/Staff/etc.): no hierarchy, no auto-sync, just a manual
    grant that only the owner controls.
    """
    action = action.lower()
    defs   = cfg.setdefault("custom_badges", {})
    grants = cfg.setdefault("user_custom_badges", {})

    if action == "create":
        if len(args) < 2:
            return await ctx.send(embed=error_embed(
                "Usage: `custombadge create <emoji> <name>`\nExample: `custombadge create 🐉 Dragon Tamer`"
            ))
        emoji_tok = args[0]
        name      = _sanitize_badge_name(" ".join(args[1:]))
        if not name:
            return await ctx.send(embed=error_embed("Badge name can't be empty (mentions and channel tags don't count as a name)."))
        if len(name) > 100:
            return await ctx.send(embed=error_embed("Badge name is too long (max 100 characters)."))
        badge_id = _slugify_badge_id(name)
        defs[badge_id] = {"name": name, "emoji": emoji_tok}
        save_config(cfg)
        return await ctx.send(embed=success_embed(
            f"Custom badge created: {emoji_tok} **{name}**\n"
            f"ID: `{badge_id}` — use this ID to give or remove it.\n\n"
            f"`custombadge give @user {badge_id}`"
        ))

    if action == "delete":
        if not args:
            return await ctx.send(embed=error_embed("Usage: `custombadge delete <badge_id>`"))
        badge_id = args[0].lower()
        if badge_id not in defs:
            return await ctx.send(embed=error_embed(f"No custom badge with ID `{badge_id}`. Use `custombadge list` to see all IDs."))
        removed = defs.pop(badge_id)
        holders = 0
        for ids in grants.values():
            if badge_id in ids:
                ids.remove(badge_id)
                holders += 1
        save_config(cfg)
        return await ctx.send(embed=success_embed(
            f"Deleted custom badge {removed.get('emoji','')} **{removed.get('name', badge_id)}** (`{badge_id}`) — "
            f"removed from {holders} member(s) who had it."
        ))

    if action == "list":
        if not defs:
            return await ctx.send(embed=info_embed("Custom Badges", "No custom badges created yet.\nUse `custombadge create <emoji> <name>` to make one."))
        lines = []
        for bid, info in defs.items():
            holder_count = sum(1 for ids in grants.values() if bid in ids)
            lines.append(f"{info.get('emoji','')} **{info.get('name', bid)}** — `{bid}` · {holder_count} holder(s)")
        return await ctx.send(embed=info_embed("Custom Badges", "\n".join(lines)))

    if action in ("give", "grant"):
        if len(args) < 2:
            return await ctx.send(embed=error_embed("Usage: `custombadge give @user <badge_id>`"))
        target_id = _resolve_badge_target(args[0])
        badge_id  = args[1].lower()
        if not target_id:
            return await ctx.send(embed=error_embed("Provide a valid user mention or ID."))
        if badge_id not in defs:
            return await ctx.send(embed=error_embed(f"No custom badge with ID `{badge_id}`. Use `custombadge list` to see all IDs."))
        held = grants.setdefault(str(target_id), [])
        if badge_id in held:
            return await ctx.send(embed=error_embed(f"<@{target_id}> already has that badge."))
        held.append(badge_id)
        save_config(cfg)
        info = defs[badge_id]
        try:
            target_user = await bot.fetch_user(target_id)
            dm = base_embed(
                "Custom Badge Granted!",
                f"You've been given the {info.get('emoji','')} **{info['name']}** badge on {BOT_NAME}!\nCheck your profile: `profile`",
                color=COLOR_SUCCESS
            )
            await target_user.send(embed=dm)
        except Exception:
            pass
        return await ctx.send(embed=success_embed(f"Gave {info.get('emoji','')} **{info['name']}** to <@{target_id}>."))

    if action in ("remove", "revoke", "take"):
        if len(args) < 2:
            return await ctx.send(embed=error_embed("Usage: `custombadge remove @user <badge_id>`"))
        target_id = _resolve_badge_target(args[0])
        badge_id  = args[1].lower()
        if not target_id:
            return await ctx.send(embed=error_embed("Provide a valid user mention or ID."))
        held = grants.get(str(target_id), [])
        if badge_id not in held:
            return await ctx.send(embed=error_embed(f"<@{target_id}> doesn't have that badge."))
        held.remove(badge_id)
        save_config(cfg)
        info = defs.get(badge_id, {})
        return await ctx.send(embed=success_embed(f"Removed {info.get('emoji','')} **{info.get('name', badge_id)}** from <@{target_id}>."))

    if action == "user":
        if not args:
            return await ctx.send(embed=error_embed("Usage: `custombadge user @user`"))
        target_id = _resolve_badge_target(args[0])
        if not target_id:
            return await ctx.send(embed=error_embed("Provide a valid user mention or ID."))
        held = get_custom_badges(target_id)
        if not held:
            return await ctx.send(embed=info_embed("Custom Badges", f"<@{target_id}> has no custom badges."))
        lines = [f"{b.get('emoji','')} **{b['name']}** — `{b['id']}`" for b in held]
        return await ctx.send(embed=info_embed(f"Custom Badges — <@{target_id}>", "\n".join(lines)))

    return await ctx.send(embed=info_embed("Custom Badges", (
        "`custombadge create <emoji> <name>` — create a new custom badge\n"
        "`custombadge give @user <badge_id>` — give a badge to a member\n"
        "`custombadge remove @user <badge_id>` — revoke a badge from a member\n"
        "`custombadge delete <badge_id>` — permanently delete a badge (removes it from everyone who has it)\n"
        "`custombadge list` — view every custom badge, its ID, and how many people hold it\n"
        "`custombadge user @user` — view a member's custom badges\n\n"
        "-# Fully separate from the built-in bot-role badges (Founder, Staff, etc.) — name and "
        "emoji are 100% yours to decide, including this server's custom emoji. Shows up right "
        "alongside the other badges on `profile`."
    )))

@bot.command(name="checkcustom", aliases=["debugcustom"])
@is_owner_or_staff()
async def pfx_checkcustom(ctx, member: discord.Member = None):
    """Owner/staff-only. Dumps the RAW stored values (not the rendered
    result) for a member's rank/level-up + ID card customization, plus
    their live premium status — so a report of 'my background/color
    reset' can be settled by looking at exactly what's on disk right now
    instead of guessing from the rendered image."""
    target = member or ctx.author
    uid = str(target.id)
    is_prem = target.id in cfg.get("premium_users", [])
    exp_str = cfg.get("premium_expiry", {}).get(uid)

    lines = [f"**is_prem:** `{is_prem}`"]
    if exp_str:
        lines.append(f"**premium_expiry:** `{exp_str}`")
    else:
        lines.append("**premium_expiry:** *(none stored — permanent)*")

    for label, store_key in [
        ("rankbg (premium_backgrounds)", "premium_backgrounds"),
        ("rankcolor (premium_colors)",   "premium_colors"),
        ("idcardbg (profile_backgrounds)", "profile_backgrounds"),
        ("idcardcolor (profile_colors)", "profile_colors"),
    ]:
        val = cfg.get(store_key, {}).get(uid)
        lines.append(f"**{label}:** `{val!r}`")

    await ctx.send(embed=info_embed(f"Raw Customization Data — {target.display_name}", "\n".join(lines)))

@bot.command(name="grantpremium", aliases=["gp"])
@is_owner_or_staff()
async def pfx_grantpremium(ctx, member: discord.Member = None, duration: str = ""):
    if not member:
        return await ctx.send(embed=info_embed("Grant Premium", "`grantpremium @user <7d/30d/permanent>` · `grantpremium @user revoke`"))
    premium_users  = cfg.setdefault("premium_users",  [])
    premium_expiry = cfg.setdefault("premium_expiry", {})
    if duration.lower() == "revoke":
        if member.id in premium_users: premium_users.remove(member.id)
        premium_expiry.pop(str(member.id), None)
        save_config(cfg)
        try:
            await member.send(embed=base_embed("Premium Ended", f"Your {BOT_NAME} Premium has ended. Premium command access and no-prefix have both been revoked.", color=COLOR_ERROR))
        except Exception: pass
        return await ctx.send(embed=success_embed(f"Premium revoked from {member.mention}."))
    expiry_dt = None
    if duration.lower() != "permanent":
        m = re.fullmatch(r"(\d+)(d|h|m)", duration.lower())
        if not m: return await ctx.send(embed=error_embed("Format: `7d`, `30d`, `24h`, `permanent`"))
        amount = int(m.group(1)); unit = m.group(2)
        delta  = {"d": datetime.timedelta(days=amount), "h": datetime.timedelta(hours=amount), "m": datetime.timedelta(minutes=amount)}[unit]
        expiry_dt = datetime.datetime.now(datetime.timezone.utc) + delta
    if member.id not in premium_users: premium_users.append(member.id)
    if expiry_dt: premium_expiry[str(member.id)] = expiry_dt.isoformat()
    else: premium_expiry.pop(str(member.id), None)
    save_config(cfg)
    dur_display = "Permanent" if not expiry_dt else discord.utils.format_dt(expiry_dt, "R")
    embed = discord.Embed(title="Premium Granted!", description=f"{member.mention} is now **Premium**!\nExpires: {dur_display}", color=COLOR_WARNING, timestamp=discord.utils.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    try:
        dm = discord.Embed(
            title="Premium Activated!",
            description=f"Your {BOT_NAME} Premium is active!\nExpires: {dur_display}\n\nEvery premium command is unlocked and **no-prefix is automatically enabled** — just type the command name without `!vx`.",
            color=COLOR_WARNING
        )
        await member.send(embed=dm)
    except Exception: pass
    await ctx.send(embed=embed)

@bot.command(name="blacklist", aliases=["bl"])
@is_owner()
async def pfx_blacklist(ctx, action: str = "", guild_id: str = ""):
    bl = cfg.setdefault("blacklisted_guilds", [])
    if action == "add":
        try: gid = int(guild_id)
        except ValueError: return await ctx.send(embed=error_embed("Guild ID must be a number."))
        if gid not in bl: bl.append(gid)
        save_config(cfg)
        g = bot.get_guild(gid)
        if g:
            try: await g.leave()
            except Exception: pass
        await ctx.send(embed=success_embed(f"Guild `{gid}` has been blacklisted and the bot has left."))
    elif action == "remove":
        try: gid = int(guild_id)
        except ValueError: return await ctx.send(embed=error_embed("Guild ID must be a number."))
        if gid in bl: bl.remove(gid)
        save_config(cfg)
        await ctx.send(embed=success_embed(f"Guild `{gid}` has been removed from the blacklist."))
    elif action == "list":
        if not bl: return await ctx.send(embed=info_embed("Blacklist", "No guilds are blacklisted."))
        lines = []
        for gid in bl:
            g = bot.get_guild(gid)
            lines.append(f"**{g.name}** (`{gid}`)" if g else f"`{gid}`")
        await ctx.send(embed=info_embed("Blacklisted Guilds", "\n".join(lines)))
    else:
        await ctx.send(embed=info_embed("Blacklist", "`blacklist add <guild_id>`\n`blacklist remove <guild_id>`\n`blacklist list`"))

class ServerListView(discord.ui.View):
    """List every server the bot is in, complete with key info
    (member count, owner, when the bot joined) — so the owner knows exactly
    which server they're about to leave before running vxleave."""
    PER_PAGE = 8

    def __init__(self, guilds: list, owner_id: int):
        super().__init__(timeout=120)
        self.guilds   = guilds
        self.owner_id = owner_id
        self.page     = 0

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.guilds) - 1) // self.PER_PAGE + 1)

    def build_embed(self) -> discord.Embed:
        start = self.page * self.PER_PAGE
        chunk = self.guilds[start:start + self.PER_PAGE]
        embed = base_embed(f"Server List — {len(self.guilds)} total", None)
        for g in chunk:
            joined_at = g.me.joined_at if g.me else None
            joined_txt = discord.utils.format_dt(joined_at, "R") if joined_at else "?"
            owner_txt  = f"{g.owner} (`{g.owner_id}`)" if g.owner else f"`{g.owner_id}`"
            embed.add_field(
                name=g.name,
                value=(
                    f"ID: `{g.id}`\n"
                    f"Members: **{g.member_count:,}** · Owner: {owner_txt}\n"
                    f"Bot joined: {joined_txt}"
                ),
                inline=False
            )
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages} • `vxleave <guild_id>` to leave a server")
        return embed

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error_embed("Only the owner can navigate this."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if not await self._guard(interaction): return
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if not await self._guard(interaction): return
        self.page = min(self.total_pages - 1, self.page + 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

@bot.command(name="vxservers", aliases=["vxguilds"])
@is_owner()
async def pfx_vxservers(ctx, sort: str = "members"):
    guilds = list(bot.guilds)
    if sort.lower() in ("name", "alpha"):
        guilds.sort(key=lambda g: g.name.lower())
    else:
        guilds.sort(key=lambda g: g.member_count or 0, reverse=True)
    if not guilds:
        return await ctx.send(embed=info_embed("Server List", "The bot hasn't joined any servers yet."))
    view = ServerListView(guilds, bot.owner_id)
    await ctx.send(embed=view.build_embed(), view=view)

@bot.command(name="vxleave", aliases=["leave"])
@is_owner()
async def pfx_vxleave(ctx, guild_id: str = ""):
    if not guild_id:
        guilds = sorted(bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        if not guilds:
            return await ctx.send(embed=info_embed("Leave Guild", "The bot hasn't joined any servers yet."))
        view = ServerListView(guilds, bot.owner_id)
        embed = view.build_embed()
        embed.description = "Pick a server from this list, then run `vxleave <guild_id>`."
        return await ctx.send(embed=embed, view=view)
    try:
        gid = int(guild_id)
    except ValueError:
        return await ctx.send(embed=error_embed("Guild ID must be a number. Check `vxservers` for the list and IDs."))
    g = bot.get_guild(gid)
    if not g:
        return await ctx.send(embed=error_embed("The bot isn't in that guild. Check `vxservers` for the server list."))
    owner_txt = f"{g.owner} (`{g.owner_id}`)" if g.owner else f"`{g.owner_id}`"
    embed = base_embed(f"Leaving {g.name}...", None, color=COLOR_ERROR)
    embed.add_field(name="Guild ID", value=f"`{g.id}`", inline=True)
    embed.add_field(name="Members", value=f"{g.member_count:,}", inline=True)
    embed.add_field(name="Owner", value=owner_txt, inline=True)
    await ctx.send(embed=embed)
    await g.leave()

# ── HELP ─────────────────────────────────────────────────────────

# ── HELP MENU — category dropdown so it doesn't pile up in one embed ──

HELP_CATEGORIES = [
    ("moderation", "Moderation", ICON_MODERATION, "🛠️", (
        "`kick` · `ban` · `unban` · `timeout` · `untimeout`\n"
        "`warn` · `warnings` · `unwarn` · `clearwarnings`\n"
        "`purge` · `lock` · `unlock` · `slowmode` · `hide` · `unhide`"
    )),
    ("role_voice", "Role & Voice", ICON_ROLE, "🎭", "`addrole` · `removerole` · `move`"),
    ("info", "Info", ICON_INFO, "ℹ️", "`userinfo` · `serverinfo` · `avatar` · `ping` · `addemoji` · `profile` · `idcardbg` (premium) · `idcardcolor` (premium)"),
    ("embed", "Embed Builder", ICON_EMBED, "🖼️", (
        "`embed title/description/append/separator` — write the content\n"
        "`embed thumbnail/banner/color` — style it (URL or attach an image)\n"
        "`embed channel #channel` · `embed edit <link/ID>` · `embed link add/remove/list` · `embed preview` · `embed send` · `embed reset`\n"
        "Admin/owner only. Draft is per-user, kept until you send or reset it."
    )),
    ("component", "Message Component Builder", ICON_COMPONENT, "🔘", (
        "`component <id> #channel` · `/component` — same builder as Embed, plus buttons\n"
        "Buttons can be **links** (open a URL) or **responses** (show the clicker an ephemeral message)\n"
        "Reuse the same `<id>` later to edit that same message instead of posting a new one\n"
        "Admin/owner only (Manage Server)."
    )),
    ("afk", "AFK System", ICON_AFK, "💤", (
        "`afk [reason]` (alias `away`) · `/afk` — set yourself as AFK\n"
        "Sending any message automatically clears your AFK status.\n"
        "Anyone who @mentions you while you're AFK gets notified with your reason.\n"
        f"-# Only **{AFK_FREE_SLOTS}** members can be AFK at once per server — once full, voting for the bot unlocks it temporarily."
    )),
    ("ticket", "Ticket", ICON_TICKET, "🎫", (
        "`ticket setup` · `ticket panel` · `ticket edit` · `ticket welcome` · `ticket list` · `ticket delete` · `ticket close`\n"
        "Each ticket has Claim + Close buttons.\n"
        "`/ticketpanel` — full visual builder (title, description, welcome message, thumbnail, "
        "banner, color, button label/emoji/color, button-or-dropdown), same style as `/embed`."
    )),
    ("level", "Level & XP", ICON_LEVEL, "📈", "`rank` · `rankbg` (premium) · `rankcolor` (premium) · `leaderboard` (alias `lb`) · `level toggle/setchannel/status` · `xp`"),
    ("giveaway", "Giveaway", ICON_GIVEAWAY, "🎉", "`giveaway start/end/reroll/list`\n`--role <id>` · `--winrole <id>`"),
    ("antispam", "Antispam", ICON_ANTISPAM, "🛡️", "`antispam setchannel` · `logchannel` · `punishment` · `threshold` · `flood` · `ignore` · `status`"),
    ("antinuke", "Anti-Nuke", ICON_ANTINUKE, "🛡️", "`antinuke enable/disable` · `antinuke logchannel` · `antinuke punishment` · `antinuke whitelist` · `antinuke status`"),
    ("verification", "Verification", ICON_VERIFICATION, "🔐", "`verification channel/unverifiedrole/verifiedrole/logchannel` · `verification enable/disable` · `verification send` · `verification status`"),
    ("automod", "AutoMod", ICON_AUTOMOD, "🤖", "`automod setup` — creates a native Discord AutoMod rule (blocks profanity/sexual content/slurs)\n`automod list` · `automod remove <rule_id>`"),
    ("ignore", "Ignore Channel", ICON_IGNORE, "🔇", "`ignorechannel add/remove/list [#channel]` — makes the bot completely silent in a specific channel"),
    ("autoresponse", "Auto-Response", ICON_AUTORESPONSE, "💬", "`autoresponse add <trigger> | <response>` · `remove` · `match` · `list` · `toggle`"),
    ("boost", "Server Boost", ICON_BOOST, "🎉", "`/boostconfig` (slash only) — configure the server boost notification channel & appearance"),
]

OWNER_HELP_CATEGORY = ("owner", "Owner Only", ICON_OWNER, "👑", (
    "`maintenance on/off/status`\n"
    "`noprefix grant/revoke/list`\n"
    "`botrole set/remove/list`\n"
    "`custombadge create/give/remove/delete/list` — free-form badges you design and assign\n"
    "`botstatus channel/online/offline/maintenance/update/degraded/custom` — status update cards\n"
    "`grantpremium @user <duration>/revoke`\n"
    "`premiumlock add/remove/list`\n"
    "`blacklist add/remove/list`\n"
    "`vxservers` — view every server the bot is in\n"
    "`vxleave <guild_id>`\n"
    "`errorlog channel/off/test` — where unexpected bot errors get auto-reported"
))

DELEGATED_HELP_CATEGORY = ("delegated_access", "Access Management", "", "🌙", (
    "You're a **Moonkeeper** — you can grant/revoke access on " + BOT_NAME + "'s behalf, "
    "same as if the owner ran it themselves:\n\n"
    "`noprefix grant @user [duration]` / `noprefix revoke @user` / `noprefix list`\n"
    "`grantpremium @user <duration>` / `grantpremium @user revoke`\n\n"
    "-# The owner can remove this access at any time by changing your bot role."
))

class HelpView(discord.ui.View):
    """Help navigation dropdown — every person who runs `help` gets their own
    View instance, so the 'Owner Only' option automatically only shows up in
    the owner's dropdown, with no need for DMs or special ephemeral handling."""
    def __init__(self, invoker_id: int, is_owner_user: bool, has_np: bool):
        super().__init__(timeout=120)
        self.invoker_id = invoker_id
        self.has_np     = has_np
        self.message: Optional[discord.Message] = None
        self.categories = list(HELP_CATEGORIES) + (
            [OWNER_HELP_CATEGORY] if is_owner_user else
            ([DELEGATED_HELP_CATEGORY] if can_manage_access(invoker_id) else [])
        )

        options = [discord.SelectOption(label="Overview", value="_home", emoji="🏠", description="Home page")]
        for key, label, icon_var, fallback, _ in self.categories:
            options.append(discord.SelectOption(label=label, value=key, emoji=e(icon_var, fallback)))
        select = discord.ui.Select(placeholder="Pick a command category...", options=options)
        select.callback = self.on_select
        self.add_item(select)

        for item in invite_support_view().children:
            self.add_item(item)

    def home_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"{BOT_NAME} — Command Reference",
            description=(
                f"*{BOT_TAGLINE}*\n\n"
                f"Prefix: **`!vx`** · **`!v`** (alias)\n"
                + ("✨ **No-prefix active** — just type the command directly!\n" if self.has_np else "")
                + "\nPick a category from the dropdown below to see its commands."
            ),
            color=COLOR_PRIMARY,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"{BOT_NAME} v{BOT_VERSION} • {BOT_TAGLINE}")
        return brand_embed(embed)

    def category_embed(self, key: str) -> discord.Embed:
        _, label, icon_var, fallback, value = next(c for c in self.categories if c[0] == key)
        embed = discord.Embed(title=f"{e(icon_var, fallback)} {label}".strip(), description=value, color=COLOR_PRIMARY, timestamp=discord.utils.utcnow())
        embed.set_footer(text=f"{BOT_NAME} v{BOT_VERSION} • {BOT_TAGLINE}")
        return brand_embed(embed)

    async def on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            return await interaction.response.send_message(embed=error_embed("This menu isn't for you — run `help` yourself."), ephemeral=True)
        value = interaction.data["values"][0]
        embed = self.home_embed() if value == "_home" else self.category_embed(value)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

@bot.command(name="help", aliases=["h"])
async def pfx_help(ctx):
    has_np = user_has_no_prefix(ctx.guild, ctx.author)
    view   = HelpView(ctx.author.id, ctx.author.id == bot.owner_id, has_np)
    view.message = await ctx.send(embed=view.home_embed(), view=view)

@bot.command(name="ownerhelp", aliases=["oh"])
@is_owner()
async def pfx_ownerhelp(ctx):
    """Owner-only reference command — always sent via DM so it can't be read by anyone else in the channel."""
    embed = discord.Embed(
        title=f"{e(ICON_OWNER, '👑')} {BOT_NAME} — Owner Command Reference".strip(),
        description="This list is only ever sent to your DMs — it's never shown in a public channel.",
        color=COLOR_PRIMARY,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Maintenance", value="`maintenance on [reason]` · `maintenance off` · `maintenance status`", inline=False)
    embed.add_field(name="No-Prefix", value="`noprefix grant @user [duration]` · `noprefix revoke @user` · `noprefix list`\nDuration: `7d` / `24h` / `30m` / leave blank for permanent.", inline=False)
    embed.add_field(name="Bot Role", value="`botrole set @user <role>` · `botrole remove @user` · `botrole list`\n`botrole sync <tier> <role_id>` — auto-badge from a Discord role in the support server", inline=False)
    embed.add_field(name="Custom Badges", value="`custombadge create <emoji> <name>` · `give/remove @user <badge_id>` · `list` · `delete <badge_id>` · `user @user`\nFree-form badges — any name, any emoji — fully separate from bot-role badges.", inline=False)
    embed.add_field(name="Premium", value="`grantpremium @user <duration>` · `grantpremium @user revoke`", inline=False)
    embed.add_field(name="Premium Lock", value="`premiumlock add <command>` · `premiumlock remove <command>` · `premiumlock list`", inline=False)
    embed.add_field(name="Blacklist", value="`blacklist add <id>` · `blacklist remove <id>` · `blacklist list`", inline=False)
    embed.add_field(name="Other", value="`vxservers` — view every server the bot is in\n`vxleave <guild_id>`", inline=False)
    embed.set_footer(text=BOT_NAME + " v" + BOT_VERSION + " • Owner Only")

    try:
        await ctx.author.send(embed=embed)
        ack = success_embed("The command reference has been sent to your DMs.")
    except discord.Forbidden:
        ack = error_embed("Couldn't DM you — open your DMs for this server/bot first, then try again.")
    await ctx.send(embed=ack, delete_after=8)

@bot.command(name="commandlist", aliases=["cmdlist", "allcommands", "cmds"])
@is_owner()
async def pfx_commandlist(ctx):
    """Owner-only to RUN, but the output is meant to be posted publicly
    (support server, etc.) — so owner/Moonkeeper-restricted commands are
    deliberately excluded from the list itself. Detected two ways so
    nothing sensitive slips through: dynamically, via any command carrying
    an `is_owner()` check, and via the static OWNER_ONLY_CMDS set (which
    also covers `is_owner_or_staff()`-gated ones like `noprefix`/`grantpremium`
    that don't use is_owner() directly)."""
    def _is_owner_restricted(c: commands.Command) -> bool:
        if c.name in OWNER_ONLY_CMDS:
            return True
        for check in c.checks:
            if getattr(check, "__qualname__", "").startswith("is_owner.<locals>.predicate"):
                return True
        return False

    cmds = sorted(
        (c for c in bot.commands if not _is_owner_restricted(c)),
        key=lambda c: c.name.lower()
    )
    lines = []
    for c in cmds:
        entry = f"`{BOT_PREFIX}{c.name}`"
        if c.aliases:
            entry += " — " + ", ".join(f"`{a}`" for a in c.aliases)
        lines.append(entry)

    embed = discord.Embed(
        title=f"{e(ICON_OWNER, '👑')} {BOT_NAME} — Full Command List".strip(),
        description="\n".join(lines),
        color=COLOR_PRIMARY,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"{BOT_NAME} v{BOT_VERSION} • {len(cmds)} commands")
    await ctx.send(embed=brand_embed(embed))

# ══════════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════

@bot.tree.command(name="rank", description="View your rank card or another member's.")
@app_commands.describe(member="The member whose rank you want to view")
@app_commands.checks.cooldown(1, 6, key=lambda i: i.user.id)
async def slash_rank(i: discord.Interaction, member: Optional[discord.Member] = None):
    await i.response.defer()
    target      = member or i.user
    gc          = guild_cfg(cfg, i.guild.id)
    data        = get_member_xp(gc, str(target.id))
    lvl, cx, nx = xp_progress(data["xp"], gc.get("xp_difficulty", 1.0))
    all_m       = sorted(gc["members_xp"].items(), key=lambda x: x[1].get("xp",0), reverse=True)
    rank        = next((idx+1 for idx,(uid,_) in enumerate(all_m) if uid == str(target.id)), 1)
    is_prem     = user_has_premium(i.guild, target)
    avatar_url  = str(target.display_avatar.with_format("png").with_size(256))

    import aiohttp
    file = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                avatar_bytes = await resp.read()
        bg_bytes = await fetch_rank_bg_bytes(is_prem, target.id)
        accent_colors = get_premium_accent(is_prem, target.id)
        buf = await asyncio.to_thread(
            rank_card.render_rank_card,
            avatar_bytes, target.name, lvl, rank, cx, nx,
            data["xp"], is_prem, data.get("messages", 0), bg_bytes, accent_colors
        )
        file = discord.File(buf, filename="rank.png")
    except Exception:
        logging.exception(f"[{BOT_NAME}] Failed to render rank card")

    if file:
        kwargs = {"file": file}
        try:
            content, view = _support_boost_promo(i.user.id)
            if content: kwargs["content"] = content
            if view:    kwargs["view"] = view
        except Exception:
            logging.exception(f"[{BOT_NAME}] Failed to build boost promo (rank card still sent)")
        return await i.followup.send(**kwargs)

    pct   = int((cx / max(nx,1)) * 100)
    bar   = "▰"*int(pct/100*16) + "▱"*(16-int(pct/100*16))
    embed = discord.Embed(description=f"**@{target.name}**\n\n**Level: {lvl}** | **XP: {cx:,}/{nx:,}** | **Rank: #{rank}**\n\n`{bar}` {pct}%\n\n*Total XP: {data['xp']:,}*", color=COLOR_PRIMARY)
    embed.set_author(name="Rank Card", icon_url=target.display_avatar.url)
    embed.set_thumbnail(url=target.display_avatar.url)
    await i.followup.send(embed=embed)

@bot.tree.command(name="rankbg", description="Set or remove a custom rank/level-up card background (Premium perk).")
@app_commands.describe(url="Direct image URL (.png/.jpg/.jpeg/.webp) — leave empty to remove your current background")
async def slash_rankbg(i: discord.Interaction, url: Optional[str] = None):
    if not user_has_premium(i.guild, i.user):
        msg = "Custom backgrounds (rank card + level-up card) are a **Premium** perk — ask the bot owner about getting Premium."
        if SUPPORT_INVITE:
            msg += f"\n[Join the support server]({SUPPORT_INVITE}) to ask about it."
        return await i.response.send_message(embed=error_embed(msg), ephemeral=True)
    backgrounds = cfg.setdefault("premium_backgrounds", {})
    uid = str(i.user.id)
    if not url:
        if uid in backgrounds:
            backgrounds.pop(uid, None)
            save_config(cfg)
            return await i.response.send_message(embed=success_embed("Custom background removed — your rank card and level-up card are back to the default look."), ephemeral=True)
        return await i.response.send_message(embed=info_embed(
            "Custom Background",
            "`/rankbg url:<image url>` — set a custom background for your `/rank` card and level-up card (must end in `.png`, `.jpg`, `.jpeg`, or `.webp`)\n"
            "`/rankbg` (no url) — remove your current custom background\n\n"
            "-# Looking for your `/profile` ID card instead? Use `/idcardbg` — it has its own separate background."
        ), ephemeral=True)
    if not _BG_URL_RE.match(url.strip()):
        return await i.response.send_message(embed=error_embed("That doesn't look like a valid direct image URL — it needs to start with `http(s)://` and end in `.png`, `.jpg`, `.jpeg`, or `.webp`."), ephemeral=True)
    await i.response.defer(ephemeral=True)
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url.strip(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return await i.followup.send(embed=error_embed(f"Couldn't fetch that URL (HTTP {resp.status}) — double-check it's a direct, public image link."), ephemeral=True)
                test_bytes = await resp.read()
    except Exception:
        return await i.followup.send(embed=error_embed("Couldn't fetch that URL — double-check it's a direct, public image link."), ephemeral=True)
    rendered = await asyncio.to_thread(rank_card.cover_image, test_bytes, (934, 300))
    if rendered is None:
        return await i.followup.send(embed=error_embed("That URL didn't decode as a valid image — try a different link."), ephemeral=True)
    backgrounds[uid] = url.strip()
    save_config(cfg)
    await i.followup.send(embed=success_embed("Custom background set! It'll show on your `/rank` card and level-up card."), ephemeral=True)

@bot.tree.command(name="rankcolor", description="Set or remove a custom 2 or 3-color gradient for your rank/level-up cards (Premium perk).")
@app_commands.describe(color1="First hex color, e.g. #a672ff", color2="Second hex color, e.g. #20dcd2", color3="Optional third hex color")
async def slash_rankcolor(i: discord.Interaction, color1: Optional[str] = None, color2: Optional[str] = None, color3: Optional[str] = None):
    if not user_has_premium(i.guild, i.user):
        msg = "A custom gradient color (rank card + level-up card) is a **Premium** perk — ask the bot owner about getting Premium."
        if SUPPORT_INVITE:
            msg += f"\n[Join the support server]({SUPPORT_INVITE}) to ask about it."
        return await i.response.send_message(embed=error_embed(msg), ephemeral=True)
    colors = cfg.setdefault("premium_colors", {})
    uid = str(i.user.id)
    if not color1:
        if uid in colors:
            colors.pop(uid, None)
            save_config(cfg)
            return await i.response.send_message(embed=success_embed("Custom gradient removed — back to the default gold premium look."), ephemeral=True)
        return await i.response.send_message(embed=info_embed(
            "Rank Card Gradient",
            "`/rankcolor color1:<hex> color2:<hex> color3:<hex>` — set a custom 2 or 3-color gradient for your `/rank` card & level-up card (color3 is optional)\n"
            "`/rankcolor` (no args) — remove your gradient, back to default gold\n\n"
            "-# Looking for your `/profile` ID card instead? Use `/idcardcolor` — it has its own separate gradient."
        ), ephemeral=True)
    if not color2:
        return await i.response.send_message(embed=error_embed("Give at least two hex colors, e.g. `color1: #a672ff color2: #20dcd2` (color3 is optional)."), ephemeral=True)
    stops = [color1, color2] + ([color3] if color3 else [])
    parsed = [parse_hex_color(c) for c in stops]
    if not all(parsed):
        return await i.response.send_message(embed=error_embed("That doesn't look like a valid hex color — use 6-digit hex codes like `#a672ff` or `a672ff`."), ephemeral=True)
    colors[uid] = [c.strip().lstrip("#") for c in stops]
    save_config(cfg)
    embed = success_embed("Custom gradient set! It'll show on your `/rank` card and level-up card.")
    embed.add_field(name="Preview", value=" → ".join(f"`#{c}`" for c in colors[uid]))
    embed.color = discord.Color.from_rgb(*parsed[0])
    await i.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="idcardbg", description="Set or remove a custom background for your profile ID card (Premium perk).")
@app_commands.describe(url="Direct image URL (.png/.jpg/.jpeg/.webp) — leave empty to remove your current background")
async def slash_idcardbg(i: discord.Interaction, url: Optional[str] = None):
    if not user_has_premium(i.guild, i.user):
        msg = "A custom ID card background is a **Premium** perk — ask the bot owner about getting Premium."
        if SUPPORT_INVITE:
            msg += f"\n[Join the support server]({SUPPORT_INVITE}) to ask about it."
        return await i.response.send_message(embed=error_embed(msg), ephemeral=True)
    backgrounds = cfg.setdefault("profile_backgrounds", {})
    uid = str(i.user.id)
    if not url:
        if uid in backgrounds:
            backgrounds.pop(uid, None)
            save_config(cfg)
            return await i.response.send_message(embed=success_embed("Custom ID card background removed — back to the default look."), ephemeral=True)
        return await i.response.send_message(embed=info_embed(
            "ID Card Background",
            "`/idcardbg url:<image url>` — set a custom background for your `/profile` ID card only (must end in `.png`, `.jpg`, `.jpeg`, or `.webp`)\n"
            "`/idcardbg` (no url) — remove your current custom background\n\n"
            "-# This is separate from `/rankbg` — the ID card is a different shape/size, so it gets its own background."
        ), ephemeral=True)
    if not _BG_URL_RE.match(url.strip()):
        return await i.response.send_message(embed=error_embed("That doesn't look like a valid direct image URL — it needs to start with `http(s)://` and end in `.png`, `.jpg`, `.jpeg`, or `.webp`."), ephemeral=True)
    await i.response.defer(ephemeral=True)
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url.strip(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return await i.followup.send(embed=error_embed(f"Couldn't fetch that URL (HTTP {resp.status}) — double-check it's a direct, public image link."), ephemeral=True)
                test_bytes = await resp.read()
    except Exception:
        return await i.followup.send(embed=error_embed("Couldn't fetch that URL — double-check it's a direct, public image link."), ephemeral=True)
    rendered = await asyncio.to_thread(rank_card.cover_image, test_bytes, (100, 100))
    if rendered is None:
        return await i.followup.send(embed=error_embed("That URL didn't decode as a valid image — try a different link."), ephemeral=True)
    backgrounds[uid] = url.strip()
    save_config(cfg)
    await i.followup.send(embed=success_embed("Custom ID card background set! Run `/profile` to see it."), ephemeral=True)

@bot.tree.command(name="idcardcolor", description="Set or remove a custom 2 or 3-color gradient for your profile ID card (Premium perk).")
@app_commands.describe(color1="First hex color, e.g. #a672ff", color2="Second hex color, e.g. #20dcd2", color3="Optional third hex color")
async def slash_idcardcolor(i: discord.Interaction, color1: Optional[str] = None, color2: Optional[str] = None, color3: Optional[str] = None):
    if not user_has_premium(i.guild, i.user):
        msg = "A custom ID card gradient is a **Premium** perk — ask the bot owner about getting Premium."
        if SUPPORT_INVITE:
            msg += f"\n[Join the support server]({SUPPORT_INVITE}) to ask about it."
        return await i.response.send_message(embed=error_embed(msg), ephemeral=True)
    colors = cfg.setdefault("profile_colors", {})
    uid = str(i.user.id)
    if not color1:
        if uid in colors:
            colors.pop(uid, None)
            save_config(cfg)
            return await i.response.send_message(embed=success_embed("Custom ID card gradient removed — back to the default gold premium look."), ephemeral=True)
        return await i.response.send_message(embed=info_embed(
            "ID Card Gradient",
            "`/idcardcolor color1:<hex> color2:<hex> color3:<hex>` — set a custom 2 or 3-color gradient for your `/profile` ID card only (color3 is optional)\n"
            "`/idcardcolor` (no args) — remove your gradient, back to default gold\n\n"
            "-# This is separate from `/rankcolor` — set them differently if you want the two cards to look distinct."
        ), ephemeral=True)
    if not color2:
        return await i.response.send_message(embed=error_embed("Give at least two hex colors, e.g. `color1: #a672ff color2: #20dcd2` (color3 is optional)."), ephemeral=True)
    stops = [color1, color2] + ([color3] if color3 else [])
    parsed = [parse_hex_color(c) for c in stops]
    if not all(parsed):
        return await i.response.send_message(embed=error_embed("That doesn't look like a valid hex color — use 6-digit hex codes like `#a672ff` or `a672ff`."), ephemeral=True)
    colors[uid] = [c.strip().lstrip("#") for c in stops]
    save_config(cfg)
    embed = success_embed("Custom ID card gradient set! Run `/profile` to see it.")
    embed.add_field(name="Preview", value=" → ".join(f"`#{c}`" for c in colors[uid]))
    embed.color = discord.Color.from_rgb(*parsed[0])
    await i.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="vote", description="Vote for the bot on top.gg and get a +10% XP Boost for 20 minutes.")
async def slash_vote(i: discord.Interaction):
    await i.response.send_message(**_vote_command_kwargs(i.user.id))

@bot.tree.command(name="leaderboard", description="View this server's top 10 XP leaderboard.")
async def slash_leaderboard(i: discord.Interaction):
    gc    = guild_cfg(cfg, i.guild.id)
    all_d = sorted(gc["members_xp"].items(), key=lambda x: x[1].get("xp",0), reverse=True)[:10]
    if not all_d:
        return await i.response.send_message(embed=info_embed("Leaderboard", "No XP data yet."), ephemeral=True)

    await i.response.defer()
    try:
        entries = await _build_leaderboard_entries(i.guild, all_d)
        buf  = await asyncio.to_thread(rank_card.render_leaderboard_card, i.guild.name, entries)
        file = discord.File(buf, filename="leaderboard.png")
        return await i.followup.send(file=file)
    except Exception as e:
        logging.error(f"[{BOT_NAME}] Failed to render leaderboard card: {e}")

    lines = []
    for idx,(uid,data) in enumerate(all_d):
        m     = i.guild.get_member(int(uid))
        name  = m.name if m else f"User ({uid[:6]})"
        medal = ["#1","#2","#3"][idx] if idx < 3 else f"#{idx+1}"
        lines.append(f"**{medal} {name}** — Level **{data.get('level',0)}** · {data.get('xp',0):,} XP")
    embed = discord.Embed(title="XP Leaderboard", description="\n".join(lines), color=COLOR_PRIMARY, timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"{BOT_NAME} · {i.guild.name}")
    await i.followup.send(embed=embed)

@bot.tree.command(name="profile", description="View your profile card and badges, or another member's.")
@app_commands.describe(member="The member whose profile you want to view")
@app_commands.checks.cooldown(1, 10, key=lambda i: i.user.id)
async def slash_profile(i: discord.Interaction, member: Optional[discord.Member] = None):
    target = member or i.user
    await i.response.defer()
    try:
        file = await build_profile_card_file(target)
        await i.followup.send(file=file)
    except Exception:
        logging.exception(f"[{BOT_NAME}] profile card render gagal")
        await i.followup.send(embed=error_embed("Couldn't generate that profile card right now — try again in a bit."))

@bot.tree.command(name="userinfo", description="View detailed info about a member.")
@app_commands.describe(member="The member you want info about")
async def slash_userinfo(i: discord.Interaction, member: Optional[discord.Member] = None):
    await do_userinfo(i.guild, member or i.user, i.response.send_message)

@bot.tree.command(name="avatar", description="View a member's avatar.")
@app_commands.describe(member="The member whose avatar you want to view")
async def slash_avatar(i: discord.Interaction, member: Optional[discord.Member] = None):
    await do_avatar(member or i.user, i.response.send_message)

@bot.tree.command(name="serverinfo", description="View this server's info.")
async def slash_serverinfo(i: discord.Interaction):
    g = i.guild
    embed = discord.Embed(title=g.name, description=g.description or "", color=COLOR_PRIMARY, timestamp=discord.utils.utcnow())
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner",      value=f"<@{g.owner_id}>",                    inline=True)
    embed.add_field(name="Members",    value=f"{g.member_count:,}",                  inline=True)
    embed.add_field(name="Created",    value=g.created_at.strftime("%d %b %Y"),       inline=True)
    embed.add_field(name="Channels",   value=str(len(g.text_channels)),               inline=True)
    embed.add_field(name="Roles",      value=str(len(g.roles)),                       inline=True)
    embed.add_field(name="Boost Tier", value=str(g.premium_tier),                     inline=True)
    embed.set_footer(text=f"{BOT_NAME} • ID: {g.id}")
    await i.response.send_message(embed=embed)

@bot.tree.command(name="boostconfig", description="Configure the server boost notification.")
@app_commands.describe(
    channel="The channel where boost notifications should be sent",
    title="Custom title (optional, default: 'New Server Boost!')",
    emoji="Custom emoji shown before the title (optional, default: 🎉)",
    description="Custom description (optional). Placeholders: {mention} {user} {server} {count} {tier}"
)
async def slash_boostconfig(
    i: discord.Interaction,
    channel: Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel],
    title: Optional[str] = None,
    emoji: Optional[str] = None,
    description: Optional[str] = None
):
    if not (i.user.id == bot.owner_id or i.user.guild_permissions.manage_guild):
        return await i.response.send_message(embed=error_embed("You don't have permission to use this command."), ephemeral=True)

    gc = guild_cfg(cfg, i.guild.id)
    bc = gc.setdefault("boost", {})
    bc["channel"] = channel.id
    if title       is not None: bc["title"]       = title
    if emoji       is not None: bc["emoji"]       = emoji
    if description is not None: bc["description"] = description
    save_config(cfg)

    def fill(template: str) -> str:
        return (template
                .replace("{mention}", i.user.mention)
                .replace("{user}",    i.user.display_name)
                .replace("{server}",  i.guild.name)
                .replace("{count}",   str(i.guild.premium_subscription_count or 0))
                .replace("{tier}",    str(i.guild.premium_tier)))

    preview = discord.Embed(
        title=f"{bc.get('emoji') or e(ICON_BOOST, '🎉')} {fill(bc.get('title', 'New Server Boost!'))}".strip(),
        description=fill(bc.get("description", "{mention} just boosted **{server}**! Thanks for the support 💜")),
        color=0xF47FFF,
        timestamp=discord.utils.utcnow()
    )
    preview.set_thumbnail(url=i.user.display_avatar.url)
    preview.set_footer(text=f"{i.guild.name} • Preview — this is what it'll look like")

    await i.response.send_message(
        embed=success_embed(f"Boost notifications will now be sent to {channel.mention} whenever a member boosts."),
        ephemeral=True
    )
    await i.followup.send(embed=preview, ephemeral=True)

@bot.tree.command(name="ping", description="Check the bot's latency.")
async def slash_ping(i: discord.Interaction):
    lat = round(bot.latency * 1000)
    await i.response.send_message(embed=base_embed("Pong!", f"Latency: **{lat}ms**", COLOR_SUCCESS if lat < 100 else COLOR_WARNING))

@bot.tree.command(name="afk", description="Set yourself as AFK — anyone who mentions you will be notified.")
@app_commands.describe(reason="Optional reason shown to people who mention you (default: 'AFK')")
async def slash_afk(i: discord.Interaction, reason: Optional[str] = None):
    await do_afk_set(i.guild, i.user, reason or "", i.response.send_message)

@bot.tree.command(name="help", description="View every VALLENT EXS command.")
async def slash_help(i: discord.Interaction):
    has_np = user_has_no_prefix(i.guild, i.user)
    view   = HelpView(i.user.id, i.user.id == bot.owner_id, has_np)
    await i.response.send_message(embed=view.home_embed(), view=view, ephemeral=True)
    view.message = await i.original_response()

def _panel_render_kwargs(draft: dict) -> dict:
    """Live-preview payload for the /embed panel message — shows the embed
    exactly as it currently stands, or a placeholder if nothing's set yet.
    Content line reflects edit-mode / configured link buttons so both
    stay visible across every re-render (title change, undo, etc)."""
    if draft.get("title") or draft.get("description"):
        embed = _build_draft_embed(draft)
    else:
        embed = discord.Embed(description="*Nothing set yet — use the buttons below to start building.*", color=COLOR_PRIMARY)
    notes = []
    if draft.get("target_message_id"):
        notes.append("editing an existing message — **Update** applies changes to it")
    links = draft.get("links") or []
    if links:
        notes.append(f"🔗 {len(links)} link button(s) configured")
    content = "**Embed Builder**" + ((" — " + " · ".join(notes)) if notes else "")
    return {"content": content, "embed": embed}

class EmbedFieldModal(discord.ui.Modal):
    """Generic single-field modal used by every text-entry button on the
    panel below. Editing the panel via interaction.response.edit_message()
    here works reliably because Discord ties a modal submission back to
    whichever message the button that opened it was attached to — unlike
    calling .edit() on a separately-stored message object, which isn't
    reliable for ephemeral messages across two different interactions."""
    def __init__(self, field: str, label: str,
                 current: str = "", style=discord.TextStyle.short, max_length: int = 256, placeholder: str = ""):
        super().__init__(title=label, timeout=300)
        self.field = field
        self.value_input = discord.ui.TextInput(
            label=label, style=style, required=False, default=current,
            max_length=max_length, placeholder=placeholder
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        draft = _get_embed_draft(interaction.user.id)
        val   = self.value_input.value.strip()

        if self.field == "color":
            hex_txt = val.lstrip("#")
            if hex_txt:
                try:
                    int(hex_txt, 16)
                except ValueError:
                    return await interaction.response.send_message(embed=error_embed("Invalid hex color — try something like `8B0000`."), ephemeral=True)
        elif self.field in ("thumbnail", "image") and val and not val.startswith("http"):
            return await interaction.response.send_message(embed=error_embed("Must be a direct image URL."), ephemeral=True)

        _snapshot_draft(draft)
        if self.field == "append":
            if val:
                draft["description"] = (draft.get("description", "") + "\n" + val).strip()[:4000]
        elif self.field == "color":
            draft["color"] = int(val.lstrip("#"), 16) if val else COLOR_PRIMARY
        elif self.field in ("thumbnail", "image"):
            draft[self.field] = val or None
        else:  # title / description
            draft[self.field] = val or None

        await interaction.response.edit_message(**_panel_render_kwargs(draft))

class SeparatorSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=s.capitalize(), value=s, description=SEPARATOR_STYLES[s][:40]) for s in SEPARATOR_STYLES]
        super().__init__(placeholder="Insert a separator line…", options=options, min_values=1, max_values=1, row=2)


    async def callback(self, interaction: discord.Interaction):
        draft = _get_embed_draft(interaction.user.id)
        style = self.values[0]
        _snapshot_draft(draft)
        draft["description"] = (draft.get("description", "") + f"\n{SEPARATOR_STYLES[style]}\n").strip("\n")[:4000]
        await interaction.response.edit_message(**_panel_render_kwargs(draft))

class EmbedLinkModal(discord.ui.Modal, title="Add Link Button"):
    def __init__(self):
        super().__init__(timeout=300)
        self.label_input = discord.ui.TextInput(label="Button label", max_length=80, placeholder="e.g. Visit Our Website")
        self.url_input   = discord.ui.TextInput(label="URL", max_length=512, placeholder="https://...")
        self.emoji_input = discord.ui.TextInput(label="Emoji (optional)", required=False, max_length=100, placeholder="e.g. 🔗 or a custom emoji")
        self.add_item(self.label_input)
        self.add_item(self.url_input)
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        draft = _get_embed_draft(interaction.user.id)
        links = draft.setdefault("links", [])
        resolved_emoji, warning = ticket_types.resolve_emoji_input(interaction.guild, self.emoji_input.value)
        err = embed_links.add_link(links, self.label_input.value, self.url_input.value, resolved_emoji or "")
        if err:
            return await interaction.response.send_message(embed=error_embed(err), ephemeral=True)
        render = _panel_render_kwargs(draft)
        if warning:
            render["content"] = f"⚠️ {warning}\n\n" + render["content"]
        await interaction.response.edit_message(**render)


class EmbedLinkRemoveSelect(discord.ui.Select):
    def __init__(self, links: list):
        options = [discord.SelectOption(label=f"{i+1}. {l['label']}"[:100], value=str(i)) for i, l in enumerate(links)]
        super().__init__(placeholder="Choose a link button to remove…", options=options[:25], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.selected_index = int(self.values[0])
        await interaction.response.edit_message(content=f"Selected **{self.values[0]}** — hit **Remove Selected** to confirm.", view=view)


class EmbedLinkManageView(discord.ui.View):
    def __init__(self, owner_id: int, links: list):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.selected_index = None
        self.add_item(EmbedLinkRemoveSelect(links))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error_embed("This isn't your embed builder — run `/embed` yourself."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Remove Selected", style=discord.ButtonStyle.danger, row=1)
    async def remove_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.selected_index is None:
            return await interaction.response.send_message(embed=error_embed("Pick a link from the dropdown first."), ephemeral=True)
        draft = _get_embed_draft(interaction.user.id)
        links = draft.get("links", [])
        if self.selected_index >= len(links):
            return await interaction.response.send_message(embed=error_embed("That link no longer exists — the list may have changed."), ephemeral=True)
        removed = links.pop(self.selected_index)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"✅ Removed link button **{removed['label']}**. Go back to the builder to see the updated preview.", view=self)


class EmbedBuilderPanel(discord.ui.View):
    """The full /embed builder — every field from the prefix `!vx embed`
    command, but as clickable buttons + modals instead of chat subcommands.
    Shares the same in-memory draft store, so switching between `/embed`
    and `!vx embed` mid-build works seamlessly. Also handles editing an
    already-sent message (is_edit=True relabels Send -> Update) and
    optional link buttons (skipped entirely — a plain embed with no view —
    if none are configured)."""
    def __init__(self, channel: Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel], owner_id: int, is_edit: bool = False):
        super().__init__(timeout=900)
        self.channel  = channel
        self.owner_id = owner_id
        self.is_edit  = is_edit
        self.add_item(SeparatorSelect())
        if is_edit:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.style == discord.ButtonStyle.success:
                    item.label = "Update"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error_embed("This isn't your embed builder — run `/embed` yourself."), ephemeral=True)
            return False
        return True

    async def _open_modal(self, interaction, field, label, current="", **kw):
        await interaction.response.send_modal(EmbedFieldModal(field, label, current=current, **kw))

    @discord.ui.button(label="Title", style=discord.ButtonStyle.secondary, row=0)
    async def title_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_embed_draft(interaction.user.id)
        await self._open_modal(interaction, "title", "Title", current=draft.get("title") or "", max_length=256)

    @discord.ui.button(label="Description", style=discord.ButtonStyle.secondary, row=0)
    async def desc_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_embed_draft(interaction.user.id)
        await self._open_modal(interaction, "description", "Description", current=draft.get("description") or "",
                                style=discord.TextStyle.paragraph, max_length=4000, placeholder="Replaces the whole body")

    @discord.ui.button(label="Add Line", style=discord.ButtonStyle.secondary, row=0)
    async def append_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await self._open_modal(interaction, "append", "Text to add", style=discord.TextStyle.paragraph,
                                max_length=1000, placeholder="Added as a new line onto the existing description")

    @discord.ui.button(label="Thumbnail", style=discord.ButtonStyle.secondary, row=0)
    async def thumb_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_embed_draft(interaction.user.id)
        await self._open_modal(interaction, "thumbnail", "Thumbnail URL", current=draft.get("thumbnail") or "", placeholder="Direct image link")

    @discord.ui.button(label="Banner", style=discord.ButtonStyle.secondary, row=0)
    async def banner_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_embed_draft(interaction.user.id)
        await self._open_modal(interaction, "image", "Banner / Image URL", current=draft.get("image") or "", placeholder="Direct image link")

    @discord.ui.button(label="Color", style=discord.ButtonStyle.secondary, row=1)
    async def color_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_embed_draft(interaction.user.id)
        current = f"{(draft.get('color') or COLOR_PRIMARY):06X}"
        await self._open_modal(interaction, "color", "Color (hex)", current=current, max_length=7, placeholder="e.g. 8B0000")

    @discord.ui.button(label="Undo", style=discord.ButtonStyle.secondary, row=1)
    async def undo_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_embed_draft(interaction.user.id)
        if not _undo_draft(draft):
            return await interaction.response.send_message(embed=error_embed("Nothing to undo yet."), ephemeral=True)
        await interaction.response.edit_message(**_panel_render_kwargs(draft))

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, row=1)
    async def reset_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_embed_draft(interaction.user.id)
        _snapshot_draft(draft)
        ch_id = draft.get("channel_id")
        for k in ("title", "description", "thumbnail", "image", "color", "links"):
            draft.pop(k, None)
        draft.update({"title": None, "description": "", "thumbnail": None, "image": None, "color": COLOR_PRIMARY, "channel_id": ch_id, "links": []})
        # Reset only clears the embed's own content, not edit-mode targeting —
        # if you're editing an existing message, Reset lets you start the
        # embed's fields over without losing track of WHICH message you're updating.
        await interaction.response.edit_message(**_panel_render_kwargs(draft))

    @discord.ui.button(label="Add Link", style=discord.ButtonStyle.secondary, row=1)
    async def add_link_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.send_modal(EmbedLinkModal())

    @discord.ui.button(label="Manage Links", style=discord.ButtonStyle.secondary, row=1)
    async def manage_links_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_embed_draft(interaction.user.id)
        links = draft.get("links") or []
        if not links:
            return await interaction.response.send_message(embed=error_embed("No link buttons configured yet — use **Add Link** first."), ephemeral=True)
        await interaction.response.send_message(content="Manage this embed's link buttons:", view=EmbedLinkManageView(interaction.user.id, links), ephemeral=True)

    @discord.ui.button(label="Send", style=discord.ButtonStyle.success, emoji=e(ICON_EMBED_SEND, "✅"), row=3)
    async def send_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_embed_draft(interaction.user.id)
        if not draft.get("title") and not draft.get("description"):
            return await interaction.response.send_message(embed=error_embed("Nothing to send yet — set a title or description first."), ephemeral=True)
        layout = build_embed_layout(draft)

        target_channel_id = draft.get("target_channel_id")
        target_message_id = draft.get("target_message_id")
        if target_channel_id and target_message_id:
            ch = interaction.guild.get_channel(target_channel_id)
            if not ch:
                return await interaction.response.send_message(embed=error_embed("Can't find the original channel anymore — the message may have been deleted."), ephemeral=True)
            try:
                msg = await ch.fetch_message(target_message_id)
                await msg.edit(view=layout)
            except discord.NotFound:
                return await interaction.response.send_message(embed=error_embed("That message doesn't exist anymore — `embed reset` and send it as new instead."), ephemeral=True)
            except discord.Forbidden:
                return await interaction.response.send_message(embed=error_embed("I don't have permission to edit messages in that channel."), ephemeral=True)
            _EMBED_DRAFTS.pop(interaction.user.id, None)
            for item in self.children:
                item.disabled = True
            return await interaction.response.edit_message(content=f"✅ Updated the existing embed in {ch.mention} — draft cleared.", view=self)

        try:
            await self.channel.send(view=layout)
        except discord.Forbidden:
            return await interaction.response.send_message(embed=error_embed("I don't have permission to send messages in that channel."), ephemeral=True)
        _EMBED_DRAFTS.pop(interaction.user.id, None)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"✅ Sent to {self.channel.mention} — draft cleared.", view=self)

@bot.tree.command(name="embed", description="Build a custom embed, or edit one this bot already sent.")
@app_commands.describe(
    channel="Channel to send a NEW embed to (omit if using message_link to edit one)",
    message_link="Link to an existing embed message (this bot's own) to edit instead of creating new",
)
async def slash_embed(
    i: discord.Interaction,
    channel: Optional[Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel]] = None,
    message_link: Optional[str] = None,
):
    if i.user.id != bot.owner_id and not i.user.guild_permissions.manage_guild:
        return await i.response.send_message(embed=error_embed("Only members with **Manage Server** or the owner can use the embed builder."), ephemeral=True)
    if not channel and not message_link:
        return await i.response.send_message(embed=error_embed("Give either `channel` (to create a new embed) or `message_link` (to edit an existing one)."), ephemeral=True)

    is_edit = False
    if message_link:
        msg, err = await _resolve_message_ref(i.guild, channel, message_link)
        if err:
            return await i.response.send_message(embed=error_embed(err), ephemeral=True)
        err = _load_message_into_draft(i.user.id, msg)
        if err:
            return await i.response.send_message(embed=error_embed(err), ephemeral=True)
        channel  = msg.channel
        is_edit  = True
    else:
        draft = _get_embed_draft(i.user.id)
        draft["channel_id"] = channel.id
        # Clear any leftover edit-mode targeting from a previous `/embed
        # message_link=` session for this user — otherwise Send would
        # silently try to edit that old message instead of posting new.
        draft["target_message_id"] = None
        draft["target_channel_id"] = None

    draft = _get_embed_draft(i.user.id)
    view  = EmbedBuilderPanel(channel, i.user.id, is_edit=is_edit)
    await i.response.send_message(view=view, **_panel_render_kwargs(draft), ephemeral=True)

# ══════════════════════════════════════════════════════════════════
# /component — same builder pattern as /embed (title, description,
# thumbnail, banner, color), but its buttons can be either a link OR a
# "response" button that shows the clicking user an ephemeral message.
# Unlike /embed, response buttons need a live interaction handler, so
# every /component message is persisted in `message_components` and its
# view re-registered via bot.add_view() in on_ready so it survives
# restarts — see MessageComponentLayout above.
# ══════════════════════════════════════════════════════════════════

def _build_component_preview_embed(draft: dict) -> discord.Embed:
    embed = discord.Embed(color=draft.get("color") or COLOR_PRIMARY, timestamp=discord.utils.utcnow())
    embed.title = draft.get("title") or None
    embed.description = draft.get("description") or "*Nothing set yet — use the buttons below to start building.*"
    if draft.get("thumbnail"):
        embed.set_thumbnail(url=draft["thumbnail"])
    if draft.get("image"):
        embed.set_image(url=draft["image"])
    embed.set_footer(text=BOT_NAME)
    return embed

def _component_render_kwargs(draft: dict) -> dict:
    buttons = draft.get("buttons") or []
    notes = []
    if draft.get("target_message_id"):
        notes.append("editing an existing message — **Update** applies changes to it")
    if buttons:
        notes.append(f"🔘 {len(buttons)} button(s) configured")
    content = f"**Message Component Builder** `{draft.get('component_id')}`" + ((" — " + " · ".join(notes)) if notes else "")
    return {"content": content, "embed": _build_component_preview_embed(draft)}

class ComponentFieldModal(discord.ui.Modal):
    """Generic single-field modal for the component builder's text
    fields — mirrors EmbedFieldModal exactly, just pointed at the
    component draft store instead."""
    def __init__(self, field: str, label: str,
                 current: str = "", style=discord.TextStyle.short, max_length: int = 256, placeholder: str = ""):
        super().__init__(title=label, timeout=300)
        self.field = field
        self.value_input = discord.ui.TextInput(
            label=label, style=style, required=False, default=current,
            max_length=max_length, placeholder=placeholder
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        draft = _get_component_draft(interaction.user.id)
        val   = self.value_input.value.strip()

        if self.field == "color":
            hex_txt = val.lstrip("#")
            if hex_txt:
                try:
                    int(hex_txt, 16)
                except ValueError:
                    return await interaction.response.send_message(embed=error_embed("Invalid hex color — try something like `8B0000`."), ephemeral=True)
        elif self.field in ("thumbnail", "image") and val and not val.startswith("http"):
            return await interaction.response.send_message(embed=error_embed("Must be a direct image URL."), ephemeral=True)

        _snapshot_draft(draft)
        if self.field == "append":
            if val:
                draft["description"] = (draft.get("description", "") + "\n" + val).strip()[:4000]
        elif self.field == "color":
            draft["color"] = int(val.lstrip("#"), 16) if val else COLOR_PRIMARY
        elif self.field in ("thumbnail", "image"):
            draft[self.field] = val or None
        else:  # title / description
            draft[self.field] = val or None

        await interaction.response.edit_message(**_component_render_kwargs(draft))

class ComponentSeparatorSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=s.capitalize(), value=s, description=SEPARATOR_STYLES[s][:40]) for s in SEPARATOR_STYLES]
        super().__init__(placeholder="Insert a separator line…", options=options, min_values=1, max_values=1, row=2)

    async def callback(self, interaction: discord.Interaction):
        draft = _get_component_draft(interaction.user.id)
        style = self.values[0]
        _snapshot_draft(draft)
        draft["description"] = (draft.get("description", "") + f"\n{SEPARATOR_STYLES[style]}\n").strip("\n")[:4000]
        await interaction.response.edit_message(**_component_render_kwargs(draft))

class ComponentLinkModal(discord.ui.Modal):
    def __init__(self, edit_index: Optional[int] = None, current: Optional[dict] = None):
        super().__init__(title="Edit Link Button" if edit_index is not None else "Add Link Button", timeout=300)
        self.edit_index = edit_index
        current = current or {}
        self.label_input = discord.ui.TextInput(label="Button label", max_length=80, default=current.get("label", ""), placeholder="e.g. Visit Our Website")
        self.url_input   = discord.ui.TextInput(label="URL", max_length=512, default=current.get("url", ""), placeholder="https://...")
        self.emoji_input = discord.ui.TextInput(label="Emoji (optional)", required=False, max_length=100, default=current.get("emoji") or "", placeholder="e.g. 🔗 or a custom emoji")
        self.add_item(self.label_input)
        self.add_item(self.url_input)
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        draft   = _get_component_draft(interaction.user.id)
        buttons = draft.setdefault("buttons", [])
        resolved_emoji, warning = ticket_types.resolve_emoji_input(interaction.guild, self.emoji_input.value)
        if self.edit_index is None:
            err = message_components.add_link_button(buttons, self.label_input.value, self.url_input.value, resolved_emoji or "")
        else:
            err = message_components.edit_link_button(buttons, self.edit_index, self.label_input.value, self.url_input.value, resolved_emoji or "")
        if err:
            return await interaction.response.send_message(embed=error_embed(err), ephemeral=True)
        if self.edit_index is not None:
            # Edited via Manage Buttons — that's a SEPARATE ephemeral message
            # from the main builder panel, so just confirm here instead of
            # trying to re-render the panel from the wrong message.
            msg = f"✅ Updated button **{self.label_input.value}**. Go back to the builder to see the updated preview."
            if warning:
                msg = f"⚠️ {warning}\n\n{msg}"
            return await interaction.response.edit_message(content=msg)
        render = _component_render_kwargs(draft)
        if warning:
            render["content"] = f"⚠️ {warning}\n\n" + render["content"]
        await interaction.response.edit_message(**render)

class ComponentResponseModal(discord.ui.Modal):
    """Step 1 of adding/editing a response-type button — label, emoji,
    and the response's thumbnail/banner. Split from step 2 (response
    title/text) because Discord caps a single modal at 5 fields and this
    button type needs 6 total."""
    def __init__(self, edit_index: Optional[int] = None, current: Optional[dict] = None):
        super().__init__(title="Edit Response Button" if edit_index is not None else "Add Response Button", timeout=300)
        self.edit_index = edit_index
        current = current or {}
        self.label_input  = discord.ui.TextInput(label="Button label", max_length=80, default=current.get("label", ""), placeholder="e.g. Rules")
        self.emoji_input  = discord.ui.TextInput(label="Emoji (optional)", required=False, max_length=100, default=current.get("emoji") or "", placeholder="e.g. 📜 or a custom emoji")
        self.thumb_input  = discord.ui.TextInput(label="Response thumbnail URL (optional)", required=False, max_length=512, default=current.get("response_thumbnail") or "", placeholder="Direct image link — shown small, top-right")
        self.banner_input = discord.ui.TextInput(label="Response banner URL (optional)", required=False, max_length=512, default=current.get("response_banner") or "", placeholder="Direct image link — shown large, below")
        self.add_item(self.label_input)
        self.add_item(self.emoji_input)
        self.add_item(self.thumb_input)
        self.add_item(self.banner_input)
        self._current = current

    async def on_submit(self, interaction: discord.Interaction):
        for field_name, val in (("thumbnail", self.thumb_input.value), ("banner", self.banner_input.value)):
            if val and not val.strip().startswith(("http://", "https://")):
                return await interaction.response.send_message(embed=error_embed(f"Response {field_name} must be a direct image URL (starting with http:// or https://)."), ephemeral=True)
        resolved_emoji, warning = ticket_types.resolve_emoji_input(interaction.guild, self.emoji_input.value)
        pending = {
            "label": self.label_input.value,
            "emoji": resolved_emoji or "",
            "response_thumbnail": self.thumb_input.value.strip(),
            "response_banner": self.banner_input.value.strip(),
            "response_title": self._current.get("response_title", ""),
            "response_description": self._current.get("response_description", ""),
            "style": self._current.get("style", "secondary"),
            "_warning": warning,
        }
        # Discord does NOT allow a modal submission to respond with
        # another modal (only a message component click can open one) —
        # so instead of chaining straight into step 2, send a tiny bridge
        # message with one button; THAT button's click is a component
        # interaction, which CAN validly open the step-2 modal.
        msg = "Almost done — click below to set the response message text:"
        if warning:
            msg = f"⚠️ {warning}\n\n{msg}"
        await interaction.response.send_message(content=msg, view=ComponentResponseStep2Prompt(pending, self.edit_index), ephemeral=True)


class ComponentResponseStep2Prompt(discord.ui.View):
    """Bridge between step 1 and step 2 of the response-button modal flow
    — see the comment in ComponentResponseModal.on_submit for why this
    extra click is necessary."""
    def __init__(self, pending: dict, edit_index: Optional[int]):
        super().__init__(timeout=300)
        self.pending    = pending
        self.edit_index = edit_index

    @discord.ui.button(label="Set Response Message", style=discord.ButtonStyle.primary, emoji="📝")
    async def open_step2(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.send_modal(ComponentResponseModalStep2(self.pending, self.edit_index))


class ComponentResponseModalStep2(discord.ui.Modal, title="Response Message"):
    """Step 2 — the response title/text actually shown to whoever clicks
    the button. Response text goes up to 4000 characters (Discord's own
    hard cap on a single modal TextInput / TextDisplay)."""
    def __init__(self, pending: dict, edit_index: Optional[int]):
        super().__init__(timeout=300)
        self.pending    = pending
        self.edit_index = edit_index
        self.title_input = discord.ui.TextInput(label="Response title (optional)", required=False, max_length=256,
                                                  default=pending.get("response_title", ""), placeholder="Shown when clicked")
        self.desc_input  = discord.ui.TextInput(label="Response text", required=False, style=discord.TextStyle.paragraph,
                                                  max_length=4000, default=pending.get("response_description", ""),
                                                  placeholder="What the clicking member sees")
        self.add_item(self.title_input)
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction):
        draft   = _get_component_draft(interaction.user.id)
        buttons = draft.setdefault("buttons", [])
        p = self.pending
        args = (p["label"], self.title_input.value, self.desc_input.value, p.get("emoji") or "",
                p.get("style", "secondary"), p.get("response_thumbnail", ""), p.get("response_banner", ""))
        if self.edit_index is None:
            err = message_components.add_response_button(buttons, *args)
        else:
            err = message_components.edit_response_button(buttons, self.edit_index, *args)
        if err:
            return await interaction.response.send_message(embed=error_embed(err), ephemeral=True)
        action = "Updated" if self.edit_index is not None else "Added"
        await interaction.response.edit_message(
            content=f"✅ {action} button **{p['label']}**. Go back to the builder to see the updated preview.",
            view=None,
        )

class ComponentButtonRemoveSelect(discord.ui.Select):
    def __init__(self, buttons: list):
        options = [
            discord.SelectOption(label=f"{i+1}. {('🔗' if b['kind']=='link' else '💬')} {b['label']}"[:100], value=str(i))
            for i, b in enumerate(buttons)
        ]
        super().__init__(placeholder="Choose a button to remove…", options=options[:25], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.selected_index = int(self.values[0])
        await interaction.response.edit_message(content=f"Selected **{self.values[0]}** — hit **Remove Selected** to confirm.", view=view)

class ComponentButtonManageView(discord.ui.View):
    def __init__(self, owner_id: int, buttons: list):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.selected_index = None
        self.add_item(ComponentButtonRemoveSelect(buttons))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error_embed("This isn't your component builder — run `/component` yourself."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Edit Selected", style=discord.ButtonStyle.primary, row=1)
    async def edit_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.selected_index is None:
            return await interaction.response.send_message(embed=error_embed("Pick a button from the dropdown first."), ephemeral=True)
        draft   = _get_component_draft(interaction.user.id)
        buttons = draft.get("buttons", [])
        if not (0 <= self.selected_index < len(buttons)):
            return await interaction.response.send_message(embed=error_embed("That button no longer exists — the list may have changed."), ephemeral=True)
        btn = buttons[self.selected_index]
        if btn["kind"] == "link":
            await interaction.response.send_modal(ComponentLinkModal(edit_index=self.selected_index, current=btn))
        else:
            await interaction.response.send_modal(ComponentResponseModal(edit_index=self.selected_index, current=btn))

    @discord.ui.button(label="Remove Selected", style=discord.ButtonStyle.danger, row=1)
    async def remove_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if self.selected_index is None:
            return await interaction.response.send_message(embed=error_embed("Pick a button from the dropdown first."), ephemeral=True)
        draft   = _get_component_draft(interaction.user.id)
        buttons = draft.get("buttons", [])
        removed = message_components.remove_button(buttons, self.selected_index)
        if removed is None:
            return await interaction.response.send_message(embed=error_embed("That button no longer exists — the list may have changed."), ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"✅ Removed button **{removed['label']}**. Go back to the builder to see the updated preview.", view=self)

class ComponentBuilderPanel(discord.ui.View):
    """The /component builder — same layout style as EmbedBuilderPanel,
    with two ways to add a button: a link (opens a URL) or a response
    (shows the clicker an ephemeral message). Sending/updating always
    persists this message's full state to `message_components` so
    response buttons keep working after a bot restart."""
    def __init__(self, channel: Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel], owner_id: int, is_edit: bool = False):
        super().__init__(timeout=900)
        self.channel  = channel
        self.owner_id = owner_id
        self.is_edit  = is_edit
        self.add_item(ComponentSeparatorSelect())
        if is_edit:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.style == discord.ButtonStyle.success:
                    item.label = "Update"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error_embed("This isn't your component builder — run `/component` yourself."), ephemeral=True)
            return False
        return True

    async def _open_modal(self, interaction, field, label, current="", **kw):
        await interaction.response.send_modal(ComponentFieldModal(field, label, current=current, **kw))

    @discord.ui.button(label="Title", style=discord.ButtonStyle.secondary, row=0)
    async def title_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_component_draft(interaction.user.id)
        await self._open_modal(interaction, "title", "Title", current=draft.get("title") or "", max_length=256)

    @discord.ui.button(label="Description", style=discord.ButtonStyle.secondary, row=0)
    async def desc_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_component_draft(interaction.user.id)
        await self._open_modal(interaction, "description", "Description", current=draft.get("description") or "",
                                style=discord.TextStyle.paragraph, max_length=4000, placeholder="Replaces the whole body")

    @discord.ui.button(label="Add Line", style=discord.ButtonStyle.secondary, row=0)
    async def append_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await self._open_modal(interaction, "append", "Text to add", style=discord.TextStyle.paragraph,
                                max_length=1000, placeholder="Added as a new line onto the existing description")

    @discord.ui.button(label="Thumbnail", style=discord.ButtonStyle.secondary, row=0)
    async def thumb_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_component_draft(interaction.user.id)
        await self._open_modal(interaction, "thumbnail", "Thumbnail URL", current=draft.get("thumbnail") or "", placeholder="Direct image link")

    @discord.ui.button(label="Banner", style=discord.ButtonStyle.secondary, row=0)
    async def banner_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_component_draft(interaction.user.id)
        await self._open_modal(interaction, "image", "Banner / Image URL", current=draft.get("image") or "", placeholder="Direct image link")

    @discord.ui.button(label="Color", style=discord.ButtonStyle.secondary, row=1)
    async def color_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_component_draft(interaction.user.id)
        current = f"{(draft.get('color') or COLOR_PRIMARY):06X}"
        await self._open_modal(interaction, "color", "Color (hex)", current=current, max_length=7, placeholder="e.g. 8B0000")

    @discord.ui.button(label="Undo", style=discord.ButtonStyle.secondary, row=1)
    async def undo_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_component_draft(interaction.user.id)
        if not _undo_draft(draft):
            return await interaction.response.send_message(embed=error_embed("Nothing to undo yet."), ephemeral=True)
        await interaction.response.edit_message(**_component_render_kwargs(draft))

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, row=1)
    async def reset_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_component_draft(interaction.user.id)
        _snapshot_draft(draft)
        keep = {k: draft.get(k) for k in ("component_id", "channel_id", "target_message_id", "target_channel_id")}
        draft.clear()
        draft.update(keep)
        draft.update({"title": None, "description": "", "thumbnail": None, "image": None, "color": COLOR_PRIMARY, "buttons": []})
        await interaction.response.edit_message(**_component_render_kwargs(draft))

    @discord.ui.button(label="Add Link", style=discord.ButtonStyle.secondary, row=1)
    async def add_link_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.send_modal(ComponentLinkModal())

    @discord.ui.button(label="Add Button", style=discord.ButtonStyle.secondary, row=3)
    async def add_response_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.send_modal(ComponentResponseModal())

    @discord.ui.button(label="Manage Buttons", style=discord.ButtonStyle.secondary, row=3)
    async def manage_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft   = _get_component_draft(interaction.user.id)
        buttons = draft.get("buttons") or []
        if not buttons:
            return await interaction.response.send_message(embed=error_embed("No buttons configured yet — use **Add Link** or **Add Button** first."), ephemeral=True)
        await interaction.response.send_message(content="Manage this message's buttons:", view=ComponentButtonManageView(interaction.user.id, buttons), ephemeral=True)

    @discord.ui.button(label="Send", style=discord.ButtonStyle.success, emoji=e(ICON_EMBED_SEND, "✅"), row=3)
    async def send_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_component_draft(interaction.user.id)
        if not draft.get("title") and not draft.get("description"):
            return await interaction.response.send_message(embed=error_embed("Nothing to send yet — set a title or description first."), ephemeral=True)

        component_id = draft["component_id"]
        gc = guild_cfg(cfg, interaction.guild.id)
        comp = gc["message_components"].setdefault(component_id, {})
        comp.update({
            "title": draft.get("title"), "description": draft.get("description") or "",
            "thumbnail": draft.get("thumbnail"), "image": draft.get("image"),
            "color": draft.get("color") or COLOR_PRIMARY, "buttons": draft.get("buttons") or [],
        })

        target_channel_id = draft.get("target_channel_id")
        target_message_id = draft.get("target_message_id")
        if target_channel_id and target_message_id:
            ch = interaction.guild.get_channel(target_channel_id)
            if not ch:
                return await interaction.response.send_message(embed=error_embed("Can't find the original channel anymore — the message may have been deleted."), ephemeral=True)
            try:
                msg = await ch.fetch_message(target_message_id)
                layout = MessageComponentLayout(component_id, comp)
                await msg.edit(view=layout)
            except discord.NotFound:
                return await interaction.response.send_message(embed=error_embed("That message doesn't exist anymore — `Reset` and send it as new instead."), ephemeral=True)
            except discord.Forbidden:
                return await interaction.response.send_message(embed=error_embed("I don't have permission to edit messages in that channel."), ephemeral=True)
            comp["channel_id"], comp["message_id"] = ch.id, msg.id
            save_config(cfg)
            bot.add_view(layout)
            _COMPONENT_DRAFTS.pop(interaction.user.id, None)
            for item in self.children:
                item.disabled = True
            return await interaction.response.edit_message(content=f"✅ Updated the existing message in {ch.mention} — draft cleared.", view=self)

        try:
            layout = MessageComponentLayout(component_id, comp)
            msg = await self.channel.send(view=layout)
        except discord.Forbidden:
            return await interaction.response.send_message(embed=error_embed("I don't have permission to send messages in that channel."), ephemeral=True)
        comp["channel_id"], comp["message_id"] = self.channel.id, msg.id
        save_config(cfg)
        bot.add_view(layout)
        _COMPONENT_DRAFTS.pop(interaction.user.id, None)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"✅ Sent to {self.channel.mention} — draft cleared.", view=self)

@bot.tree.command(name="component", description="Build a message with link and/or response buttons.")
@app_commands.describe(
    component_id="A short ID for this message (reuse it later to edit the same message)",
    channel="Channel to send a NEW message to (omit if component_id already exists — it'll load that one to edit)",
)
async def slash_component(
    i: discord.Interaction,
    component_id: str,
    channel: Optional[Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel]] = None,
):
    if i.user.id != bot.owner_id and not i.user.guild_permissions.manage_guild:
        return await i.response.send_message(embed=error_embed("Only members with **Manage Server** or the owner can use the component builder."), ephemeral=True)

    component_id = re.sub(r"[^a-z0-9_-]+", "-", component_id.lower()).strip("-") or "component"
    gc = guild_cfg(cfg, i.guild.id)
    existing = gc["message_components"].get(component_id)
    draft = _get_component_draft(i.user.id)
    draft.clear()

    if existing:
        draft.update({
            "component_id": component_id,
            "title": existing.get("title"), "description": existing.get("description") or "",
            "thumbnail": existing.get("thumbnail"), "image": existing.get("image"),
            "color": existing.get("color") or COLOR_PRIMARY,
            "buttons": [dict(b) for b in existing.get("buttons", [])],
            "channel_id": existing.get("channel_id"),
            "target_message_id": existing.get("message_id"), "target_channel_id": existing.get("channel_id"),
            "_history": [],
        })
        is_edit = True
        target_channel = channel or i.guild.get_channel(existing.get("channel_id") or 0)
        if not target_channel:
            return await i.response.send_message(embed=error_embed("Couldn't find the original channel anymore — give `channel` explicitly."), ephemeral=True)
    else:
        if not channel:
            return await i.response.send_message(embed=error_embed(f"No message component `{component_id}` exists yet — give `channel` to create a new one."), ephemeral=True)
        draft.update({
            "component_id": component_id, "title": None, "description": "", "thumbnail": None,
            "image": None, "color": COLOR_PRIMARY, "channel_id": channel.id,
            "buttons": [], "target_message_id": None, "target_channel_id": None, "_history": [],
        })
        is_edit = False
        target_channel = channel

    view = ComponentBuilderPanel(target_channel, i.user.id, is_edit=is_edit)
    await i.response.send_message(view=view, **_component_render_kwargs(draft), ephemeral=True)

@bot.command(name="component", aliases=["comp"])
async def pfx_component(ctx, component_id: str = "", *, rest: str = ""):
    """Prefix entry point for the same builder as /component — opens the
    interactive panel (buttons need a real interaction, which prefix
    commands can trigger a message with, same pattern as !vx embed)."""
    if ctx.author.id != bot.owner_id and not ctx.author.guild_permissions.manage_guild:
        return await ctx.send(embed=error_embed("Only members with **Manage Server** or the owner can use the component builder."))
    if not component_id:
        return await ctx.send(embed=info_embed("Message Component Builder", (
            "`component <id> #channel` — start a NEW message (or continue an existing `<id>` in that channel)\n"
            "`component <id>` — reopen an existing message component's builder (no channel needed if it already exists)\n\n"
            "Buttons can be **links** (open a URL) or **responses** (show the clicking member an ephemeral message) — "
            "add/manage both from the builder that opens."
        )))

    component_id = re.sub(r"[^a-z0-9_-]+", "-", component_id.lower()).strip("-") or "component"
    channel = ctx.message.channel_mentions[0] if ctx.message.channel_mentions else None
    gc = guild_cfg(cfg, ctx.guild.id)
    existing = gc["message_components"].get(component_id)
    draft = _get_component_draft(ctx.author.id)
    draft.clear()

    if existing:
        draft.update({
            "component_id": component_id,
            "title": existing.get("title"), "description": existing.get("description") or "",
            "thumbnail": existing.get("thumbnail"), "image": existing.get("image"),
            "color": existing.get("color") or COLOR_PRIMARY,
            "buttons": [dict(b) for b in existing.get("buttons", [])],
            "channel_id": existing.get("channel_id"),
            "target_message_id": existing.get("message_id"), "target_channel_id": existing.get("channel_id"),
            "_history": [],
        })
        is_edit = True
        target_channel = channel or ctx.guild.get_channel(existing.get("channel_id") or 0)
        if not target_channel:
            return await ctx.send(embed=error_embed("Couldn't find the original channel anymore — mention a channel explicitly."))
    else:
        if not channel:
            return await ctx.send(embed=error_embed(f"No message component `{component_id}` exists yet — mention a channel: `component {component_id} #channel`"))
        draft.update({
            "component_id": component_id, "title": None, "description": "", "thumbnail": None,
            "image": None, "color": COLOR_PRIMARY, "channel_id": channel.id,
            "buttons": [], "target_message_id": None, "target_channel_id": None, "_history": [],
        })
        is_edit = False
        target_channel = channel

    view = ComponentBuilderPanel(target_channel, ctx.author.id, is_edit=is_edit)
    await ctx.send(view=view, **_component_render_kwargs(draft))

# ══════════════════════════════════════════════════════════════════
# /ticketpanel — same builder pattern as /embed, but for ticket panels:
# title, description, welcome message, thumbnail, banner, color, button
# label/emoji/color, and button-vs-dropdown opening style.
# ══════════════════════════════════════════════════════════════════

class TicketFieldModal(discord.ui.Modal):
    """Generic single-field modal for the ticket builder's text fields —
    mirrors EmbedFieldModal, refreshing the panel via edit_message() the
    same reliable way (tied to the button that opened it)."""
    def __init__(self, field: str, label: str, current: str = "",
                 style=discord.TextStyle.short, max_length: int = 256, placeholder: str = ""):
        super().__init__(title=label, timeout=300)
        self.field = field
        self.value_input = discord.ui.TextInput(
            label=label, style=style, required=False, default=current,
            max_length=max_length, placeholder=placeholder
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        draft = _get_ticket_draft(interaction.user.id)
        val   = self.value_input.value.strip()

        if self.field == "color":
            hex_txt = val.lstrip("#")
            if hex_txt:
                try:
                    int(hex_txt, 16)
                except ValueError:
                    return await interaction.response.send_message(embed=error_embed("Invalid hex color — try something like `8B0000`."), ephemeral=True)
        elif self.field in ("thumbnail", "image") and val and not val.startswith("http"):
            return await interaction.response.send_message(embed=error_embed("Must be a direct image URL."), ephemeral=True)

        _snapshot_draft(draft)
        if self.field == "color":
            draft["color"] = int(val.lstrip("#"), 16) if val else COLOR_PRIMARY
        else:
            draft[self.field] = val or None

        await interaction.response.edit_message(**_ticket_render_kwargs(interaction.guild, draft))

class TicketButtonModal(discord.ui.Modal, title="Button Label & Emoji"):
    """Both button fields at once since a Modal supports multiple inputs
    — one popup instead of two separate ones for label vs emoji."""
    def __init__(self, current_label: str, current_emoji: str):
        super().__init__(timeout=300)
        self.label_input = discord.ui.TextInput(label="Button Label", default=current_label, max_length=80, required=False)
        self.emoji_input = discord.ui.TextInput(label="Button Emoji (optional)", default=current_emoji, max_length=100, required=False, placeholder="e.g. 🎫 or a custom emoji")
        self.add_item(self.label_input)
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        draft = _get_ticket_draft(interaction.user.id)
        _snapshot_draft(draft)
        draft["button_label"] = self.label_input.value.strip() or "Open Ticket"
        resolved, warning = ticket_types.resolve_emoji_input(interaction.guild, self.emoji_input.value)
        draft["button_emoji"] = resolved or ""
        render = _ticket_render_kwargs(interaction.guild, draft)
        if warning:
            render["content"] = f"⚠️ {warning}\n\n" + render["content"]
        await interaction.response.edit_message(**render)

class TicketButtonStyleSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Red (Danger)",      value="danger",    emoji="🔴"),
            discord.SelectOption(label="Green (Success)",    value="success",   emoji="🟢"),
            discord.SelectOption(label="Blurple (Primary)",  value="primary",   emoji="🔵"),
            discord.SelectOption(label="Gray (Secondary)",   value="secondary", emoji="⚪"),
        ]
        super().__init__(placeholder="Button color…", options=options, min_values=1, max_values=1, row=2)

    async def callback(self, interaction: discord.Interaction):
        draft = _get_ticket_draft(interaction.user.id)
        _snapshot_draft(draft)
        draft["button_style"] = self.values[0]
        await interaction.response.edit_message(**_ticket_render_kwargs(interaction.guild, draft))

class TicketOpenTypeSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Button", value="button", emoji="🔘", description="A single clickable button"),
            discord.SelectOption(label="Dropdown", value="dropdown", emoji="📋", description="A select menu instead of a button"),
        ]
        super().__init__(placeholder="Opening style: button or dropdown…", options=options, min_values=1, max_values=1, row=3)

    async def callback(self, interaction: discord.Interaction):
        draft = _get_ticket_draft(interaction.user.id)
        _snapshot_draft(draft)
        draft["open_type"] = self.values[0]
        await interaction.response.edit_message(**_ticket_render_kwargs(interaction.guild, draft))

async def _resync_panel_message(guild: discord.Guild, panel_id: str, panel: dict) -> bool:
    """Re-render the live panel message's view (used whenever `types` or
    open_type changes) so users see the current dropdown/button without
    needing to repost the whole panel. Returns True on success."""
    ch = guild.get_channel(panel.get("channel_id") or 0)
    if not ch or not panel.get("message_id"):
        return False
    try:
        msg = await ch.fetch_message(panel["message_id"])
        await msg.edit(view=TicketPanelLayout(panel_id, panel, guild=guild))
        return True
    except Exception:
        return False


class TicketTypeInfoModal(discord.ui.Modal, title="Add Ticket Type"):
    """Step 1 of adding a type from inside the panel builder — text fields
    only (label/emoji/description/max tickets). Category/log/role need
    real Discord pickers a modal can't contain, so step 2 continues in a
    follow-up view (TicketTypeCategoryView) with actual Select components."""
    def __init__(self):
        super().__init__(timeout=300)
        self.label_input = discord.ui.TextInput(label="Label (shown in dropdown)", max_length=100, placeholder="e.g. Report a Bug")
        self.emoji_input = discord.ui.TextInput(label="Emoji (optional)", required=False, max_length=100, placeholder="e.g. 🐞 or a custom emoji")
        self.desc_input  = discord.ui.TextInput(label="Description (optional)", required=False, max_length=100, placeholder="Shown under the label in the dropdown")
        self.max_input   = discord.ui.TextInput(label="Max tickets per user (default 1)", required=False, max_length=2, placeholder="1-5")
        self.add_item(self.label_input)
        self.add_item(self.emoji_input)
        self.add_item(self.desc_input)
        self.add_item(self.max_input)

    async def on_submit(self, interaction: discord.Interaction):
        resolved_emoji, warning = ticket_types.resolve_emoji_input(interaction.guild, self.emoji_input.value)
        raw_max = (self.max_input.value or "").strip()
        try:
            max_tickets = max(1, min(5, int(raw_max))) if raw_max else 1
        except ValueError:
            max_tickets = 1
        pending = {
            "label":       self.label_input.value.strip()[:100] or "New Type",
            "emoji":       resolved_emoji or "",
            "description": (self.desc_input.value or "").strip()[:100],
            "max_tickets": max_tickets,
        }
        # `pending` is carried directly on THIS view instance (not stashed
        # in the per-user draft) so two overlapping Add Type sessions for
        # the same user can never cross-contaminate each other's label —
        # each step-2 message only ever knows about its own submission.
        draft    = _get_ticket_draft(interaction.user.id)
        panel_id = draft.get("panel_id")
        content  = f"**{pending['label']}** — now pick a category (required), log channel & role (both optional) below, then **Save Type**."
        if warning:
            content = f"⚠️ {warning}\n\n" + content
        await interaction.response.send_message(content=content, view=TicketTypeCategoryView(interaction.user.id, panel_id, pending), ephemeral=True)


class TicketTypeCategoryView(discord.ui.View):
    """Step 2 of Add Type — the real Discord pickers a modal can't hold.
    Finalizes the type (adds it to the panel's `types` dict and resyncs
    the live message) once Save Type is pressed with a category chosen.
    Carries its own panel_id + pending label/emoji/description so it never
    depends on (or can be clobbered by) another overlapping Add Type
    session for the same user."""
    def __init__(self, owner_id: int, panel_id: str, pending: dict):
        super().__init__(timeout=300)
        self.owner_id         = owner_id
        self.panel_id         = panel_id
        self.pending          = pending
        self.category_id      = None
        self.log_channel_id   = None
        self.support_role_id  = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error_embed("This isn't your ticket type setup — run it yourself."), ephemeral=True)
            return False
        return True

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.category],
                        placeholder="Category for this type (required)", row=0)
    async def category_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.category_id = select.values[0].id
        await interaction.response.edit_message(content=f"Category set to {select.values[0].mention}. Pick log channel/role (optional), then **Save Type**.", view=self)

    @discord.ui.select(cls=discord.ui.ChannelSelect,
                        channel_types=[discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.public_thread, discord.ChannelType.private_thread],
                        placeholder="Log channel (optional)", row=1)
    async def log_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.log_channel_id = select.values[0].id if select.values else None
        await interaction.response.edit_message(content=f"Log channel set to {select.values[0].mention}. Pick a role (optional), then **Save Type**.", view=self)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Support role (optional)", row=2)
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        self.support_role_id = select.values[0].id if select.values else None
        await interaction.response.edit_message(content=f"Role set to {select.values[0].mention}. Hit **Save Type** to finish.", view=self)

    @discord.ui.button(label="Save Type", style=discord.ButtonStyle.success, row=3)
    async def save_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        pending  = self.pending
        panel_id = self.panel_id
        if not pending or not panel_id:
            return await interaction.response.send_message(embed=error_embed("This setup expired — start again with the **Add Type** button in the builder."), ephemeral=True)
        if not self.category_id:
            return await interaction.response.send_message(embed=error_embed("Pick a category first — it's required."), ephemeral=True)

        gc     = guild_cfg(cfg, interaction.guild.id)
        panel  = gc["ticket"]["panels"].setdefault(panel_id, {})
        types  = panel.setdefault("types", {})
        if len(types) >= ticket_types.MAX_TYPES_PER_PANEL:
            return await interaction.response.send_message(embed=error_embed(f"This panel already has the max {ticket_types.MAX_TYPES_PER_PANEL} types Discord allows."), ephemeral=True)

        type_key = ticket_types.slugify_type_id(pending["label"], types)
        types[type_key] = {
            "label": pending["label"], "emoji": pending["emoji"], "description": pending["description"],
            "category": self.category_id, "log_channel": self.log_channel_id,
            "support_role": self.support_role_id, "max_tickets": pending["max_tickets"],
        }
        save_config(cfg)

        resynced = await _resync_panel_message(interaction.guild, panel_id, panel)
        note = "The live panel message was updated to show the new dropdown." if resynced else \
            "Config saved — hit **Create/Update Panel** in the builder to show it live."
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=(f"✅ Added type `{type_key}` (**{pending['label']}**) to panel `{panel_id}`. "
                     f"This panel now has **{len(types)}** type(s) — "
                     f"{'a dropdown will show automatically' if len(types) >= 2 else 'add one more to activate the dropdown'}.\n{note}"),
            view=self
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=3)
    async def cancel_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Cancelled — no type was added.", view=self)


class TicketTypeRemoveSelect(discord.ui.Select):
    def __init__(self, panel: dict, guild: discord.Guild = None):
        types = panel.get("types", {})
        options = [
            discord.SelectOption(label=(t.get("label") or key)[:100], value=key,
                                  emoji=ticket_types.safe_emoji_for_guild(guild, t.get("emoji")) or None)
            for key, t in types.items()
        ][:25]
        super().__init__(placeholder="Choose a type to remove…", options=options, min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.selected_key = self.values[0]
        await interaction.response.edit_message(content=f"Selected `{self.values[0]}` — hit **Remove Selected** to confirm.", view=view)


class TicketTypeManageView(discord.ui.View):
    def __init__(self, owner_id: int, panel_id: str, panel: dict, guild: discord.Guild = None):
        super().__init__(timeout=300)
        self.owner_id      = owner_id
        self.panel_id      = panel_id
        self.selected_key  = None
        self.add_item(TicketTypeRemoveSelect(panel, guild))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error_embed("This isn't your ticket type manager — run it yourself."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Remove Selected", style=discord.ButtonStyle.danger, row=1)
    async def remove_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        if not self.selected_key:
            return await interaction.response.send_message(embed=error_embed("Pick a type from the dropdown first."), ephemeral=True)
        gc    = guild_cfg(cfg, interaction.guild.id)
        panel = gc["ticket"]["panels"].get(self.panel_id)
        if not panel or self.selected_key not in panel.get("types", {}):
            return await interaction.response.send_message(embed=error_embed("That type no longer exists."), ephemeral=True)
        # Defer FIRST — _resync_panel_message() below does a real Discord
        # API call (fetch + edit the live panel message), which can
        # occasionally take longer than the 3-second ack window.
        await interaction.response.defer()
        removed = panel["types"].pop(self.selected_key)
        save_config(cfg)
        resynced = await _resync_panel_message(interaction.guild, self.panel_id, panel)
        note = "The live panel message was updated." if resynced else "The panel message wasn't found — update it from the builder if needed."
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(
            content=f"✅ Removed type `{self.selected_key}` (**{removed.get('label', self.selected_key)}**) from panel `{self.panel_id}`.\n{note}",
            view=self
        )


class TicketPanelBuilderView(discord.ui.View):
    """Full /ticketpanel builder — every visual setting a ticket panel
    supports, as clickable buttons + modals + selects instead of chat
    subcommands. Structural setup (category/log/role/max) is provided
    upfront as slash command parameters, since those need real Discord
    pickers that only exist at command-invocation time, not inside a
    modal."""
    def __init__(self, owner_id: int, post_channel: Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel], is_edit: bool = False):
        super().__init__(timeout=900)
        self.owner_id     = owner_id
        self.post_channel = post_channel
        self.is_edit      = is_edit  # True when this panel_id already has a live message — reuses/edits it instead of posting a duplicate
        self.add_item(TicketButtonStyleSelect())
        self.add_item(TicketOpenTypeSelect())
        if is_edit:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.style == discord.ButtonStyle.success:
                    item.label = "Update Panel"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error_embed("This isn't your ticket builder — run `/ticketpanel` yourself."), ephemeral=True)
            return False
        return True

    async def _open_modal(self, interaction, field, label, current="", **kw):
        await interaction.response.send_modal(TicketFieldModal(field, label, current=current, **kw))

    @discord.ui.button(label="Title", style=discord.ButtonStyle.secondary, row=0)
    async def title_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_ticket_draft(interaction.user.id)
        await self._open_modal(interaction, "title", "Title", current=draft.get("title") or "", max_length=256)

    @discord.ui.button(label="Description", style=discord.ButtonStyle.secondary, row=0)
    async def desc_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_ticket_draft(interaction.user.id)
        await self._open_modal(interaction, "description", "Description", current=draft.get("description") or "",
                                style=discord.TextStyle.paragraph, max_length=4000)

    @discord.ui.button(label="Welcome Msg", style=discord.ButtonStyle.secondary, row=0)
    async def welcome_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_ticket_draft(interaction.user.id)
        await self._open_modal(interaction, "welcome_message", "Welcome Message (posted inside the ticket)",
                                current=draft.get("welcome_message") or "", style=discord.TextStyle.paragraph,
                                max_length=2000, placeholder="Placeholders: {user} {server} {panel}")

    @discord.ui.button(label="Thumbnail", style=discord.ButtonStyle.secondary, row=0)
    async def thumb_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_ticket_draft(interaction.user.id)
        await self._open_modal(interaction, "thumbnail", "Thumbnail URL", current=draft.get("thumbnail") or "", placeholder="Direct image link")

    @discord.ui.button(label="Banner", style=discord.ButtonStyle.secondary, row=0)
    async def banner_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_ticket_draft(interaction.user.id)
        await self._open_modal(interaction, "image", "Banner / Image URL", current=draft.get("image") or "", placeholder="Direct image link")

    @discord.ui.button(label="Color", style=discord.ButtonStyle.secondary, row=1)
    async def color_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_ticket_draft(interaction.user.id)
        current = f"{(draft.get('color') or COLOR_PRIMARY):06X}"
        await self._open_modal(interaction, "color", "Color (hex)", current=current, max_length=7, placeholder="e.g. 8B0000")

    @discord.ui.button(label="Button Text", style=discord.ButtonStyle.secondary, row=1)
    async def button_text_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_ticket_draft(interaction.user.id)
        await interaction.response.send_modal(TicketButtonModal(draft.get("button_label") or "Open Ticket", draft.get("button_emoji") or ""))

    @discord.ui.button(label="Undo", style=discord.ButtonStyle.secondary, row=1)
    async def undo_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_ticket_draft(interaction.user.id)
        if not _undo_draft(draft):
            return await interaction.response.send_message(embed=error_embed("Nothing to undo yet."), ephemeral=True)
        await interaction.response.edit_message(**_ticket_render_kwargs(interaction.guild, draft))

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, row=1)
    async def reset_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        draft = _get_ticket_draft(interaction.user.id)
        _snapshot_draft(draft)
        draft.update({
            "title": "Support Tickets", "description": "Click the button below to open a support ticket.",
            "welcome_message": None, "thumbnail": None, "image": None, "color": COLOR_PRIMARY,
            "button_label": "Open Ticket", "button_emoji": "", "button_style": "danger", "open_type": "button",
        })
        await interaction.response.edit_message(**_ticket_render_kwargs(interaction.guild, draft))

    @discord.ui.button(label="Add Type", style=discord.ButtonStyle.secondary, row=1)
    async def add_type_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        """Adds a dropdown 'type' with its own category/log/role — this is
        what turns a single-category panel into a multi-category dropdown
        (2+ types = dropdown shows automatically). Uses ticket_types.py."""
        await interaction.response.send_modal(TicketTypeInfoModal())

    @discord.ui.button(label="Manage Types", style=discord.ButtonStyle.secondary, row=4)
    async def manage_types_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        # Defer FIRST — avoids "didn't respond in time" if anything (config
        # lookup, guild_cfg init/save) takes a beat longer than Discord's
        # 3-second ack window, same fix already applied to create_btn above.
        await interaction.response.defer(ephemeral=True)
        draft    = _get_ticket_draft(interaction.user.id)
        panel_id = draft.get("panel_id")
        gc       = guild_cfg(cfg, interaction.guild.id)
        panel    = gc["ticket"]["panels"].get(panel_id)
        if not panel or not panel.get("types"):
            return await interaction.followup.send(embed=error_embed("This panel has no ticket types configured yet — use **Add Type** first."), ephemeral=True)
        await interaction.followup.send(
            content=f"Manage ticket types on panel `{panel_id}`:",
            view=TicketTypeManageView(interaction.user.id, panel_id, panel, guild=interaction.guild),
            ephemeral=True
        )

    @discord.ui.button(label="Create Panel", style=discord.ButtonStyle.success, row=4)
    async def create_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        # Defer FIRST — sending the panel message + save_config() (blocking
        # file write) can occasionally take longer than Discord's 3-second
        # ack window. Without deferring, a slow tick makes Discord show
        # "This interaction failed" to the user even though the panel
        # message went out fine underneath — which is exactly the
        # confusing state this fixes.
        await interaction.response.defer(ephemeral=True)

        draft  = _get_ticket_draft(interaction.user.id)
        gc     = guild_cfg(cfg, interaction.guild.id)
        panels = gc["ticket"]["panels"]
        panel_id = draft.get("panel_id")
        panel  = panels.setdefault(panel_id, {})
        panel.update({
            "title":           draft.get("title") or "Support Tickets",
            "description":     draft.get("description") or "Click the button below to open a support ticket.",
            "category":        draft.get("category_id"),
            "log_channel":     draft.get("log_channel_id"),
            "support_role":    draft.get("support_role_id"),
            "max_tickets":     draft.get("max_tickets", 1),
            "welcome_message": draft.get("welcome_message"),
            "thumbnail":       draft.get("thumbnail"),
            "image":           draft.get("image"),
            "color":           draft.get("color") or COLOR_PRIMARY,
            "button_label":    draft.get("button_label") or "Open Ticket",
            "button_emoji":    draft.get("button_emoji") or "",
            "button_style":    draft.get("button_style") or "danger",
            "open_type":       draft.get("open_type") or "button",
        })

        # If this panel_id already has a live message (i.e. a ticket panel
        # was already created before — meaning tickets may already have
        # been opened from it), edit that SAME message in place instead of
        # posting a duplicate. Only falls back to posting a new message if
        # the old one is gone (deleted manually, channel removed, etc).
        old_channel_id = panel.get("channel_id")
        old_message_id = panel.get("message_id")
        edited_existing = False
        target_channel  = self.post_channel

        if old_channel_id and old_message_id:
            old_channel = interaction.guild.get_channel(old_channel_id)
            if old_channel:
                try:
                    old_msg = await old_channel.fetch_message(old_message_id)
                    await old_msg.edit(view=TicketPanelLayout(panel_id, panel, guild=interaction.guild))
                    panel["channel_id"] = old_channel.id
                    target_channel      = old_channel
                    edited_existing     = True
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    edited_existing = False

        if not edited_existing:
            try:
                msg = await self.post_channel.send(view=TicketPanelLayout(panel_id, panel, guild=interaction.guild))
            except discord.Forbidden:
                return await interaction.followup.send(embed=error_embed("I don't have permission to send messages in that channel."), ephemeral=True)
            except discord.HTTPException as ex:
                return await interaction.followup.send(embed=error_embed(f"Discord rejected the panel message: {ex}"), ephemeral=True)
            panel["message_id"], panel["channel_id"] = msg.id, self.post_channel.id
            target_channel = self.post_channel

        save_config(cfg)
        _TICKET_DRAFTS.pop(interaction.user.id, None)
        for item in self.children:
            item.disabled = True

        verb = "updated" if edited_existing else "created"
        note = "" if (target_channel.id == self.post_channel.id) else \
            f"\n*(Edited the existing panel message in {target_channel.mention} — pick that same channel next time to avoid this note.)*"
        await interaction.edit_original_response(
            content=f"✅ Ticket panel `{panel_id}` {verb} in {target_channel.mention}.{note}", embed=None, view=self
        )

@bot.tree.command(name="ticketpanel", description="Build a fully custom ticket panel and post it to a channel.")
@app_commands.describe(
    panel_id="Short ID for this panel (letters/numbers/-/_ only, e.g. 'support')",
    category="Category where new ticket channels will be created",
    log_channel="Channel where ticket open/close logs are sent",
    post_channel="Channel where the panel message itself gets posted",
    support_role="Role that can see/manage tickets from this panel (optional)",
    max_tickets="Max open tickets per user for this panel (default 1)"
)
async def slash_ticketpanel(
    i: discord.Interaction,
    panel_id: str,
    category: discord.CategoryChannel,
    log_channel: Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel],
    post_channel: Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel],
    support_role: Optional[discord.Role] = None,
    max_tickets: Optional[int] = 1
):
    if i.user.id != bot.owner_id and not i.user.guild_permissions.manage_guild:
        return await i.response.send_message(embed=error_embed("Only Manage Server permission holders or the owner can build ticket panels."), ephemeral=True)

    panel_id = panel_id.lower().strip()
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", panel_id):
        return await i.response.send_message(embed=error_embed("Panel ID can only contain lowercase letters, numbers, `-`, `_` (max 32 characters)."), ephemeral=True)

    gc       = guild_cfg(cfg, i.guild.id)
    existing = gc["ticket"]["panels"].get(panel_id, {})

    draft = _get_ticket_draft(i.user.id)
    draft.clear()
    draft.update({
        "panel_id": panel_id,
        "category_id": category.id,
        "log_channel_id": log_channel.id,
        "support_role_id": support_role.id if support_role else None,
        "max_tickets": max(1, min(5, max_tickets or 1)),
        "title": existing.get("title", "Support Tickets"),
        "description": existing.get("description", "Click the button below to open a support ticket."),
        "welcome_message": existing.get("welcome_message"),
        "thumbnail": existing.get("thumbnail"),
        "image": existing.get("image"),
        "color": existing.get("color", COLOR_PRIMARY),
        "button_label": existing.get("button_label", "Open Ticket"),
        "button_emoji": existing.get("button_emoji", ""),
        "button_style": existing.get("button_style", "danger"),
        "open_type": existing.get("open_type", "button"),
        "_history": [],
    })

    is_edit = bool(existing.get("message_id") and existing.get("channel_id"))
    view    = TicketPanelBuilderView(i.user.id, post_channel, is_edit=is_edit)
    render  = _ticket_render_kwargs(i.guild, draft)
    if is_edit:
        old_ch = i.guild.get_channel(existing["channel_id"])
        intro  = (
            f"**Ticket Panel Builder** `{panel_id}` — this panel already exists"
            + (f" in {old_ch.mention}" if old_ch else "") +
            ". Editing it will update that same message in place (existing tickets aren't affected). "
            "Use the buttons below, then **Update Panel** to apply.\n\n"
        )
    else:
        intro = f"**Ticket Panel Builder** `{panel_id}` — will post in {post_channel.mention}. Use the buttons below.\n\n"
    await i.response.send_message(
        content=intro + render["content"], embed=render["embed"], view=view, ephemeral=True
    )

# ══════════════════════════════════════════════════════════════════
# /tickettype — multi-category dropdown support (ticket_types.py).
# Separate command group instead of cramming into /ticketpanel's modal
# builder, since category/log/role need real Discord pickers that only
# exist as slash parameters, not inside a modal's text fields.
# ══════════════════════════════════════════════════════════════════

tickettype_group = app_commands.Group(name="tickettype", description="Manage multiple ticket types (categories) inside one panel.")

def _tickettype_permission_check(i: discord.Interaction) -> bool:
    return i.user.id == bot.owner_id or i.user.guild_permissions.manage_guild

@tickettype_group.command(name="add", description="Add a ticket type (its own category/log/role) to a panel.")
@app_commands.describe(
    panel_id="The panel to add this type to (must already exist — see /ticketpanel)",
    label="Shown in the dropdown, e.g. 'Report a Bug'",
    category="Category where this type's tickets get created",
    emoji="Emoji shown next to this type in the dropdown (optional)",
    description="Short description shown under the label in the dropdown (optional)",
    log_channel="Log channel for this type (optional — falls back to the panel's own log channel)",
    support_role="Role that can see this type's tickets (optional — falls back to the panel's role)",
    max_tickets="Max open tickets per user for this type (default 1)"
)
async def tickettype_add(
    i: discord.Interaction, panel_id: str, label: str, category: discord.CategoryChannel,
    emoji: Optional[str] = None, description: Optional[str] = None,
    log_channel: Optional[Union[discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel]] = None,
    support_role: Optional[discord.Role] = None, max_tickets: Optional[int] = 1
):
    if not _tickettype_permission_check(i):
        return await i.response.send_message(embed=error_embed("Only Manage Server permission holders or the owner can manage ticket types."), ephemeral=True)
    gc     = guild_cfg(cfg, i.guild.id)
    panels = gc["ticket"]["panels"]
    panel  = panels.get(panel_id.lower().strip())
    if not panel:
        return await i.response.send_message(embed=error_embed(f"Panel `{panel_id}` doesn't exist yet — create it first with `/ticketpanel`."), ephemeral=True)

    types = panel.setdefault("types", {})
    if len(types) >= ticket_types.MAX_TYPES_PER_PANEL:
        return await i.response.send_message(embed=error_embed(f"This panel already has the max {ticket_types.MAX_TYPES_PER_PANEL} types Discord allows in one dropdown."), ephemeral=True)

    type_key = ticket_types.slugify_type_id(label, types)
    resolved_emoji, emoji_warning = ticket_types.resolve_emoji_input(i.guild, emoji or "")
    types[type_key] = {
        "label": label[:100], "emoji": resolved_emoji or "", "description": (description or "").strip(),
        "category": category.id, "log_channel": log_channel.id if log_channel else None,
        "support_role": support_role.id if support_role else None, "max_tickets": max(1, min(5, max_tickets or 1)),
    }
    save_config(cfg)

    resynced = await _resync_panel_message(i.guild, panel_id, panel)
    note = "The live panel message was updated to show the new dropdown." if resynced else \
        "Config saved, but the panel message wasn't found — run `/ticketpanel` again (same panel_id) to repost it with the dropdown."
    warn = f"\n⚠️ {emoji_warning}" if emoji_warning else ""
    await i.response.send_message(embed=success_embed(
        f"Added type `{type_key}` (**{label}**) to panel `{panel_id}`.\n"
        f"This panel now has **{len(types)}** type(s) — {'a dropdown will show automatically' if len(types) >= 2 else 'add one more to activate the dropdown'}.\n{note}{warn}"
    ), ephemeral=True)

@tickettype_group.command(name="remove", description="Remove a ticket type from a panel.")
@app_commands.describe(panel_id="The panel to remove from", type_id="The type's ID (see /tickettype list)")
async def tickettype_remove(i: discord.Interaction, panel_id: str, type_id: str):
    if not _tickettype_permission_check(i):
        return await i.response.send_message(embed=error_embed("Only Manage Server permission holders or the owner can manage ticket types."), ephemeral=True)
    gc     = guild_cfg(cfg, i.guild.id)
    panel  = gc["ticket"]["panels"].get(panel_id.lower().strip())
    if not panel:
        return await i.response.send_message(embed=error_embed(f"Panel `{panel_id}` doesn't exist."), ephemeral=True)
    types = panel.get("types", {})
    if type_id not in types:
        return await i.response.send_message(embed=error_embed(f"No type `{type_id}` on panel `{panel_id}`. Use `/tickettype list` to see valid IDs."), ephemeral=True)
    removed = types.pop(type_id)
    save_config(cfg)

    resynced = await _resync_panel_message(i.guild, panel_id, panel)
    note = "The live panel message was updated." if resynced else "The panel message wasn't found — repost it with `/ticketpanel` if needed."
    await i.response.send_message(embed=success_embed(f"Removed type `{type_id}` (**{removed.get('label', type_id)}**) from panel `{panel_id}`.\n{note}"), ephemeral=True)

@tickettype_group.command(name="list", description="View every ticket type configured on a panel.")
@app_commands.describe(panel_id="The panel to inspect")
async def tickettype_list(i: discord.Interaction, panel_id: str):
    gc    = guild_cfg(cfg, i.guild.id)
    panel = gc["ticket"]["panels"].get(panel_id.lower().strip())
    if not panel:
        return await i.response.send_message(embed=error_embed(f"Panel `{panel_id}` doesn't exist."), ephemeral=True)
    summary = ticket_types.format_type_list(panel, i.guild.get_channel, i.guild.get_role)
    await i.response.send_message(embed=info_embed(f"Ticket Types — `{panel_id}`", summary), ephemeral=True)

bot.tree.add_command(tickettype_group)



@bot.event
async def on_member_join(member: discord.Member):
    # Verification runs for ANY guild that has it configured & enabled —
    # deliberately placed before the support-server-only return below,
    # since that early return only gates the badge/welcome-DM logic.
    if not member.bot:
        await _apply_unverified_role(member)

    support_server_id = int(os.getenv("SUPPORT_SERVER_ID", "0"))
    if member.guild.id != support_server_id or member.bot:
        return
    uid = member.id
    # Grant the USER badge when joining the support server
    support_members = cfg.setdefault("support_server_members", [])
    if uid not in support_members:
        support_members.append(uid)
        save_config(cfg)

    boosted = can_receive_join_boost(uid)
    if boosted:
        grant_xp_boost(uid, minutes=60, multiplier=1.15)
        mark_join_boost_granted(uid)

    badge_lines, _ = _badge_display_lines(uid)
    role   = get_bot_role(uid)
    bonus_line = (
        "Bonus: **+15% XP Boost** active for **60 minutes** on every server using " + BOT_NAME + "!\n\n"
        if boosted else ""
    )
    embed  = discord.Embed(
        title="Welcome to " + member.guild.name + "!",
        description=(
            "Hey " + member.mention + "!\n\n"
            "You just earned the **USER** badge!\n"
            + bonus_line +
            "Type `profile` to see your badges.\n\nType `help` to see every command."
        ),
        color=COLOR_PRIMARY,
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name=f"{e(ICON_BADGES, '✨')} ALL BADGES".strip(), value="\n".join(badge_lines), inline=True)
    embed.add_field(name="Bot Role", value=role.capitalize(), inline=True)
    embed.set_footer(text=BOT_NAME + " \u2022 " + BOT_TAGLINE)
    try:
        await member.send(embed=embed)
    except discord.Forbidden:
        pass
    gc = guild_cfg(cfg, member.guild.id)
    main_ch_id = gc.get("main_channel") or gc.get("announce_channel")
    if main_ch_id:
        ch = member.guild.get_channel(main_ch_id)
        if ch:
            w = discord.Embed(description=member.mention + " has joined!", color=COLOR_PRIMARY, timestamp=discord.utils.utcnow())
            w.set_author(name=str(member), icon_url=member.display_avatar.url)
            w.set_footer(text=BOT_NAME)
            try:
                await ch.send(embed=w)
            except Exception:
                pass

@bot.tree.error
async def on_app_command_error(i: discord.Interaction, error: app_commands.AppCommandError):
    msg = str(error)
    unexpected = True
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use this command."
        unexpected = False
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = f"Slow down — try again in **{error.retry_after:.1f}s**."
        unexpected = False
    elif "channel id specified is invalid" in msg.lower():
        # Known Discord-side glitch: happens when a client (often mobile)
        # is holding a stale cached copy of this command's option
        # definitions, so the channel it submits doesn't match what
        # Discord currently expects. Not something our code can catch
        # earlier — it fails during Discord's own option resolution,
        # before our command body ever runs.
        msg = (
            "Discord rejected the channel you picked — this usually means your app has a stale cached "
            "copy of this command. Fully close and reopen Discord (or try from desktop), then run the "
            "command again."
        )
        unexpected = False
    if unexpected:
        real_error = getattr(error, "original", error)
        cmd_name = i.command.qualified_name if i.command else "unknown"
        await report_error(
            real_error, location=f"/{cmd_name}",
            user=i.user, guild=i.guild, channel=i.channel
        )
        msg = "Something went wrong running that command — it's been reported automatically."
    try:
        await i.response.send_message(embed=error_embed(msg), ephemeral=True)
    except discord.InteractionResponded:
        try:
            await i.followup.send(embed=error_embed(msg), ephemeral=True)
        except Exception:
            pass

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(embed=error_embed(f"Slow down — try again in **{error.retry_after:.1f}s**."), delete_after=5)
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.send(embed=error_embed("You don't have access to this command."), delete_after=5)
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=error_embed(f"Missing argument: `{error.param.name}`"), delete_after=5)
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(embed=error_embed(f"Invalid argument: {error}"), delete_after=5)
        return
    real_error = getattr(error, "original", error)
    await report_error(
        real_error, location=ctx.command.qualified_name if ctx.command else "unknown",
        user=ctx.author, guild=ctx.guild, channel=ctx.channel
    )
    try:
        await ctx.send(embed=error_embed("Something went wrong running that command — it's been reported automatically."), delete_after=8)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable is not set.")

    async def main():
        async with bot:
            await bot.start(token)

    asyncio.run(main())
