"""
VALLENT EXS — Web Dashboard
==============================================================
Self-contained, same design as vote_system.py — never imports anything
from vallent.py. This module only knows how to (1) run the Discord OAuth2
login flow, (2) manage a signed session cookie, and (3) serve the
dashboard's HTML/JS + JSON API routes. vallent.py wires it into the SAME
aiohttp app the top.gg vote webhook already runs on, and hands it small
getter/setter callables so this file never touches `cfg`/`bot` directly —
the bot process stays the only thing that ever reads or writes
data/config.json, so there's no risk of the dashboard and the bot racing
each other to save the file.

Why a dashboard needs its own backend at all
----------------------------------------------
A pure static site (like the marketing pages) can't do a Discord OAuth2
login — that flow needs a server that holds a secret (the OAuth Client
Secret) and can make a server-to-server request to Discord to exchange a
login code for that user's identity. That's what all of this is.

Session design
----------------
No database and no server-side session store — sessions are a single
HMAC-signed, timestamped cookie. It holds the logged-in user's id/name/
avatar plus the list of guilds they're allowed to manage (fetched once
from Discord at login and cached for the cookie's lifetime). Tampering
with the cookie invalidates its signature; Discord permissions changing
mid-session just means they see a slightly stale guild list until they
log in again — an acceptable trade-off for a first version of this.

Every request that would actually change a guild's config re-checks,
live, that the bot is still in that guild (`get_bot().get_guild(...)`) —
the cached "can manage" flag from login only ever grants read access to
try; a live bot-presence check gates anything that writes.
"""

import base64
import hashlib
import hmac
import json
import re
import time
import logging
from typing import Callable, Optional, Awaitable
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

log = logging.getLogger("dashboard")

DISCORD_API = "https://discord.com/api/v10"
SESSION_COOKIE = "vx_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
MANAGE_GUILD_BIT = 0x20


# ══════════════════════════════════════════════════════════════════
# SIGNED SESSION COOKIE — stateless, no DB/session store needed
# ══════════════════════════════════════════════════════════════════

def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()

def create_session(data: dict, secret: str, max_age: int = SESSION_MAX_AGE) -> str:
    body = {"data": data, "exp": time.time() + max_age}
    b64 = base64.urlsafe_b64encode(json.dumps(body).encode()).decode()
    return f"{b64}.{_sign(b64, secret)}"

def read_session(cookie_value: Optional[str], secret: str) -> Optional[dict]:
    if not cookie_value or "." not in cookie_value:
        return None
    b64, sig = cookie_value.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(b64, secret)):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(b64.encode()))
        if body["exp"] < time.time():
            return None
        return body["data"]
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# DASHBOARD APP FACTORY
# ══════════════════════════════════════════════════════════════════

def build_dashboard_routes(
    app: web.Application,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    session_secret: str,
    get_bot: Callable[[], object],
    get_leveling: Callable[[int], dict],
    set_leveling: Callable[[int, dict], Optional[str]],
    add_leveling_noxp_role: Callable[[int, int], Optional[str]],
    remove_leveling_noxp_role: Callable[[int, int], None],
    set_leveling_role_reward: Callable[[int, int, int], Optional[str]],
    remove_leveling_role_reward: Callable[[int, int], None],
    get_tickets: Callable[[int], dict],
    set_ticket_panel: Callable[[int, str, dict], Optional[str]],
    send_ticket_panel: Callable[[int, str, dict, int], Awaitable[Optional[str]]],
    get_components: Callable[[int], dict],
    set_component: Callable[[int, str, dict], Optional[str]],
    send_component: Callable[[int, str, dict, int], Awaitable[Optional[str]]],
    add_component_button: Callable[[int, str, dict], Optional[str]],
    remove_component_button: Callable[[int, str, int], Optional[str]],
    get_antinuke: Callable[[int], dict],
    set_antinuke: Callable[[int, dict], Optional[str]],
    add_antinuke_whitelist: Callable[[int, int], Optional[str]],
    remove_antinuke_whitelist: Callable[[int, int], None],
    get_antispam: Callable[[int], dict],
    set_antispam: Callable[[int, dict], Optional[str]],
    add_antispam_ignore: Callable[[int, str, int], Optional[str]],
    remove_antispam_ignore: Callable[[int, str, int], None],
    get_customization: Callable[[int], dict],
    set_rank_colors: Callable[[int, Optional[list]], Optional[str]],
    set_rank_background: Callable[[int, Optional[str]], Optional[str]],
    set_profile_colors: Callable[[int, Optional[list]], Optional[str]],
    set_profile_background: Callable[[int, Optional[str]], Optional[str]],
    get_payment_methods: Callable[[], dict],
    create_premium_order: Callable[[int, str, str, str, str, str], str],
) -> None:
    """Registers every /auth, /dashboard, and /api route onto the shared
    aiohttp `app` (the same one the top.gg webhook runs on). Everything
    this module needs from the bot/config side comes in as a callable:
      - get_bot()                    -> the discord.py Bot instance
      - get_leveling(guild_id)       -> {"enabled", "channel_id", "difficulty", "xp_min", "xp_max",
        "cooldown", "message", "noxp_roles": [{"id","name"}], "level_roles": [{"level","role_id","role_name"}]}
      - set_leveling(guild_id, dict) -> applies + persists a partial update; returns an error string or None
      - add/remove_leveling_noxp_role(guild_id, role_id) -> mutate the no-XP role list by one role
      - set_leveling_role_reward(guild_id, level, role_id) -> upsert one level's role reward; returns an error string or None
      - remove_leveling_role_reward(guild_id, level) -> remove one level's role reward
      - get_tickets(guild_id) -> {"panels": [{"id","title","description","welcome_message","category_id",
        "category_name","log_channel_id","log_channel_name","support_role_id","support_role_name",
        "max_tickets","thumbnail","image","color","button_label","button_emoji","button_style",
        "open_type","is_live","post_channel_id","post_channel_name","types_count"}]}
      - set_ticket_panel(guild_id, panel_id, dict) -> settings-only update on a panel that already
        exists; applies + persists, and refreshes the live panel message in place if there is one.
        Returns an error string or None.
      - send_ticket_panel(guild_id, panel_id, dict, post_channel_id) -> ASYNC (await this one).
        Creates a brand-new panel and posts it, OR updates + reposts/moves an existing one — same
        mechanics as the /ticketpanel builder's own Send/Update step (edits the existing live message
        in place if it's still there, otherwise posts fresh in post_channel_id). Returns an error
        string or None.
      - get_components(guild_id) -> {"components": [{"id","title","description","thumbnail","image",
        "color","buttons":[{"index","kind","label","url","emoji","style","response_title",
        "response_description","response_thumbnail","response_banner","description"}],"is_live",
        "post_channel_id","post_channel_name"}], "max_buttons": int} — mirrors the `/component` builder
        (a Container message with link and/or response buttons).
      - set_component(guild_id, component_id, dict) -> settings-only update (title/description/
        thumbnail/image/color) on a message that already exists; refreshes the live message in place
        if there is one. Returns an error string or None.
      - send_component(guild_id, component_id, dict, post_channel_id) -> ASYNC (await this one).
        Same create-or-move mechanics as send_ticket_panel, for a message component instead of a
        ticket panel.
      - add_component_button(guild_id, component_id, dict) -> dict needs "kind": "link" (+ label, url,
        emoji) or "kind": "response" (+ label, response_title, response_description, emoji, style,
        response_thumbnail, response_banner). Appends one button; returns an error string or None, and
        refreshes the live message if there is one.
      - remove_component_button(guild_id, component_id, index) -> removes one button by its index;
        returns an error string or None, and refreshes the live message if there is one.
      - get_antinuke(guild_id)       -> {"enabled", "log_channel", "punishment", "whitelist": [...], "bot_has_audit_log_perm"}
      - set_antinuke(guild_id, dict) -> applies + persists a partial update; returns an error string or None
      - add/remove_antinuke_whitelist(guild_id, user_id) -> mutate the whitelist by one user
      - get_customization(uid)       -> {"is_premium", "rank_colors", "rank_background", "profile_colors", "profile_background"}
        (rank_* mirrors the `rankcolor`/`rankbg` commands — rank card + level-up card;
         profile_* mirrors `idcardcolor`/`idcardbg` — the `profile` ID card, kept separate)
      - set_rank_colors/set_profile_colors(uid, [hex, hex, hex?] | None) -> error string or None; None removes the gradient
      - set_rank_background/set_profile_background(uid, url | None) -> error string or None; None removes the background
        All five are user-scoped (not guild-scoped) and are expected to enforce the Premium
        gate themselves, exactly like the equivalent Discord commands do.
      - get_payment_methods() -> {"qris": {...}, "bank": {...}, "ewallet": {...}} — only the enabled
        ones (and only their display fields) are meant to reach the client; the bot owner configures
        these themselves once real payment is wired up.
      - create_premium_order(uid, username, product, plan_id, plan_label, price) -> order_id (str).
        Called once a logged-in user submits the checkout form on /dashboard/checkout. This is an
        ORDER, not a confirmed payment — persisting it and (best-effort) notifying the bot owner is
        entirely up to this callable; the actual payment verification/fulfillment stays manual
        (the owner still runs `grantpremium`/`noprefix` themselves) until real payment processing
        is wired up.
    """

    def _session_from_request(request: web.Request) -> Optional[dict]:
        return read_session(request.cookies.get(SESSION_COOKIE), session_secret)

    def _guild_summaries(bot) -> Callable[[int], Optional[dict]]:
        def _get(guild_id: int):
            g = bot.get_guild(guild_id)
            if not g:
                return None
            return {"id": str(g.id), "name": g.name, "icon": str(g.icon.url) if g.icon else None}
        return _get

    # ---------------- OAuth2 login flow ----------------

    async def handle_login(request: web.Request) -> web.Response:
        next_path = request.query.get("next", "")
        # Only ever redirect back into our own /dashboard/* — never an
        # open redirect to an arbitrary host.
        state = next_path if next_path.startswith("/dashboard") else ""
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
            "prompt": "none",
        }
        if state:
            params["state"] = state
        return web.HTTPFound(f"https://discord.com/oauth2/authorize?{urlencode(params)}")

    async def handle_callback(request: web.Request) -> web.Response:
        code = request.query.get("code")
        if not code:
            return web.HTTPFound("/dashboard?error=missing_code")

        async with aiohttp.ClientSession() as session:
            token_resp = await session.post(
                f"{DISCORD_API}/oauth2/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_resp.status != 200:
                log.warning(f"OAuth token exchange failed: {token_resp.status} {await token_resp.text()}")
                return web.HTTPFound("/dashboard?error=oauth_failed")
            token_data = await token_resp.json()
            access_token = token_data["access_token"]
            auth_header = {"Authorization": f"Bearer {access_token}"}

            user_resp = await session.get(f"{DISCORD_API}/users/@me", headers=auth_header)
            guilds_resp = await session.get(f"{DISCORD_API}/users/@me/guilds", headers=auth_header)
            if user_resp.status != 200 or guilds_resp.status != 200:
                return web.HTTPFound("/dashboard?error=oauth_failed")
            user = await user_resp.json()
            user_guilds = await guilds_resp.json()

        bot = get_bot()
        manageable = []
        for g in user_guilds:
            try:
                perms = int(g.get("permissions", 0))
            except (TypeError, ValueError):
                perms = 0
            can_manage = g.get("owner") or (perms & MANAGE_GUILD_BIT) == MANAGE_GUILD_BIT
            if not can_manage:
                continue
            bot_present = bot.get_guild(int(g["id"])) is not None if bot else False
            manageable.append({
                "id": g["id"], "name": g["name"],
                "icon": (f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png" if g.get("icon") else None),
                "bot_present": bot_present,
            })

        session_data = {
            "uid": user["id"],
            "username": user.get("global_name") or user["username"],
            "avatar": (f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png" if user.get("avatar") else None),
            "guilds": manageable,
        }
        cookie = create_session(session_data, session_secret)
        dest = request.query.get("state") or "/dashboard"
        if not dest.startswith("/dashboard"):
            dest = "/dashboard"
        resp = web.HTTPFound(dest)
        resp.set_cookie(SESSION_COOKIE, cookie, max_age=SESSION_MAX_AGE, httponly=True, samesite="Lax", secure=True)
        return resp

    async def handle_logout(request: web.Request) -> web.Response:
        resp = web.HTTPFound("/dashboard")
        resp.del_cookie(SESSION_COOKIE)
        return resp

    # ---------------- JSON API ----------------

    async def api_me(request: web.Request) -> web.Response:
        sess = _session_from_request(request)
        if not sess:
            return web.json_response({"logged_in": False})
        bot = get_bot()
        guilds = sess.get("guilds", [])
        if bot:
            for g in guilds:
                g["bot_present"] = bot.get_guild(int(g["id"])) is not None
        return web.json_response({
            "logged_in": True,
            "username": sess["username"],
            "avatar": sess["avatar"],
            "guilds": guilds,
        })

    def _require_guild_access(request: web.Request, guild_id: str):
        """Returns (session, error_response_or_None). Checks the cached
        'can manage' flag from login AND a LIVE bot-presence check — the
        cache only ever grants trying to read; presence is always fresh."""
        sess = _session_from_request(request)
        if not sess:
            return None, web.json_response({"error": "not_logged_in"}, status=401)
        allowed = any(g["id"] == guild_id for g in sess.get("guilds", []))
        if not allowed:
            return None, web.json_response({"error": "forbidden"}, status=403)
        bot = get_bot()
        if not bot or not bot.get_guild(int(guild_id)):
            return None, web.json_response({"error": "bot_not_in_guild"}, status=404)
        return sess, None

    async def api_get_leveling(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        return web.json_response(get_leveling(int(guild_id)))

    async def api_patch_leveling(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        # lightweight CSRF mitigation: cross-site form posts can't set this
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        update = {}
        if "enabled" in body:
            update["enabled"] = bool(body["enabled"])
        if "channel_id" in body:
            update["channel_id"] = str(body["channel_id"]) if body["channel_id"] else None
        if "difficulty" in body:
            try:
                d = float(body["difficulty"])
            except (TypeError, ValueError):
                return web.json_response({"error": "invalid_difficulty"}, status=400)
            if not (0.1 <= d <= 10):
                return web.json_response({"error": "difficulty_out_of_range"}, status=400)
            update["difficulty"] = d
        if "xp_min" in body or "xp_max" in body:
            try:
                if "xp_min" in body:
                    update["xp_min"] = int(body["xp_min"])
                if "xp_max" in body:
                    update["xp_max"] = int(body["xp_max"])
            except (TypeError, ValueError):
                return web.json_response({"error": "invalid_xp_range"}, status=400)
        if "cooldown" in body:
            try:
                update["cooldown"] = int(body["cooldown"])
            except (TypeError, ValueError):
                return web.json_response({"error": "invalid_cooldown"}, status=400)
        if "message" in body:
            update["message"] = str(body["message"])[:800]
        error = set_leveling(int(guild_id), update)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_leveling(int(guild_id)))

    async def api_guild_roles(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        bot = get_bot()
        guild = bot.get_guild(int(guild_id))
        roles = [{"id": str(r.id), "name": r.name} for r in guild.roles if not r.is_default()]
        roles.sort(key=lambda r: r["name"].lower())
        return web.json_response(roles)

    async def api_guild_categories(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        bot = get_bot()
        guild = bot.get_guild(int(guild_id))
        cats = [{"id": str(c.id), "name": c.name} for c in guild.categories]
        cats.sort(key=lambda c: c["name"].lower())
        return web.json_response(cats)

    async def api_guild_emojis(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        bot = get_bot()
        guild = bot.get_guild(int(guild_id))
        emojis = [{"id": str(e.id), "name": e.name, "animated": e.animated, "url": str(e.url), "tag": f"<{'a' if e.animated else ''}:{e.name}:{e.id}>"} for e in guild.emojis]
        emojis.sort(key=lambda e: e["name"].lower())
        return web.json_response(emojis)

    async def api_get_tickets(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        return web.json_response(get_tickets(int(guild_id)))

    def _extract_ticket_fields(body: dict) -> dict:
        fields = {}
        for key in ("title", "description", "welcome_message", "thumbnail", "image", "color", "button_label", "button_emoji", "button_style", "open_type"):
            if key in body:
                fields[key] = str(body[key]) if body[key] is not None else ""
        for key in ("category_id", "log_channel_id", "support_role_id"):
            if key in body:
                fields[key] = str(body[key]) if body[key] else None
        if "max_tickets" in body:
            fields["max_tickets"] = body["max_tickets"]
        return fields

    async def api_patch_ticket_panel(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        panel_id = request.match_info["panel_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        update = _extract_ticket_fields(body)
        error = set_ticket_panel(int(guild_id), panel_id, update)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_tickets(int(guild_id)))

    async def api_post_ticket_panel(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        panel_id = str(body.get("panel_id", "")).strip()
        post_channel_id = body.get("post_channel_id")
        if not panel_id or not post_channel_id:
            return web.json_response({"error": "Pick a panel ID and a channel to post in."}, status=400)
        try:
            post_channel_id = int(post_channel_id)
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_channel"}, status=400)
        fields = _extract_ticket_fields(body)
        error = await send_ticket_panel(int(guild_id), panel_id, fields, post_channel_id)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_tickets(int(guild_id)))

    async def api_get_components(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        return web.json_response(get_components(int(guild_id)))

    def _extract_component_fields(body: dict) -> dict:
        fields = {}
        for key in ("title", "description", "thumbnail", "image", "color"):
            if key in body:
                fields[key] = str(body[key]) if body[key] is not None else ""
        return fields

    async def api_patch_component(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        component_id = request.match_info["component_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        update = _extract_component_fields(body)
        error = set_component(int(guild_id), component_id, update)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_components(int(guild_id)))

    async def api_post_component(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        component_id = str(body.get("component_id", "")).strip()
        post_channel_id = body.get("post_channel_id")
        if not component_id or not post_channel_id:
            return web.json_response({"error": "Pick a message ID and a channel to post in."}, status=400)
        try:
            post_channel_id = int(post_channel_id)
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_channel"}, status=400)
        fields = _extract_component_fields(body)
        error = await send_component(int(guild_id), component_id, fields, post_channel_id)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_components(int(guild_id)))

    async def api_add_component_button(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        component_id = request.match_info["component_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        kind = str(body.get("kind", "")).strip()
        button_fields = {"kind": kind}
        for key in ("label", "url", "emoji", "style", "response_title", "response_description", "response_thumbnail", "response_banner"):
            if key in body:
                button_fields[key] = str(body[key]) if body[key] is not None else ""
        error = add_component_button(int(guild_id), component_id, button_fields)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_components(int(guild_id)))

    async def api_remove_component_button(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        component_id = request.match_info["component_id"]
        index = request.match_info["index"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            index = int(index)
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_index"}, status=400)
        error = remove_component_button(int(guild_id), component_id, index)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_components(int(guild_id)))

    async def api_add_leveling_noxp(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
            role_id = int(body["id"])
        except Exception:
            return web.json_response({"error": "invalid_id"}, status=400)
        error = add_leveling_noxp_role(int(guild_id), role_id)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_leveling(int(guild_id)))

    async def api_remove_leveling_noxp(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        role_id = request.match_info["role_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            remove_leveling_noxp_role(int(guild_id), int(role_id))
        except Exception:
            return web.json_response({"error": "invalid_id"}, status=400)
        return web.json_response(get_leveling(int(guild_id)))

    async def api_set_leveling_role_reward(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
            level = int(body["level"])
            role_id = int(body["role_id"])
        except Exception:
            return web.json_response({"error": "invalid_id"}, status=400)
        error = set_leveling_role_reward(int(guild_id), level, role_id)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_leveling(int(guild_id)))

    async def api_remove_leveling_role_reward(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        level = request.match_info["level"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            remove_leveling_role_reward(int(guild_id), int(level))
        except Exception:
            return web.json_response({"error": "invalid_level"}, status=400)
        return web.json_response(get_leveling(int(guild_id)))

    async def api_guild_channels(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        bot = get_bot()
        guild = bot.get_guild(int(guild_id))
        channels = [{"id": str(c.id), "name": c.name} for c in guild.text_channels]
        return web.json_response(channels)

    async def api_get_antinuke(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        return web.json_response(get_antinuke(int(guild_id)))

    async def api_patch_antinuke(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        update = {}
        if "enabled" in body:
            update["enabled"] = bool(body["enabled"])
        if "log_channel" in body:
            update["log_channel"] = str(body["log_channel"]) if body["log_channel"] else None
        if "punishment" in body:
            update["punishment"] = str(body["punishment"])
        error = set_antinuke(int(guild_id), update)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_antinuke(int(guild_id)))

    async def api_add_antinuke_whitelist(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
            user_id = int(body["user_id"])
        except Exception:
            return web.json_response({"error": "invalid_user_id"}, status=400)
        error = add_antinuke_whitelist(int(guild_id), user_id)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_antinuke(int(guild_id)))

    async def api_remove_antinuke_whitelist(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        user_id = request.match_info["user_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            remove_antinuke_whitelist(int(guild_id), int(user_id))
        except Exception:
            return web.json_response({"error": "invalid_user_id"}, status=400)
        return web.json_response(get_antinuke(int(guild_id)))

    async def api_get_antispam(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        return web.json_response(get_antispam(int(guild_id)))

    async def api_patch_antispam(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)
        update = {}
        for field in ("trap_channel", "log_channel"):
            if field in body:
                update[field] = str(body[field]) if body[field] else None
        if "punishment" in body:
            update["punishment"] = str(body["punishment"])
        for field in ("threshold", "window", "flood_count", "flood_window"):
            if field in body:
                update[field] = body[field]
        error = set_antispam(int(guild_id), update)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_antispam(int(guild_id)))

    async def api_add_antispam_ignore(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        kind = request.match_info["kind"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if kind not in ("user", "role"):
            return web.json_response({"error": "invalid_kind"}, status=400)
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
            target_id = int(body["id"])
        except Exception:
            return web.json_response({"error": "invalid_id"}, status=400)
        error = add_antispam_ignore(int(guild_id), kind, target_id)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_antispam(int(guild_id)))

    async def api_remove_antispam_ignore(request: web.Request) -> web.Response:
        guild_id = request.match_info["guild_id"]
        kind = request.match_info["kind"]
        target_id = request.match_info["target_id"]
        _, err = _require_guild_access(request, guild_id)
        if err:
            return err
        if kind not in ("user", "role"):
            return web.json_response({"error": "invalid_kind"}, status=400)
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            remove_antispam_ignore(int(guild_id), kind, int(target_id))
        except Exception:
            return web.json_response({"error": "invalid_id"}, status=400)
        return web.json_response(get_antispam(int(guild_id)))

    # ---------------- My Rank Card / Profile customization (user-scoped) ----------------
    # Same validation shapes as the `rankcolor`/`rankbg`/`idcardcolor`/`idcardbg`
    # commands: 2-3 six-digit hex colors, or a direct image URL ending in
    # .png/.jpg/.jpeg/.webp. The injected setters own the authoritative check
    # (and the Premium gate) — this just does cheap shape validation so bad
    # input never even reaches them.
    _HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")
    _URL_RE = re.compile(r"^https?://\S+\.(png|jpe?g|webp)(\?\S*)?$", re.IGNORECASE)

    def _parse_colors_body(body: dict):
        """Returns (colors_or_None_or_SENTINEL_missing, error_or_None).
        `colors: null` (or an empty list) means "remove the gradient"."""
        if "colors" not in body:
            return None, "missing_colors"
        colors = body["colors"]
        if colors is None or colors == []:
            return None, None
        if not isinstance(colors, list) or not (2 <= len(colors) <= 3) or not all(isinstance(c, str) for c in colors):
            return None, "Give 2 or 3 hex colors, e.g. #A672FF."
        if not all(_HEX_RE.match(c.strip()) for c in colors):
            return None, "That doesn't look like a valid hex color — use 6-digit hex like #A672FF."
        return [c.strip().lstrip("#") for c in colors], None

    def _parse_url_body(body: dict):
        if "url" not in body:
            return None, "missing_url"
        url = body["url"]
        if not url:
            return None, None
        url = str(url).strip()
        if not _URL_RE.match(url):
            return None, "That doesn't look like a valid direct image URL — it must start with http(s):// and end in .png, .jpg, .jpeg, or .webp."
        return url, None

    async def api_get_customization(request: web.Request) -> web.Response:
        sess = _session_from_request(request)
        if not sess:
            return web.json_response({"error": "not_logged_in"}, status=401)
        return web.json_response(get_customization(int(sess["uid"])))

    async def _handle_customization_patch(request: web.Request, kind: str) -> web.Response:
        sess = _session_from_request(request)
        if not sess:
            return web.json_response({"error": "not_logged_in"}, status=401)
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)

        uid = int(sess["uid"])
        if kind in ("rankcolor", "idcardcolor"):
            value, err = _parse_colors_body(body)
            if err == "missing_colors":
                return web.json_response({"error": "missing_colors"}, status=400)
            if err:
                return web.json_response({"error": err}, status=400)
            setter = set_rank_colors if kind == "rankcolor" else set_profile_colors
        else:
            value, err = _parse_url_body(body)
            if err == "missing_url":
                return web.json_response({"error": "missing_url"}, status=400)
            if err:
                return web.json_response({"error": err}, status=400)
            setter = set_rank_background if kind == "rankbg" else set_profile_background

        error = setter(uid, value)
        if error:
            return web.json_response({"error": error}, status=400)
        return web.json_response(get_customization(uid))

    async def api_patch_rankcolor(request: web.Request) -> web.Response:
        return await _handle_customization_patch(request, "rankcolor")

    async def api_patch_rankbg(request: web.Request) -> web.Response:
        return await _handle_customization_patch(request, "rankbg")

    async def api_patch_idcardcolor(request: web.Request) -> web.Response:
        return await _handle_customization_patch(request, "idcardcolor")

    async def api_patch_idcardbg(request: web.Request) -> web.Response:
        return await _handle_customization_patch(request, "idcardbg")

    # ---------------- Premium checkout (order intake — no live payment yet) ----------------

    _PRODUCTS = {"premium", "noprefix", "badge"}

    async def api_get_payment_methods(request: web.Request) -> web.Response:
        sess = _session_from_request(request)
        if not sess:
            return web.json_response({"error": "not_logged_in"}, status=401)
        return web.json_response(get_payment_methods())

    async def api_post_premium_order(request: web.Request) -> web.Response:
        sess = _session_from_request(request)
        if not sess:
            return web.json_response({"error": "not_logged_in"}, status=401)
        if request.headers.get("X-Requested-With") != "vallent-dashboard":
            return web.json_response({"error": "bad_request"}, status=400)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)

        product   = str(body.get("product", "")).strip().lower()
        plan_id   = str(body.get("plan_id", "")).strip()
        plan_label = str(body.get("plan_label", "")).strip()[:60]
        price     = str(body.get("price", "")).strip()[:20]
        if product not in _PRODUCTS or not plan_id or not plan_label or not price:
            return web.json_response({"error": "Missing or invalid order details."}, status=400)

        order_id = create_premium_order(int(sess["uid"]), sess["username"], product, plan_id, plan_label, price)
        return web.json_response({"order_id": order_id})

    # ---------------- Frontend shell ----------------

    async def serve_dashboard_shell(request: web.Request) -> web.Response:
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    app.router.add_get("/auth/discord/login", handle_login)
    app.router.add_get("/auth/discord/callback", handle_callback)
    app.router.add_get("/auth/discord/logout", handle_logout)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/me/customization", api_get_customization)
    app.router.add_patch("/api/me/rankcolor", api_patch_rankcolor)
    app.router.add_patch("/api/me/rankbg", api_patch_rankbg)
    app.router.add_patch("/api/me/idcardcolor", api_patch_idcardcolor)
    app.router.add_patch("/api/me/idcardbg", api_patch_idcardbg)
    app.router.add_get("/api/payment-methods", api_get_payment_methods)
    app.router.add_post("/api/premium/order", api_post_premium_order)
    app.router.add_get("/api/guilds/{guild_id}/leveling", api_get_leveling)
    app.router.add_patch("/api/guilds/{guild_id}/leveling", api_patch_leveling)
    app.router.add_post("/api/guilds/{guild_id}/leveling/noxp", api_add_leveling_noxp)
    app.router.add_delete("/api/guilds/{guild_id}/leveling/noxp/{role_id}", api_remove_leveling_noxp)
    app.router.add_post("/api/guilds/{guild_id}/leveling/role-reward", api_set_leveling_role_reward)
    app.router.add_delete("/api/guilds/{guild_id}/leveling/role-reward/{level}", api_remove_leveling_role_reward)
    app.router.add_get("/api/guilds/{guild_id}/channels", api_guild_channels)
    app.router.add_get("/api/guilds/{guild_id}/roles", api_guild_roles)
    app.router.add_get("/api/guilds/{guild_id}/categories", api_guild_categories)
    app.router.add_get("/api/guilds/{guild_id}/emojis", api_guild_emojis)
    app.router.add_get("/api/guilds/{guild_id}/tickets", api_get_tickets)
    app.router.add_post("/api/guilds/{guild_id}/tickets", api_post_ticket_panel)
    app.router.add_patch("/api/guilds/{guild_id}/tickets/{panel_id}", api_patch_ticket_panel)
    app.router.add_get("/api/guilds/{guild_id}/components", api_get_components)
    app.router.add_post("/api/guilds/{guild_id}/components", api_post_component)
    app.router.add_patch("/api/guilds/{guild_id}/components/{component_id}", api_patch_component)
    app.router.add_post("/api/guilds/{guild_id}/components/{component_id}/buttons", api_add_component_button)
    app.router.add_delete("/api/guilds/{guild_id}/components/{component_id}/buttons/{index}", api_remove_component_button)
    app.router.add_get("/api/guilds/{guild_id}/antinuke", api_get_antinuke)
    app.router.add_patch("/api/guilds/{guild_id}/antinuke", api_patch_antinuke)
    app.router.add_post("/api/guilds/{guild_id}/antinuke/whitelist", api_add_antinuke_whitelist)
    app.router.add_delete("/api/guilds/{guild_id}/antinuke/whitelist/{user_id}", api_remove_antinuke_whitelist)
    app.router.add_get("/api/guilds/{guild_id}/antispam", api_get_antispam)
    app.router.add_patch("/api/guilds/{guild_id}/antispam", api_patch_antispam)
    app.router.add_post("/api/guilds/{guild_id}/antispam/ignore/{kind}", api_add_antispam_ignore)
    app.router.add_delete("/api/guilds/{guild_id}/antispam/ignore/{kind}/{target_id}", api_remove_antispam_ignore)
    app.router.add_get("/dashboard", serve_dashboard_shell)
    app.router.add_get("/dashboard/{guild_id}", serve_dashboard_shell)


# ══════════════════════════════════════════════════════════════════
# FRONTEND — single-page shell, vanilla JS, same brand tokens as the
# marketing site (crimson/gold, Big Shoulders + Outfit). Kept as one
# inline template so the whole dashboard ships from this one module.
# ══════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Dashboard — VALLENT EXS</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders:wght@700;900&family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --void:#0a0605; --surface:#130b0c; --surface-2:#1c1112; --surface-3:#241618;
    --line:rgba(245,240,236,0.09); --line-2:rgba(245,240,236,0.16); --line-3:rgba(245,240,236,0.26);
    --crimson:#a80f2c; --crimson-deep:#3d0010; --crimson-glow:rgba(168,15,44,0.35); --crimson-bright:#d81941;
    --gold:#f5a623; --gold-deep:#b3760f; --gold-glow:rgba(245,166,35,0.30);
    --violet:#7c3aed; --violet-glow:rgba(124,58,237,0.28);
    --ink:#f5f0ec; --muted:#a3908d; --muted-2:#6e5c5a;
    --shadow-lg:0 30px 70px -24px rgba(0,0,0,0.7); --shadow-sm:0 10px 26px -14px rgba(0,0,0,0.55);
    --radius-lg:16px; --radius-md:12px; --radius-sm:8px;
    --container:1180px;
  }
  *{ margin:0; padding:0; box-sizing:border-box; }
  html{ scrollbar-color: var(--surface-3) var(--void); scroll-behavior:smooth; }
  body{
    background:var(--void); color:var(--ink); font-family:'Outfit',sans-serif; min-height:100vh;
    position:relative; overflow-x:hidden;
  }
  body::before{
    content:""; position:fixed; inset:0; pointer-events:none; z-index:0;
    background-image: radial-gradient(circle at 12% 4%, var(--crimson-glow), transparent 30%),
                       radial-gradient(circle at 92% 90%, var(--gold-glow), transparent 36%);
    opacity:0.2;
  }
  body::after{
    content:""; position:fixed; inset:0; z-index:0; pointer-events:none; mix-blend-mode:overlay;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
  }
  .bg-grid{
    position:fixed; inset:0; z-index:0; pointer-events:none; opacity:0.4;
    background-image:linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px);
    background-size:64px 64px;
    -webkit-mask-image:radial-gradient(ellipse 80% 50% at 50% 0%, #000 30%, transparent 75%);
    mask-image:radial-gradient(ellipse 80% 50% at 50% 0%, #000 30%, transparent 75%);
  }
  nav, main{ position:relative; z-index:2; }
  ::selection{ background:var(--crimson); color:#fff; }
  ::-webkit-scrollbar{ width:10px; height:10px; }
  ::-webkit-scrollbar-track{ background:var(--void); }
  ::-webkit-scrollbar-thumb{ background:var(--surface-3); border-radius:8px; border:2px solid var(--void); }
  a{ color:inherit; text-decoration:none; }
  a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible{ outline:2px solid var(--gold); outline-offset:2px; border-radius:4px; }
  h1,h2,.display{ font-family:'Big Shoulders',sans-serif; font-weight:900; text-transform:uppercase; letter-spacing:-0.01em; }
  .mono{ font-family:'JetBrains Mono',monospace; }
  .wrap{ max-width:var(--container); margin:0 auto; padding:0 32px; }
  @media (max-width:640px){ .wrap{ padding:0 20px; } }
  .hex{ width:38px; height:39px; clip-path:polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%); display:flex; align-items:center; justify-content:center; background:linear-gradient(155deg,var(--crimson),var(--crimson-deep)); box-shadow:0 0 20px var(--crimson-glow); flex-shrink:0; }
  .hex span{ font-family:'Big Shoulders',sans-serif; font-weight:900; font-size:18px; }
  nav{
    position:sticky; top:0; z-index:50; border-bottom:1px solid var(--line); padding:0;
    background:rgba(10,6,5,0.72); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  }
  nav::after{ content:""; position:absolute; left:0; right:0; bottom:-1px; height:1px;
    background:linear-gradient(90deg, transparent, var(--crimson-glow) 35%, var(--gold-glow) 65%, transparent); opacity:.6; }
  nav .row{ display:flex; align-items:center; justify-content:space-between; height:72px; gap:20px; }
  .brand{ display:flex; align-items:center; gap:12px; font-family:'Big Shoulders',sans-serif; font-weight:700; font-size:18px; letter-spacing:0.02em; }
  .brand b{ color:var(--gold); }
  .brand-site-link{ font-size:12.5px; color:var(--muted); display:flex; align-items:center; gap:6px; transition:color .2s ease; }
  .brand-site-link:hover{ color:var(--ink); }
  @media (max-width:640px){ .brand-site-link{ display:none; } }
  .userchip{ display:flex; align-items:center; gap:10px; font-size:13px; color:var(--muted); }
  .userchip img{ width:28px; height:28px; border-radius:50%; border:1px solid var(--line-2); }
  .btn{ display:inline-flex; align-items:center; gap:8px; padding:10px 20px; font-weight:600; font-size:13px; border-radius:6px; border:none; cursor:pointer;
    position:relative; overflow:hidden; isolation:isolate; transition:transform .18s ease, box-shadow .18s ease, opacity .18s ease; }
  .btn::before{ content:""; position:absolute; inset:0; background:linear-gradient(120deg,transparent 30%,rgba(255,255,255,0.22) 48%,transparent 66%); transform:translateX(-130%); transition:transform .5s ease; z-index:1; pointer-events:none; }
  .btn:hover::before{ transform:translateX(130%); }
  .btn:hover{ transform:translateY(-1px); }
  .btn:disabled{ opacity:0.45; cursor:not-allowed; transform:none; }
  .btn:disabled::before{ display:none; }
  .btn-sm{ padding:8px 16px; font-size:12.5px; }
  .btn-primary{ background:linear-gradient(135deg,var(--crimson-bright) 0%,var(--crimson) 55%,#4a0714 100%); color:#fff; box-shadow:0 10px 26px -12px var(--crimson-glow); }
  .btn-primary:hover{ box-shadow:0 16px 34px -10px var(--crimson-glow); }
  .btn-ghost{ background:rgba(245,240,236,0.03); border:1px solid var(--line); color:var(--ink); }
  .btn-ghost:hover{ border-color:var(--line-2); }
  .btn-gold{ background:linear-gradient(135deg,var(--gold),var(--gold-deep)); color:#160b02; box-shadow:0 10px 24px -12px var(--gold-glow); }
  main{ padding:48px 0 120px; min-height:calc(100vh - 72px); }
  .loading{ text-align:center; padding:120px 0; color:var(--muted-2); font-size:14px; }
  .login-card{ max-width:420px; margin:100px auto; text-align:center; padding:48px 32px; border:1px solid var(--line); border-radius:var(--radius-lg); background:var(--surface); box-shadow:var(--shadow-lg); }
  .login-card h1{ font-size:26px; margin:18px 0 10px; }
  .login-card p{ color:var(--muted); font-size:14px; margin-bottom:28px; line-height:1.6; }

  /* ================= APP SHELL (guild editor: sidebar + content) ================= */
  .app-shell{ display:flex; align-items:flex-start; gap:0; margin:-48px 0 0; min-height:calc(100vh - 120px); }
  .app-sidebar{
    width:272px; flex-shrink:0; position:sticky; top:72px; height:calc(100vh - 72px); overflow-y:auto;
    border-right:1px solid var(--line); padding:44px 20px 40px; background:rgba(19,11,12,0.4); margin-right:40px;
  }
  @media (max-width:900px){
    .app-shell{ flex-direction:column; margin-top:-28px; }
    .app-sidebar{ width:100%; position:relative; top:0; height:auto; border-right:none; border-bottom:1px solid var(--line); padding:20px; margin-right:0; }
    .sidebar-nav{ flex-direction:row !important; overflow-x:auto; gap:6px !important; }
    .sidebar-item{ flex-shrink:0; }
  }
  .sidebar-back{ display:inline-flex; align-items:center; gap:6px; font-size:12.5px; color:var(--muted); margin-bottom:20px; transition:color .2s ease; }
  .sidebar-back:hover{ color:var(--ink); }
  .sidebar-guild-chip{ display:flex; align-items:center; gap:11px; padding:10px; border-radius:10px; background:var(--surface); border:1px solid var(--line); margin-bottom:28px; }
  .sidebar-guild-chip img{ width:32px; height:32px; border-radius:8px; flex-shrink:0; object-fit:cover; }
  .sidebar-guild-chip .no-icon{ width:32px; height:32px; border-radius:8px; background:var(--surface-2); flex-shrink:0; }
  .sidebar-guild-chip .name{ font-size:13.5px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sidebar-label{ font-size:11px; color:var(--muted-2); text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin-bottom:12px; padding:0 4px; }
  .sidebar-nav{ display:flex; flex-direction:column; gap:2px; }
  .sidebar-item{
    display:flex; align-items:center; gap:11px; padding:11px 12px; border-radius:9px; border:none; background:transparent;
    color:var(--muted); font-family:'Outfit',sans-serif; font-size:13.5px; font-weight:500; cursor:pointer; text-align:left; width:100%;
    transition:background .18s ease, color .18s ease;
  }
  .sidebar-item:hover{ background:var(--surface); color:var(--ink); }
  .sidebar-item.active{ background:var(--surface-2); color:var(--ink); box-shadow:inset 2px 0 0 var(--gold); }
  .sidebar-item svg{ width:16px; height:16px; flex-shrink:0; opacity:0.85; }
  .sidebar-item-label{ flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sidebar-dot{ width:6px; height:6px; border-radius:50%; flex-shrink:0; }
  .sidebar-dot.on{ background:#4ade80; }
  .sidebar-dot.off{ background:var(--muted-2); }
  .sidebar-dot.neutral{ background:var(--gold); }
  .sidebar-item.disabled{ opacity:0.4; cursor:not-allowed; }
  .sidebar-item.disabled:hover{ background:transparent; color:var(--muted); }
  .sidebar-divider{ height:1px; background:var(--line); margin:16px 4px; }
  .sidebar-soon{ font-size:9.5px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted-2); background:var(--surface-2); padding:2px 7px; border-radius:100px; flex-shrink:0; }

  .app-content{ flex:1; min-width:0; padding:44px 40px 0; max-width:920px; }
  @media (max-width:900px){ .app-content{ padding:32px 20px 0; max-width:none; } }
  .content-panel{ display:none; }
  .content-panel.active{ display:block; animation:panelFadeIn .35s ease; }
  @keyframes panelFadeIn{ from{ opacity:0; transform:translateY(8px); } to{ opacity:1; transform:translateY(0); } }
  .panel-page-head{ display:flex; align-items:center; gap:16px; margin-bottom:32px; }
  .panel-page-icon{ width:46px; height:46px; border-radius:11px; background:var(--surface-2); border:1px solid var(--line); display:flex; align-items:center; justify-content:center; flex-shrink:0; }
  .panel-page-icon svg{ width:21px; height:21px; }
  .panel-page-head h1{ font-family:'Outfit',sans-serif; text-transform:none; font-weight:700; font-size:22px; letter-spacing:0; margin-bottom:2px; }
  .panel-page-head p{ font-size:13px; color:var(--muted-2); }
  .panel-page-head .status-badge{ margin-left:auto; }
  .guild-grid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:16px; margin-top:28px; }
  .guild-card{ background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-md); padding:20px; cursor:pointer; transition:border-color .2s ease, transform .2s ease, box-shadow .2s ease; display:flex; align-items:center; gap:14px; box-shadow:var(--shadow-sm); }
  .guild-card:hover{ border-color:var(--line-3); transform:translateY(-3px); box-shadow:var(--shadow-lg); }
  .guild-icon{ width:44px; height:44px; border-radius:50%; background:var(--surface-2); flex-shrink:0; object-fit:cover; border:1px solid var(--line-2); }
  .page-title{ font-size:clamp(28px,3.4vw,38px); margin-bottom:8px; }
  .page-sub{ color:var(--muted); font-size:14.5px; margin-bottom:40px; line-height:1.6; max-width:560px; }
  .guild-card.disabled{ opacity:0.45; cursor:not-allowed; }
  .guild-name{ font-size:14px; font-weight:600; }
  .guild-note{ font-size:11px; color:var(--muted-2); margin-top:2px; }
  .status-badge{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; padding:4px 10px; border-radius:20px; flex-shrink:0; }
  .status-badge.on{ background:rgba(74,222,128,0.15); color:#4ade80; }
  .status-badge.off{ background:rgba(255,255,255,0.06); color:var(--muted-2); }

  .panel{ background:var(--surface); border:1px solid var(--line); border-radius:var(--radius-lg); padding:28px; margin-bottom:20px; box-shadow:var(--shadow-sm); }
  .panel-head{ display:flex; align-items:center; justify-content:space-between; margin-bottom:20px; }
  .panel-head h2{ font-family:'Outfit',sans-serif; text-transform:none; font-weight:700; font-size:17px; letter-spacing:0; }
  .field{ margin-bottom:20px; }
  .field label{ display:block; font-size:12.5px; color:var(--muted); text-transform:uppercase; letter-spacing:0.06em; margin-bottom:8px; }
  .field select, .field input[type=number], .field textarea{
    width:100%; background:var(--surface-2); border:1px solid var(--line); border-radius:6px; padding:10px 12px;
    color:var(--ink); font-family:'Outfit',sans-serif; font-size:14px; outline:none;
  }
  .field textarea{ resize:vertical; min-height:64px; line-height:1.5; }
  .field select:focus, .field input:focus, .field textarea:focus{ border-color:var(--crimson); }
  .field-row{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media (max-width:520px){ .field-row{ grid-template-columns:1fr; } }

  /* ---------- Emoji picker ---------- */
  .emoji-field{ display:flex; gap:8px; }
  .emoji-field input{ flex:1; }
  .emoji-pick-btn{ width:44px; height:44px; flex-shrink:0; border-radius:6px; border:1px solid var(--line); background:var(--surface-2); cursor:pointer; font-size:18px; display:flex; align-items:center; justify-content:center; transition:border-color .18s ease; }
  .emoji-pick-btn:hover{ border-color:var(--line-2); }
  .emoji-pick-btn img{ width:20px; height:20px; }
  .emoji-popover{ position:fixed; z-index:300; width:290px; max-height:340px; overflow-y:auto; background:var(--surface); border:1px solid var(--line-2); border-radius:10px; box-shadow:var(--shadow-lg); padding:14px; display:none; }
  .emoji-popover.open{ display:block; }
  .emoji-popover h6{ font-size:10.5px; text-transform:uppercase; letter-spacing:0.07em; color:var(--muted-2); font-weight:600; margin:12px 0 8px; }
  .emoji-popover h6:first-child{ margin-top:0; }
  .emoji-grid{ display:grid; grid-template-columns:repeat(7,1fr); gap:3px; }
  .emoji-grid button{ background:transparent; border:none; border-radius:6px; padding:6px 0; cursor:pointer; font-size:18px; display:flex; align-items:center; justify-content:center; transition:background .15s ease; }
  .emoji-grid button:hover{ background:var(--surface-2); }
  .emoji-grid img{ width:20px; height:20px; }
  .emoji-clear-btn{ font-size:11px; color:var(--muted-2); background:transparent; border:none; cursor:pointer; padding:2px 0; }
  .emoji-clear-btn:hover{ color:var(--ink); }
  .emoji-popover-empty{ font-size:11.5px; color:var(--muted-2); line-height:1.5; }
  .toggle{ position:relative; display:inline-block; width:46px; height:26px; }
  .toggle input{ opacity:0; width:0; height:0; }
  .toggle-slider{ position:absolute; inset:0; background:var(--surface-2); border:1px solid var(--line); border-radius:14px; cursor:pointer; transition:.2s; }
  .toggle-slider::before{ content:""; position:absolute; width:18px; height:18px; left:3px; top:2.5px; background:var(--muted); border-radius:50%; transition:.2s; }
  .toggle input:checked + .toggle-slider{ background:rgba(168,15,44,0.35); border-color:var(--crimson); }
  .toggle input:checked + .toggle-slider::before{ transform:translateX(20px); background:var(--crimson); }
  .save-row{ display:flex; align-items:center; gap:14px; margin-top:24px; }
  .save-status{ font-size:12.5px; color:var(--muted-2); }
  .save-status.ok{ color:#4ade80; }
  .save-status.err{ color:var(--crimson); }
  .back-link{ display:inline-flex; align-items:center; gap:6px; font-size:13px; color:var(--muted); margin-bottom:24px; }
  .back-link:hover{ color:var(--ink); }
  .soon-note{ font-size:12.5px; color:var(--muted-2); margin-top:6px; }
  .field input[type=text], .field input[type=url]{
    width:100%; background:var(--surface-2); border:1px solid var(--line); border-radius:6px; padding:10px 12px;
    color:var(--ink); font-family:'Outfit',sans-serif; font-size:14px; outline:none;
  }
  .field input[type=text]:focus, .field input[type=url]:focus{ border-color:var(--crimson); }
  .field input:disabled, .field select:disabled{ opacity:0.5; cursor:not-allowed; }

  /* ---------- My Rank Card / customization panel ---------- */
  .premium-banner{
    display:flex; align-items:center; gap:14px; padding:16px 18px; border-radius:10px; margin-bottom:22px;
    background:rgba(245,166,35,0.08); border:1px solid rgba(245,166,35,0.28);
  }
  .premium-banner.is-off{ background:rgba(255,255,255,0.03); border-color:var(--line); }
  .premium-banner .pb-icon{ width:34px; height:34px; border-radius:9px; flex-shrink:0; display:flex; align-items:center; justify-content:center; background:rgba(245,166,35,0.15); }
  .premium-banner .pb-icon svg{ width:17px; height:17px; }
  .premium-banner .pb-text{ font-size:13px; color:var(--muted); line-height:1.5; }
  .premium-banner .pb-text b{ color:var(--gold); }
  .premium-banner.is-off .pb-text b{ color:var(--ink); }

  .custom-subhead{ font-size:15px; font-weight:600; margin:30px 0 4px; display:flex; align-items:center; gap:8px; }
  .custom-subhead:first-of-type{ margin-top:6px; }
  .custom-subnote{ font-size:12.5px; color:var(--muted-2); margin-bottom:18px; }

  .color-stops{ display:flex; gap:14px; flex-wrap:wrap; margin-bottom:14px; }
  .color-stop{ display:flex; align-items:center; gap:8px; background:var(--surface-2); border:1px solid var(--line); border-radius:8px; padding:8px 10px; }
  .color-stop input[type=color]{
    width:30px; height:30px; border:none; border-radius:50%; overflow:hidden; padding:0; background:none; cursor:pointer;
  }
  .color-stop input[type=color]::-webkit-color-swatch-wrapper{ padding:0; }
  .color-stop input[type=color]::-webkit-color-swatch{ border:1px solid var(--line-2); border-radius:50%; }
  .color-stop input[type=text]{ width:88px; background:transparent; border:none; color:var(--ink); font-family:'JetBrains Mono',monospace; font-size:12.5px; padding:0; outline:none; }
  .color-stop .stop-remove{ background:transparent; border:none; color:var(--muted-2); cursor:pointer; font-size:15px; line-height:1; padding:0 2px; }
  .color-stop .stop-remove:hover{ color:var(--crimson); }
  .add-stop-btn{ background:transparent; border:1px dashed var(--line-2); color:var(--muted); border-radius:8px; padding:8px 16px; font-size:12.5px; cursor:pointer; }
  .add-stop-btn:hover{ color:var(--ink); border-color:var(--muted); }
  .add-stop-btn:disabled{ opacity:0.35; cursor:not-allowed; }

  .mini-id-card{
    width:100%; max-width:420px; aspect-ratio:934/300; position:relative; border-radius:12px;
    background:linear-gradient(160deg, #170d0e 0%, #2b0a0f 60%, #170d0e 100%);
    border:1px solid rgba(245,166,35,0.24); overflow:hidden; margin-bottom:18px;
    box-shadow:var(--shadow-sm); transition:background-image .3s ease;
  }
  .mini-id-card::before{
    content:"VX"; position:absolute; right:-6%; top:-22%; font-family:'Big Shoulders',sans-serif; font-weight:900;
    font-size:150px; color:rgba(255,255,255,0.035); line-height:1; pointer-events:none;
  }
  .mc-corner{ position:absolute; width:16px; height:16px; border:2px solid var(--gold); opacity:0.85; }
  .mc-corner.tl{ top:8px; left:8px; border-right:none; border-bottom:none; }
  .mc-corner.br{ bottom:8px; right:8px; border-left:none; border-top:none; }
  .mc-body{ position:absolute; inset:0; display:flex; align-items:center; padding:7% 8%; gap:5%; }
  .mc-avatar{
    flex-shrink:0; width:24%; aspect-ratio:1;
    clip-path: polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%);
    background: linear-gradient(145deg, var(--gold), var(--crimson)) border-box;
    padding:3px; display:flex; align-items:center; justify-content:center;
    box-shadow:0 0 24px -6px var(--gold-glow);
  }
  .mc-avatar img{ width:100%; height:100%; object-fit:cover; clip-path: polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%); background:var(--surface-2); }
  .mc-info{ flex:1; min-width:0; }
  .mc-name{ font-family:'Big Shoulders',sans-serif; font-weight:900; font-size:clamp(13px,3vw,21px); color:#fff; letter-spacing:0.01em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .mc-tag{ display:inline-flex; align-items:center; gap:6px; font-size:clamp(7px,1.3vw,10px); color:var(--gold); text-transform:uppercase; letter-spacing:0.1em; font-weight:600; margin:4px 0 8px; }
  .mc-dot{ width:5px; height:5px; background:var(--gold); clip-path: polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%); flex-shrink:0; }
  .mc-bar{ display:flex; gap:2px; }
  .mc-bar i{ flex:1; height:7px; background:rgba(255,255,255,0.08); border-radius:1px; }
  .mc-bar i.on{ background:linear-gradient(90deg, var(--crimson), var(--gold)); }
  .mc-sub{ font-size:clamp(6px,1.1vw,9px); color:var(--muted); margin-top:7px; letter-spacing:0.04em; }

  .bg-preview{ width:100%; aspect-ratio: 934/300; border-radius:10px; border:1px solid var(--line); background:var(--surface-2) center/cover no-repeat; margin-bottom:14px; display:flex; align-items:center; justify-content:center; color:var(--muted-2); font-size:12.5px; overflow:hidden; }

  .custom-divider{ height:1px; background:var(--line); margin:34px 0 6px; }

  /* ---------- Ticket System: tabs + live preview ---------- */
  .tix-tabs{ display:flex; gap:4px; flex-wrap:wrap; margin-bottom:26px; border-bottom:1px solid var(--line); }
  .tix-tab{ background:transparent; border:none; border-bottom:2px solid transparent; color:var(--muted); font-size:13px; font-weight:600;
    padding:11px 14px; cursor:pointer; display:flex; align-items:center; gap:7px; transition:color .2s ease, border-color .2s ease; white-space:nowrap; }
  .tix-tab:hover{ color:var(--ink); }
  .tix-tab.active{ color:var(--ink); border-bottom-color:var(--gold); }
  .tix-tab-new{ color:var(--gold); }
  .tix-tab-new.active{ border-bottom-color:var(--gold); }
  .tix-dot{ width:6px; height:6px; border-radius:50%; flex-shrink:0; }
  .tix-dot.on{ background:#4ade80; }
  .tix-dot.off{ background:var(--muted-2); }

  .tix-pane{ display:none; }
  .tix-pane.active{ display:block; }
  .tix-layout{ display:grid; grid-template-columns:260px 1fr; gap:28px; align-items:start; }
  @media (max-width:760px){ .tix-layout{ grid-template-columns:1fr; } }

  .tix-preview{ position:sticky; top:20px; border-radius:10px; overflow:hidden; background:var(--surface-2); border:1px solid var(--line); box-shadow:var(--shadow-sm); }
  .tix-preview-banner{ height:76px; background:linear-gradient(120deg,var(--crimson-deep),var(--crimson)); background-size:cover; background-position:center; }
  .tix-preview-body{ padding:16px; border-left:3px solid #8B0000; }
  .tix-preview-head{ display:flex; align-items:center; gap:10px; margin-bottom:8px; }
  .tix-preview-thumb{ width:32px; height:32px; border-radius:7px; object-fit:cover; flex-shrink:0; background:var(--surface-3); }
  .tix-preview-title{ font-size:14px; font-weight:700; line-height:1.3; }
  .tix-preview-desc{ font-size:12px; color:var(--muted); line-height:1.55; margin-bottom:14px; white-space:pre-wrap; word-break:break-word; }
  .tix-preview-btn{ display:inline-flex; align-items:center; gap:6px; padding:8px 14px; border-radius:5px; font-size:12.5px; font-weight:600; color:#fff; }
  .tix-preview-caption{ font-size:11px; color:var(--muted-2); text-align:center; padding:8px 0 2px; }

  /* ---------- Checkout ---------- */
  .checkout-panel{ max-width:520px; }
  .order-summary{ border:1px solid var(--line); border-radius:10px; overflow:hidden; margin-bottom:24px; }
  .order-row{ display:flex; align-items:center; justify-content:space-between; padding:12px 16px; font-size:13.5px; border-bottom:1px solid var(--line); background:var(--surface-2); }
  .order-row:last-child{ border-bottom:none; }
  .order-row span{ color:var(--muted); }
  .order-row b{ display:flex; align-items:center; gap:8px; font-weight:600; }
  .order-avatar{ width:22px; height:22px; border-radius:50%; }
  .order-total{ background:rgba(245,166,35,0.07); }
  .order-total b{ color:var(--gold); font-size:15px; }
  .pay-loading{ color:var(--muted-2); font-size:12.5px; }
  .pay-fallback{ font-size:13px; color:var(--muted); line-height:1.6; padding:14px 16px; border:1px dashed var(--line-2); border-radius:8px; }
  .pay-method{ background:var(--surface-2); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin-bottom:10px; }
  .pay-method-name{ font-weight:600; font-size:13.5px; margin-bottom:6px; }
  .pay-method-info{ font-size:12.5px; color:var(--muted); line-height:1.5; }
  .pay-qris-img{ max-width:180px; border-radius:6px; display:block; margin-bottom:8px; }
  .checkout-status{ font-size:12.5px; margin-top:10px; }
  .checkout-status.err{ color:#e0637a; }
  .order-done{ text-align:center; padding:20px 0; }
  .order-done-icon{ width:52px; height:52px; border-radius:50%; background:rgba(245,166,35,0.14); color:var(--gold); font-size:24px; display:flex; align-items:center; justify-content:center; margin:0 auto 16px; }
  .order-done p{ color:var(--muted); font-size:13.5px; line-height:1.6; margin-top:8px; }
</style>
</head>
<body>
<nav><div class="wrap row">
  <div style="display:flex;align-items:center;gap:20px;">
    <a href="/dashboard" class="brand"><div class="hex"><span>V</span></div>VALLENT <b>EXS</b> <span class="mono" style="font-size:11px;color:var(--muted-2);margin-left:4px;">DASHBOARD</span></a>
    <a href="https://vallentexs.web.id" class="brand-site-link">&larr; Back to site</a>
  </div>
  <div id="navRight"></div>
</div></nav>
<main class="wrap" id="app"><div class="loading">Loading…</div></main>

<script>
const app = document.getElementById('app');
const navRight = document.getElementById('navRight');

function el(html){ const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.childNodes.length === 1 ? t.content.firstChild : t.content; }

async function api(path, opts={}) {
  const res = await fetch(path, { credentials: 'same-origin', headers: {'X-Requested-With':'vallent-dashboard','Content-Type':'application/json'}, ...opts });
  return res;
}

// ---------------- Shared emoji picker ----------------
// One popover reused everywhere an emoji field shows up (ticket buttons,
// component buttons, etc) — pulls the actual server's custom emoji list
// (passed in per-call) alongside a curated set of common unicode emoji,
// so people aren't stuck typing/pasting emoji codes by hand.
const COMMON_EMOJIS = ['🎫','💬','✅','❌','⚠️','🔒','🔓','📋','📌','🛠️','💰','🎉','🔗','📢','👋','🙋','❓','📝','🚀','⭐','🔔','📦','🎁','🛡️','⚡','🔥','💡','📎','🗑️','➕'];
let _emojiPopover = null;
let _emojiPopoverTarget = null;

function _ensureEmojiPopover() {
  if (_emojiPopover) return _emojiPopover;
  _emojiPopover = el(`<div class="emoji-popover"></div>`);
  document.body.appendChild(_emojiPopover);
  document.addEventListener('click', (e) => {
    if (_emojiPopover.classList.contains('open') && !_emojiPopover.contains(e.target) && !e.target.closest('.emoji-pick-btn')) {
      _emojiPopover.classList.remove('open');
    }
  });
  window.addEventListener('scroll', () => _emojiPopover.classList.remove('open'), true);
  return _emojiPopover;
}

function attachEmojiPicker(inputEl, customEmojis) {
  const wrap = el(`<div class="emoji-field"></div>`);
  inputEl.parentNode.insertBefore(wrap, inputEl);
  wrap.appendChild(inputEl);
  const pickBtn = el(`<button type="button" class="emoji-pick-btn" title="Pick an emoji">🙂</button>`);
  wrap.appendChild(pickBtn);

  function updatePickBtnFace() {
    const v = (inputEl.value || '').trim();
    const custom = customEmojis.find(e => e.tag === v);
    pickBtn.innerHTML = custom ? `<img src="${custom.url}">` : (v && !v.startsWith('<') ? v : '🙂');
  }
  updatePickBtnFace();
  inputEl.addEventListener('input', updatePickBtnFace);

  pickBtn.onclick = (e) => {
    e.stopPropagation();
    const pop = _ensureEmojiPopover();
    if (pop.classList.contains('open') && _emojiPopoverTarget === inputEl) { pop.classList.remove('open'); return; }
    _emojiPopoverTarget = inputEl;
    pop.innerHTML = '';
    pop.appendChild(el(`<h6>Common</h6>`));
    const commonGrid = el(`<div class="emoji-grid"></div>`);
    COMMON_EMOJIS.forEach(em => {
      const b = el(`<button type="button">${em}</button>`);
      b.onclick = () => { inputEl.value = em; inputEl.dispatchEvent(new Event('input')); pop.classList.remove('open'); };
      commonGrid.appendChild(b);
    });
    pop.appendChild(commonGrid);
    pop.appendChild(el(`<h6>This Server</h6>`));
    if (customEmojis.length) {
      const customGrid = el(`<div class="emoji-grid"></div>`);
      customEmojis.forEach(ce => {
        const b = el(`<button type="button" title=":${ce.name}:"><img src="${ce.url}"></button>`);
        b.onclick = () => { inputEl.value = ce.tag; inputEl.dispatchEvent(new Event('input')); pop.classList.remove('open'); };
        customGrid.appendChild(b);
      });
      pop.appendChild(customGrid);
    } else {
      pop.appendChild(el(`<div class="emoji-popover-empty">No custom emoji on this server yet.</div>`));
    }
    const clearBtn = el(`<button type="button" class="emoji-clear-btn">Clear emoji</button>`);
    clearBtn.onclick = () => { inputEl.value = ''; inputEl.dispatchEvent(new Event('input')); pop.classList.remove('open'); };
    pop.appendChild(clearBtn);

    const r = pickBtn.getBoundingClientRect();
    pop.style.top = Math.min(r.bottom + 6, window.innerHeight - 350) + 'px';
    pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 300)) + 'px';
    pop.classList.add('open');
  };
}

function renderNav(me) {
  navRight.innerHTML = '';
  if (!me.logged_in) return;
  const chip = el(`<div style="display:flex;align-items:center;gap:16px;">
    <div class="userchip">${me.avatar ? `<img src="${me.avatar}">` : ''}<span>${me.username}</span></div>
    <a href="/auth/discord/logout" class="btn btn-ghost">Log Out</a>
  </div>`);
  navRight.appendChild(chip);
}

function renderLogin() {
  app.innerHTML = '';
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  app.appendChild(el(`
    <div class="login-card">
      <div class="hex" style="margin:0 auto;"><span>V</span></div>
      <h1>Sign in to manage<br>your server</h1>
      <p>Log in with Discord to configure VALLENT EXS on any server where you have Manage Server permission.</p>
      <a href="/auth/discord/login?next=${next}" class="btn btn-primary" style="width:100%;justify-content:center;">Continue with Discord</a>
    </div>
  `));
}

const PRODUCT_LABELS = { premium: 'Premium', noprefix: 'No-Prefix Only', badge: 'Custom Profile Badge' };

async function renderCheckout(me) {
  app.innerHTML = '';
  const q = new URLSearchParams(window.location.search);
  const product = (q.get('product') || '').toLowerCase();
  const planId = q.get('plan') || '';
  const planLabel = q.get('label') || '';
  const price = q.get('price') || '';

  if (!PRODUCT_LABELS[product] || !planId || !planLabel || !price) {
    app.appendChild(el(`
      <a class="back-link" href="https://vallentexs.web.id/pricing.html">&larr; Back to Pricing</a>
      <div class="panel"><h2>Order not found</h2><p style="color:var(--muted);margin-top:8px;">This checkout link is missing some details. Head back to the pricing page and pick a plan again.</p></div>
    `));
    return;
  }

  app.appendChild(el(`<a class="back-link" href="https://vallentexs.web.id/pricing.html">&larr; Back to Pricing</a>`));
  app.appendChild(el(`<h1 class="page-title">Checkout</h1><p class="page-sub">Confirm your order below. Payment is verified manually right now — you'll be notified in Discord once Premium is activated.</p>`));

  const card = el(`<div class="panel checkout-panel"></div>`);
  card.appendChild(el(`
    <div class="order-summary">
      <div class="order-row"><span>Product</span><b>${PRODUCT_LABELS[product]}</b></div>
      <div class="order-row"><span>Plan</span><b>${planLabel}</b></div>
      <div class="order-row"><span>Buying as</span><b>${me.avatar ? `<img class="order-avatar" src="${me.avatar}">` : ''}${me.username}</b></div>
      <div class="order-row order-total"><span>Total</span><b>$${price}</b></div>
    </div>
  `));

  const payBox = el(`<div class="pay-methods"><div class="pay-loading">Loading payment options...</div></div>`);
  card.appendChild(payBox);

  const statusEl = el(`<div class="checkout-status"></div>`);
  const submitBtn = el(`<button class="btn btn-primary btn-block">I've Paid — Submit Order</button>`);
  const submitWrap = el(`<div style="margin-top:22px;"></div>`);
  submitWrap.appendChild(submitBtn);
  submitWrap.appendChild(statusEl);
  card.appendChild(submitWrap);

  submitBtn.onclick = async () => {
    submitBtn.disabled = true;
    statusEl.textContent = 'Submitting...';
    statusEl.className = 'checkout-status';
    const res = await api('/api/premium/order', {
      method: 'POST',
      body: JSON.stringify({ product, plan_id: planId, plan_label: planLabel, price }),
    });
    const data = await res.json();
    if (res.ok) {
      card.innerHTML = '';
      card.appendChild(el(`
        <div class="order-done">
          <div class="order-done-icon">✓</div>
          <h2>Order submitted</h2>
          <p>Order <span class="mono">#${data.order_id}</span> for <b>${PRODUCT_LABELS[product]} — ${planLabel}</b> is in. We'll verify your payment and activate it on your account, then follow up in your Discord DMs.</p>
          <a href="https://discord.gg/ahHan43mqe" class="btn btn-ghost" style="margin-top:16px;">Join Support Server</a>
        </div>
      `));
    } else {
      statusEl.textContent = data.error || 'Something went wrong — please try again or reach out on the support server.';
      statusEl.className = 'checkout-status err';
      submitBtn.disabled = false;
    }
  };

  app.appendChild(card);

  const pmRes = await api('/api/payment-methods');
  payBox.innerHTML = '';
  if (!pmRes.ok) {
    payBox.appendChild(el(`<p style="color:var(--muted);font-size:13px;">Couldn't load payment options — you can still submit your order and we'll follow up in Discord.</p>`));
    return;
  }
  const pm = await pmRes.json();
  const active = Object.entries(pm).filter(([, v]) => v && v.enabled);
  if (active.length === 0) {
    payBox.appendChild(el(`<div class="pay-fallback">Payment options aren't set up on the site yet — submit your order below and reach out on the <a href="https://discord.gg/ahHan43mqe" style="color:var(--gold);">support server</a> to arrange payment directly.</div>`));
    return;
  }
  payBox.appendChild(el(`<div class="custom-subhead" style="margin-top:0;">Pay with</div>`));
  active.forEach(([key, v]) => {
    if (key === 'qris') {
      payBox.appendChild(el(`
        <div class="pay-method">
          <div class="pay-method-name">QRIS</div>
          ${v.image_url ? `<img class="pay-qris-img" src="${v.image_url}">` : ''}
          ${v.info ? `<p class="pay-method-info">${v.info}</p>` : ''}
        </div>
      `));
    } else if (key === 'bank') {
      payBox.appendChild(el(`
        <div class="pay-method">
          <div class="pay-method-name">Bank Transfer</div>
          <p class="pay-method-info">${v.bank_name || ''} &middot; <span class="mono">${v.account_number || ''}</span> &middot; a.n. ${v.account_name || ''}</p>
        </div>
      `));
    } else if (key === 'ewallet') {
      payBox.appendChild(el(`
        <div class="pay-method">
          <div class="pay-method-name">${v.type || 'E-Wallet'}</div>
          <p class="pay-method-info"><span class="mono">${v.number || ''}</span></p>
        </div>
      `));
    }
  });
}

async function renderGuildPicker(me) {
  app.innerHTML = '';
  app.appendChild(await buildCustomizationCard(me));
  app.appendChild(el(`<h1 class="page-title">Your Servers</h1><p class="page-sub">Pick a server to configure. Only servers where VALLENT EXS is present and you have Manage Server show as available.</p>`));
  const grid = el('<div class="guild-grid"></div>');
  me.guilds.forEach(g => {
    const card = el(`
      <div class="guild-card ${g.bot_present ? '' : 'disabled'}">
        ${g.icon ? `<img class="guild-icon" src="${g.icon}">` : `<div class="guild-icon"></div>`}
        <div><div class="guild-name">${g.name}</div><div class="guild-note">${g.bot_present ? 'Manage settings' : 'Bot not in this server'}</div></div>
      </div>
    `);
    if (g.bot_present) card.onclick = () => { window.location.href = '/dashboard/' + g.id; };
    grid.appendChild(card);
  });
  app.appendChild(grid);
}

// ---------------- My Rank Card / Profile customization ----------------
// Personal, not per-server — mirrors the bot's own `rankcolor`/`rankbg`
// (rank card + level-up card) and `idcardcolor`/`idcardbg` (profile ID
// card, kept separate) commands.

function defaultStops(existing) {
  if (existing && existing.length >= 2) return existing.slice(0, 3).map(c => c.replace('#', ''));
  return ['A672FF', '20DCD2'];
}

function buildGradientEditor(me, label, subnote, existingColors, existingBg, saveColorPath, saveBgPath, isPremium) {
  const wrap = el(`<div></div>`);
  wrap.appendChild(el(`<div class="custom-subhead">${label}</div><div class="custom-subnote">${subnote}</div>`));

  // ---- gradient ----
  let stops = defaultStops(existingColors);
  const stopsBox = el(`<div class="color-stops"></div>`);
  const addBtn = el(`<button type="button" class="add-stop-btn">+ Add a 3rd color</button>`);

  // ---- live mini card preview, synced to the real logged-in user ----
  const avatarHtml = me.avatar ? `<img src="${me.avatar}">` : '';
  const miniCard = el(`
    <div class="mini-id-card">
      <div class="mc-corner tl"></div>
      <div class="mc-corner br"></div>
      <div class="mc-body">
        <div class="mc-avatar">${avatarHtml}</div>
        <div class="mc-info">
          <div class="mc-name">${(me.username || 'YOU').toUpperCase()}</div>
          <div class="mc-tag"><span class="mc-dot"></span>${isPremium ? 'Premium Member' : 'Preview'}</div>
          <div class="mc-bar">${Array.from({length: 15}, (_, i) => `<i class="${i < 8 ? 'on' : ''}"></i>`).join('')}</div>
          <div class="mc-sub">LIVE PREVIEW &middot; ${label.toUpperCase()}</div>
        </div>
      </div>
    </div>
  `);
  function updateMiniGradient() {
    const grad = `linear-gradient(90deg, ${stops.map(s => '#' + s).join(', ')})`;
    miniCard.querySelectorAll('.mc-bar i.on').forEach(i => { i.style.background = grad; });
    miniCard.querySelector('.mc-avatar').style.background = `linear-gradient(145deg, ${stops.map(s => '#' + s).join(', ')}) border-box`;
    miniCard.querySelector('.mc-dot').style.background = '#' + stops[stops.length - 1];
  }
  function updateMiniBackground(url) {
    miniCard.style.backgroundImage = url ? `linear-gradient(160deg, rgba(10,6,5,0.55), rgba(10,6,5,0.75)), url('${url}')` : '';
    miniCard.style.backgroundSize = 'cover';
    miniCard.style.backgroundPosition = 'center';
  }
  if (existingBg) updateMiniBackground(existingBg);

  function updateBar() {
    addBtn.disabled = stops.length >= 3 || !isPremium;
    updateMiniGradient();
  }

  function renderStops() {
    stopsBox.innerHTML = '';
    stops.forEach((hex, idx) => {
      const row = el(`
        <div class="color-stop">
          <input type="color" value="#${hex}" ${isPremium ? '' : 'disabled'}>
          <input type="text" value="${hex}" maxlength="7" ${isPremium ? '' : 'disabled'}>
          ${idx === 2 ? `<button type="button" class="stop-remove" title="Remove this color">&times;</button>` : ''}
        </div>
      `);
      const colorInput = row.querySelector('input[type=color]');
      const textInput = row.querySelector('input[type=text]');
      colorInput.oninput = () => { const v = colorInput.value.replace('#','').toUpperCase(); textInput.value = v; stops[idx] = v; updateBar(); };
      textInput.oninput = () => {
        let v = textInput.value.replace('#','').trim();
        if (/^[0-9A-Fa-f]{6}$/.test(v)) { colorInput.value = '#' + v; stops[idx] = v.toUpperCase(); updateBar(); }
      };
      const rm = row.querySelector('.stop-remove');
      if (rm) rm.onclick = () => { stops = stops.slice(0, 2); renderStops(); updateBar(); };
      stopsBox.appendChild(row);
    });
  }
  renderStops(); updateBar();

  addBtn.onclick = () => { if (stops.length < 3) { stops.push('F5A623'); renderStops(); updateBar(); } };

  const gradStatus = el(`<span class="save-status"></span>`);
  const saveGradBtn = el(`<button class="btn btn-primary btn-sm" ${isPremium ? '' : 'disabled'}>Save Gradient</button>`);
  const removeGradBtn = el(`<button class="btn btn-ghost btn-sm" ${isPremium ? '' : 'disabled'}>Remove</button>`);
  saveGradBtn.onclick = async () => {
    gradStatus.textContent = 'Saving...'; gradStatus.className = 'save-status';
    const res = await api(saveColorPath, { method: 'PATCH', body: JSON.stringify({ colors: stops.map(s => '#' + s) }) });
    const data = await res.json();
    if (res.ok) { gradStatus.textContent = 'Saved.'; gradStatus.className = 'save-status ok'; }
    else { gradStatus.textContent = data.error || 'Failed to save.'; gradStatus.className = 'save-status err'; }
  };
  removeGradBtn.onclick = async () => {
    gradStatus.textContent = 'Removing...'; gradStatus.className = 'save-status';
    const res = await api(saveColorPath, { method: 'PATCH', body: JSON.stringify({ colors: null }) });
    if (res.ok) {
      gradStatus.textContent = 'Removed — back to default gold.'; gradStatus.className = 'save-status ok';
      stops = defaultStops(null); renderStops(); updateBar();
    }
    else { const data = await res.json(); gradStatus.textContent = data.error || 'Failed to remove.'; gradStatus.className = 'save-status err'; }
  };

  wrap.appendChild(miniCard);
  wrap.appendChild(stopsBox);
  wrap.appendChild(addBtn);
  wrap.appendChild(el(`<div class="save-row">${''}</div>`));
  const gradRow = wrap.querySelector('.save-row');
  gradRow.appendChild(saveGradBtn); gradRow.appendChild(removeGradBtn); gradRow.appendChild(gradStatus);

  // ---- background ----
  wrap.appendChild(el(`<div class="custom-subhead" style="margin-top:26px;font-size:13.5px;">Custom Background</div>`));
  const bgInput = el(`<input type="url" placeholder="https://example.com/background.png" value="${existingBg || ''}" ${isPremium ? '' : 'disabled'}>`);
  const bgStatus = el(`<span class="save-status"></span>`);
  const saveBgBtn = el(`<button class="btn btn-primary btn-sm" ${isPremium ? '' : 'disabled'}>Save Background</button>`);
  const removeBgBtn = el(`<button class="btn btn-ghost btn-sm" ${isPremium ? '' : 'disabled'}>Remove</button>`);
  saveBgBtn.onclick = async () => {
    bgStatus.textContent = 'Saving...'; bgStatus.className = 'save-status';
    const res = await api(saveBgPath, { method: 'PATCH', body: JSON.stringify({ url: bgInput.value.trim() }) });
    const data = await res.json();
    if (res.ok) {
      bgStatus.textContent = 'Saved.'; bgStatus.className = 'save-status ok';
      updateMiniBackground(bgInput.value.trim());
    } else { bgStatus.textContent = data.error || 'Failed to save.'; bgStatus.className = 'save-status err'; }
  };
  removeBgBtn.onclick = async () => {
    bgStatus.textContent = 'Removing...'; bgStatus.className = 'save-status';
    const res = await api(saveBgPath, { method: 'PATCH', body: JSON.stringify({ url: null }) });
    if (res.ok) { bgStatus.textContent = 'Removed.'; bgStatus.className = 'save-status ok'; bgInput.value = ''; updateMiniBackground(''); }
    else { const data = await res.json(); bgStatus.textContent = data.error || 'Failed to remove.'; bgStatus.className = 'save-status err'; }
  };
  wrap.appendChild(el(`<div class="field" style="margin-bottom:10px;"></div>`)).appendChild(bgInput);
  wrap.appendChild(el(`<div class="save-row">${''}</div>`));
  const bgRow = wrap.lastChild;
  bgRow.appendChild(saveBgBtn); bgRow.appendChild(removeBgBtn); bgRow.appendChild(bgStatus);

  return wrap;
}

async function buildCustomizationCard(me) {
  const res = await api('/api/me/customization');
  if (!res.ok) return el('<div></div>');
  const c = await res.json();

  const card = el(`<div class="panel"></div>`);
  card.appendChild(el(`<div class="panel-head"><h2>My Rank Card &amp; Profile</h2></div>`));
  card.appendChild(el(
    c.is_premium
      ? `<div class="premium-banner"><div class="pb-icon"><svg viewBox="0 0 24 24" fill="none" stroke="#f5a623" stroke-width="1.8"><path d="M12 2l2.4 7.2H22l-6 4.6 2.3 7.2L12 16.4 5.7 21l2.3-7.2-6-4.6h7.6z"/></svg></div><div class="pb-text"><b>Premium active</b> — customize the gradient and background on your rank card, level-up card, and profile ID card below. The preview updates live and reflects your own Discord name &amp; avatar.</div></div>`
      : `<div class="premium-banner is-off"><div class="pb-icon"><svg viewBox="0 0 24 24" fill="none" stroke="#a3908d" stroke-width="1.8"><path d="M12 2l2.4 7.2H22l-6 4.6 2.3 7.2L12 16.4 5.7 21l2.3-7.2-6-4.6h7.6z"/></svg></div><div class="pb-text">Custom gradients and backgrounds are a <b>Premium</b> perk. <a href="https://vallentexs.web.id/pricing.html" style="color:var(--gold);">Get Premium</a> to unlock these.</div></div>`
  ));

  card.appendChild(buildGradientEditor(me, 'Rank Card & Level-Up Card', 'Applies to your `rank` card and level-up announcement card.', c.rank_colors, c.rank_background, '/api/me/rankcolor', '/api/me/rankbg', c.is_premium));
  card.appendChild(el(`<div class="custom-divider"></div>`));
  card.appendChild(buildGradientEditor(me, 'Profile ID Card', 'Applies to your `profile` ID card only — kept separate so it can look different from your rank card.', c.profile_colors, c.profile_background, '/api/me/idcardcolor', '/api/me/idcardbg', c.is_premium));

  return card;
}

function badgeHtml(enabled) {
  if (enabled === null) return `<span class="status-badge" data-badge style="background:rgba(245,166,35,0.15);color:var(--gold);">Always Active</span>`;
  if (typeof enabled === 'string') return `<span class="status-badge" data-badge style="background:var(--surface-2);color:var(--muted);">${enabled}</span>`;
  return `<span class="status-badge ${enabled ? 'on' : 'off'}" data-badge>${enabled ? 'Enabled' : 'Disabled'}</span>`;
}

function setBadge(card, enabled) {
  const badge = card.querySelector('[data-badge]');
  badge.textContent = enabled ? 'Enabled' : 'Disabled';
  badge.className = `status-badge ${enabled ? 'on' : 'off'}`;
}

async function renderGuildEditor(guildId, me) {
  app.innerHTML = '<div class="loading">Loading server settings…</div>';
  const [lvlRes, chRes, rolesRes, catRes, anRes, asRes, tixRes, compRes, emojiRes] = await Promise.all([
    api(`/api/guilds/${guildId}/leveling`),
    api(`/api/guilds/${guildId}/channels`),
    api(`/api/guilds/${guildId}/roles`),
    api(`/api/guilds/${guildId}/categories`),
    api(`/api/guilds/${guildId}/antinuke`),
    api(`/api/guilds/${guildId}/antispam`),
    api(`/api/guilds/${guildId}/tickets`),
    api(`/api/guilds/${guildId}/components`),
    api(`/api/guilds/${guildId}/emojis`),
  ]);
  if (lvlRes.status === 403 || lvlRes.status === 404) {
    app.innerHTML = `<div class="loading">You don't have access to manage this server.</div>`;
    return;
  }
  const lvl = await lvlRes.json();
  const channels = await chRes.json();
  const roles = await rolesRes.json();
  const categories = await catRes.json();
  const an = await anRes.json();
  const as_ = await asRes.json();
  const tix = await tixRes.json();
  const comps = await compRes.json();
  const guildEmojis = emojiRes.ok ? await emojiRes.json() : [];
  const guildMeta = (me && me.guilds || []).find(g => g.id === guildId);

  app.innerHTML = '';
  const shell = el(`<div class="app-shell"></div>`);
  const sidebar = el(`
    <aside class="app-sidebar">
      <a href="/dashboard" class="sidebar-back">&larr; All Servers</a>
      <div class="sidebar-guild-chip">
        ${guildMeta && guildMeta.icon ? `<img src="${guildMeta.icon}">` : `<div class="no-icon"></div>`}
        <div class="name">${guildMeta ? guildMeta.name : 'Server Settings'}</div>
      </div>
      <div class="sidebar-label">Systems</div>
      <nav class="sidebar-nav" id="sidebarNav"></nav>
    </aside>
  `);
  const content = el(`<main class="app-content" id="appContent"></main>`);
  shell.appendChild(sidebar);
  shell.appendChild(content);
  app.appendChild(shell);
  const sidebarNav = sidebar.querySelector('#sidebarNav');

  function addSidebarItem(key, iconSvg, label, dotClass) {
    const btn = el(`
      <button class="sidebar-item" data-target="${key}">
        ${iconSvg}<span class="sidebar-item-label">${label}</span><span class="sidebar-dot ${dotClass}"></span>
      </button>
    `);
    btn.onclick = () => showSystem(key);
    sidebarNav.appendChild(btn);
    return btn;
  }
  function addSidebarComingSoon(iconSvg, label) {
    sidebarNav.appendChild(el(`
      <div class="sidebar-item disabled">${iconSvg}<span class="sidebar-item-label">${label}</span><span class="sidebar-soon">Soon</span></div>
    `));
  }
  function showSystem(key) {
    sidebarNav.querySelectorAll('.sidebar-item').forEach(b => b.classList.toggle('active', b.getAttribute('data-target') === key));
    content.querySelectorAll('.content-panel').forEach(p => p.classList.toggle('active', p.getAttribute('data-panel') === key));
  }
  function makePanelPage(key, icon, title, subtitle, enabled, bodyHtml) {
    const pane = el(`
      <div class="content-panel" data-panel="${key}">
        <div class="panel-page-head">
          <div class="panel-page-icon">${icon}</div>
          <div><h1>${title}</h1><p>${subtitle}</p></div>
          ${badgeHtml(enabled)}
        </div>
        <div class="panel">${bodyHtml}</div>
      </div>
    `);
    content.appendChild(pane);
    return pane;
  }

  // ---------------- Level & XP ----------------
  const roleOptions = (selectedId) => roles.map(r => `<option value="${r.id}" ${selectedId === r.id ? 'selected' : ''}>@${r.name}</option>`).join('');

  const lvlCard = makePanelPage('leveling', '<svg viewBox="0 0 24 24" fill="none" stroke="#f5a623" stroke-width="1.6"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>', 'Level &amp; XP', 'XP gain, level-up announcements, difficulty', lvl.enabled, `
    <div class="field">
      <label>Enabled</label>
      <label class="toggle"><input type="checkbox" id="lvlEnabled" ${lvl.enabled ? 'checked' : ''}><span class="toggle-slider"></span></label>
    </div>
    <div class="field">
      <label>Level-Up Announcement Channel</label>
      <select id="lvlChannel">
        <option value="">— None (announce in the channel it happened) —</option>
        ${channels.map(c => `<option value="${c.id}" ${lvl.channel_id === c.id ? 'selected' : ''}>#${c.name}</option>`).join('')}
      </select>
    </div>
    <div class="field-row">
      <div class="field"><label>Min XP per Message</label><input type="number" id="lvlXpMin" min="1" max="1000" value="${lvl.xp_min}"></div>
      <div class="field"><label>Max XP per Message</label><input type="number" id="lvlXpMax" min="1" max="1000" value="${lvl.xp_max}"></div>
    </div>
    <div class="field-row">
      <div class="field"><label>XP Cooldown (seconds, 0–3600)</label><input type="number" id="lvlCooldown" min="0" max="3600" value="${lvl.cooldown}"></div>
      <div class="field"><label>Difficulty Multiplier (0.1 – 10)</label><input type="number" id="lvlDifficulty" min="0.1" max="10" step="0.1" value="${lvl.difficulty}"></div>
    </div>
    <div class="field">
      <label>Level-Up Message</label>
      <textarea id="lvlMessage" rows="3">${lvl.message}</textarea>
      <div class="soon-note">Placeholders: <span class="mono">{mention}</span> <span class="mono">{user}</span> <span class="mono">{level}</span> <span class="mono">{server}</span> <span class="mono">{roles}</span> — <span class="mono">{roles}</span> is blank when no role reward was earned.</div>
    </div>
    <div class="save-row">
      <button class="btn btn-primary" id="saveLvl">Save Changes</button>
      <span class="save-status" id="lvlStatus"></span>
    </div>

    <div class="field" style="margin-top:28px;">
      <label>No-XP Roles (members with these never gain XP or level up)</label>
      <div id="noxpList" style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px;"></div>
      <div style="display:flex;gap:8px;">
        <select id="noxpRole" style="flex:1;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'Outfit',sans-serif;font-size:14px;">${roles.length ? roleOptions() : '<option value="">No roles found</option>'}</select>
        <button class="btn btn-ghost" id="noxpAdd">Add</button>
      </div>
      <span class="save-status" id="noxpStatus"></span>
    </div>

    <div class="field" style="margin-top:28px;">
      <label>Level Role Rewards (auto-grant a role at a level)</label>
      <div id="lvlRoleList" style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px;"></div>
      <div style="display:flex;gap:8px;">
        <input type="number" id="lvlRewardLevel" placeholder="Level" min="1" style="width:90px;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'Outfit',sans-serif;font-size:14px;">
        <select id="lvlRewardRole" style="flex:1;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'Outfit',sans-serif;font-size:14px;">${roles.length ? roleOptions() : '<option value="">No roles found</option>'}</select>
        <button class="btn btn-ghost" id="lvlRewardAdd">Add</button>
      </div>
      <span class="save-status" id="lvlRewardStatus"></span>
    </div>
  `);

  function renderNoxpList(list) {
    const box = lvlCard.querySelector('#noxpList');
    box.innerHTML = '';
    if (!list.length) { box.appendChild(el(`<div class="soon-note">No no-XP roles set — everyone gains XP normally.</div>`)); return; }
    list.forEach(r => {
      const row = el(`
        <div style="display:flex;align-items:center;gap:10px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;">
          <span style="flex:1;font-size:13.5px;">@${r.name}</span>
          <button data-rid="${r.id}" style="background:transparent;border:none;color:var(--muted-2);cursor:pointer;font-size:16px;">&times;</button>
        </div>
      `);
      row.querySelector('button').onclick = async (e) => {
        e.stopPropagation();
        const rid = e.target.getAttribute('data-rid');
        const res = await api(`/api/guilds/${guildId}/leveling/noxp/${rid}`, { method: 'DELETE' });
        if (res.ok) { const data = await res.json(); renderNoxpList(data.noxp_roles); }
      };
      box.appendChild(row);
    });
  }
  renderNoxpList(lvl.noxp_roles);

  lvlCard.querySelector('#noxpAdd').onclick = async (e) => {
    e.stopPropagation();
    const select = lvlCard.querySelector('#noxpRole');
    const status = lvlCard.querySelector('#noxpStatus');
    if (!select.value) return;
    status.textContent = 'Adding...'; status.className = 'save-status';
    const res = await api(`/api/guilds/${guildId}/leveling/noxp`, { method: 'POST', body: JSON.stringify({ id: select.value }) });
    const data = await res.json();
    if (res.ok) { status.textContent = ''; renderNoxpList(data.noxp_roles); }
    else { status.textContent = data.error || 'Failed to add.'; status.className = 'save-status err'; }
  };

  function renderLvlRoleList(list) {
    const box = lvlCard.querySelector('#lvlRoleList');
    box.innerHTML = '';
    if (!list.length) { box.appendChild(el(`<div class="soon-note">No role rewards set yet.</div>`)); return; }
    list.forEach(r => {
      const row = el(`
        <div style="display:flex;align-items:center;gap:10px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;">
          <span style="flex:1;font-size:13.5px;">Level <b>${r.level}</b> &rarr; @${r.role_name}</span>
          <button data-lvl="${r.level}" style="background:transparent;border:none;color:var(--muted-2);cursor:pointer;font-size:16px;">&times;</button>
        </div>
      `);
      row.querySelector('button').onclick = async (e) => {
        e.stopPropagation();
        const level = e.target.getAttribute('data-lvl');
        const res = await api(`/api/guilds/${guildId}/leveling/role-reward/${level}`, { method: 'DELETE' });
        if (res.ok) { const data = await res.json(); renderLvlRoleList(data.level_roles); }
      };
      box.appendChild(row);
    });
  }
  renderLvlRoleList(lvl.level_roles);

  lvlCard.querySelector('#lvlRewardAdd').onclick = async (e) => {
    e.stopPropagation();
    const levelInput = lvlCard.querySelector('#lvlRewardLevel');
    const roleSelect = lvlCard.querySelector('#lvlRewardRole');
    const status = lvlCard.querySelector('#lvlRewardStatus');
    const level = parseInt(levelInput.value, 10);
    if (!level || level < 1 || !roleSelect.value) { status.textContent = 'Pick a level and a role.'; status.className = 'save-status err'; return; }
    status.textContent = 'Adding...'; status.className = 'save-status';
    const res = await api(`/api/guilds/${guildId}/leveling/role-reward`, { method: 'POST', body: JSON.stringify({ level, role_id: roleSelect.value }) });
    const data = await res.json();
    if (res.ok) { status.textContent = ''; levelInput.value = ''; renderLvlRoleList(data.level_roles); }
    else { status.textContent = data.error || 'Failed to add.'; status.className = 'save-status err'; }
  };

  lvlCard.querySelector('#saveLvl').onclick = async (e) => {
    e.stopPropagation();
    const status = document.getElementById('lvlStatus');
    status.textContent = 'Saving...'; status.className = 'save-status';
    const body = {
      enabled: document.getElementById('lvlEnabled').checked,
      channel_id: document.getElementById('lvlChannel').value || null,
      xp_min: parseInt(document.getElementById('lvlXpMin').value, 10),
      xp_max: parseInt(document.getElementById('lvlXpMax').value, 10),
      cooldown: parseInt(document.getElementById('lvlCooldown').value, 10),
      difficulty: parseFloat(document.getElementById('lvlDifficulty').value),
      message: document.getElementById('lvlMessage').value,
    };
    const res = await api(`/api/guilds/${guildId}/leveling`, { method: 'PATCH', body: JSON.stringify(body) });
    if (res.ok) { status.textContent = 'Saved.'; status.className = 'save-status ok'; setBadge(lvlCard, body.enabled); }
    else { const data = await res.json(); status.textContent = data.error || 'Failed to save — try again.'; status.className = 'save-status err'; }
  };

  // ---------------- Anti-Nuke ----------------
  const anCard = makePanelPage('antinuke', '<svg viewBox="0 0 24 24" fill="none" stroke="#a80f2c" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>', 'Anti-Nuke', 'Raid protection, mass-action detection', an.enabled, `
    <div class="field">
      <label>Enabled</label>
      <label class="toggle"><input type="checkbox" id="anEnabled" ${an.enabled ? 'checked' : ''}><span class="toggle-slider"></span></label>
    </div>
    ${!an.bot_has_audit_log_perm ? `<div class="soon-note" style="color:var(--crimson);margin-bottom:16px;">The bot is missing the "View Audit Log" permission — Anti-Nuke can't detect anything until that's granted in Discord's server settings.</div>` : ''}
    <div class="field">
      <label>Alert Log Channel</label>
      <select id="anLogChannel">
        <option value="">— None —</option>
        ${channels.map(c => `<option value="${c.id}" ${an.log_channel === c.id ? 'selected' : ''}>#${c.name}</option>`).join('')}
      </select>
    </div>
    <div class="field">
      <label>Punishment</label>
      <select id="anPunishment">
        <option value="strip_roles" ${an.punishment === 'strip_roles' ? 'selected' : ''}>Strip Roles</option>
        <option value="kick" ${an.punishment === 'kick' ? 'selected' : ''}>Kick</option>
        <option value="ban" ${an.punishment === 'ban' ? 'selected' : ''}>Ban</option>
      </select>
    </div>
    <div class="save-row">
      <button class="btn btn-primary" id="saveAn">Save Changes</button>
      <span class="save-status" id="anStatus"></span>
    </div>
    <div class="field" style="margin-top:28px;">
      <label>Whitelist (trusted staff exempt from detection)</label>
      <div id="wlList" style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px;"></div>
      <div style="display:flex;gap:8px;">
        <input type="text" id="wlUserId" placeholder="Discord User ID" style="flex:1;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'Outfit',sans-serif;font-size:14px;">
        <button class="btn btn-ghost" id="wlAdd">Add</button>
      </div>
      <span class="save-status" id="wlStatus"></span>
    </div>
  `);

  function renderWhitelist(list) {
    const wlList = anCard.querySelector('#wlList');
    wlList.innerHTML = '';
    if (!list.length) { wlList.appendChild(el(`<div class="soon-note">No one whitelisted yet.</div>`)); return; }
    list.forEach(u => {
      const row = el(`
        <div style="display:flex;align-items:center;gap:10px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;">
          ${u.avatar ? `<img src="${u.avatar}" style="width:24px;height:24px;border-radius:50%;">` : `<div style="width:24px;height:24px;border-radius:50%;background:var(--surface);"></div>`}
          <span style="flex:1;font-size:13.5px;">${u.name}</span>
          <button data-uid="${u.id}" style="background:transparent;border:none;color:var(--muted-2);cursor:pointer;font-size:16px;">&times;</button>
        </div>
      `);
      row.querySelector('button').onclick = async (e) => {
        e.stopPropagation();
        const uid = e.target.getAttribute('data-uid');
        const res = await api(`/api/guilds/${guildId}/antinuke/whitelist/${uid}`, { method: 'DELETE' });
        if (res.ok) { const data = await res.json(); renderWhitelist(data.whitelist); }
      };
      wlList.appendChild(row);
    });
  }
  renderWhitelist(an.whitelist);

  anCard.querySelector('#wlAdd').onclick = async (e) => {
    e.stopPropagation();
    const input = anCard.querySelector('#wlUserId');
    const status = anCard.querySelector('#wlStatus');
    const uid = input.value.trim();
    if (!uid) return;
    status.textContent = 'Adding...'; status.className = 'save-status';
    const res = await api(`/api/guilds/${guildId}/antinuke/whitelist`, { method: 'POST', body: JSON.stringify({ user_id: uid }) });
    const data = await res.json();
    if (res.ok) { status.textContent = ''; input.value = ''; renderWhitelist(data.whitelist); }
    else { status.textContent = data.error || 'Failed to add.'; status.className = 'save-status err'; }
  };

  anCard.querySelector('#saveAn').onclick = async (e) => {
    e.stopPropagation();
    const status = anCard.querySelector('#anStatus');
    status.textContent = 'Saving...'; status.className = 'save-status';
    const body = {
      enabled: anCard.querySelector('#anEnabled').checked,
      log_channel: anCard.querySelector('#anLogChannel').value || null,
      punishment: anCard.querySelector('#anPunishment').value,
    };
    const res = await api(`/api/guilds/${guildId}/antinuke`, { method: 'PATCH', body: JSON.stringify(body) });
    const data = await res.json();
    if (res.ok) { status.textContent = 'Saved.'; status.className = 'save-status ok'; setBadge(anCard, body.enabled); }
    else { status.textContent = data.error || 'Failed to save — try again.'; status.className = 'save-status err'; anCard.querySelector('#anEnabled').checked = an.enabled; }
  };

  // ---------------- Antispam ----------------
  const asCard = makePanelPage('antispam', '<svg viewBox="0 0 24 24" fill="none" stroke="#a80f2c" stroke-width="1.6"><path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z"/><path d="M9 12l2 2 4-4"/></svg>', 'Antispam', 'Flood &amp; cross-channel spam detection', null, `
    <div class="field">
      <label>Honeypot / Trap Channel</label>
      <select id="asTrapChannel">
        <option value="">— None —</option>
        ${channels.map(c => `<option value="${c.id}" ${as_.trap_channel === c.id ? 'selected' : ''}>#${c.name}</option>`).join('')}
      </select>
    </div>
    <div class="field">
      <label>Alert Log Channel</label>
      <select id="asLogChannel">
        <option value="">— None —</option>
        ${channels.map(c => `<option value="${c.id}" ${as_.log_channel === c.id ? 'selected' : ''}>#${c.name}</option>`).join('')}
      </select>
    </div>
    <div class="field">
      <label>Punishment</label>
      <select id="asPunishment">
        <option value="ban" ${as_.punishment === 'ban' ? 'selected' : ''}>Ban</option>
        <option value="kick" ${as_.punishment === 'kick' ? 'selected' : ''}>Kick</option>
        <option value="timeout" ${as_.punishment === 'timeout' ? 'selected' : ''}>Timeout</option>
      </select>
    </div>
    <div class="field">
      <label>Cross-Channel Spam — messages / seconds</label>
      <div style="display:flex;gap:10px;">
        <input type="number" id="asThreshold" min="1" value="${as_.threshold}" style="flex:1;">
        <input type="number" id="asWindow" min="1" value="${as_.window}" style="flex:1;">
      </div>
    </div>
    <div class="field">
      <label>Same-Channel Flood — messages / seconds</label>
      <div style="display:flex;gap:10px;">
        <input type="number" id="asFloodCount" min="1" value="${as_.flood_count}" style="flex:1;">
        <input type="number" id="asFloodWindow" min="1" value="${as_.flood_window}" style="flex:1;">
      </div>
    </div>
    <div class="save-row">
      <button class="btn btn-primary" id="saveAs">Save Changes</button>
      <span class="save-status" id="asStatus"></span>
    </div>

    <div class="field" style="margin-top:28px;">
      <label>Ignored Users (exempt from spam detection)</label>
      <div id="asUserList" style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px;"></div>
      <div style="display:flex;gap:8px;">
        <input type="text" id="asUserId" placeholder="Discord User ID" style="flex:1;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'Outfit',sans-serif;font-size:14px;">
        <button class="btn btn-ghost" id="asUserAdd">Add</button>
      </div>
      <span class="save-status" id="asUserStatus"></span>
    </div>

    <div class="field">
      <label>Ignored Roles (exempt from spam detection)</label>
      <div id="asRoleList" style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px;"></div>
      <div style="display:flex;gap:8px;">
        <input type="text" id="asRoleId" placeholder="Discord Role ID" style="flex:1;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'Outfit',sans-serif;font-size:14px;">
        <button class="btn btn-ghost" id="asRoleAdd">Add</button>
      </div>
      <span class="save-status" id="asRoleStatus"></span>
    </div>
  `);

  function renderIgnoreUsers(list) {
    const box = asCard.querySelector('#asUserList');
    box.innerHTML = '';
    if (!list.length) { box.appendChild(el(`<div class="soon-note">None ignored.</div>`)); return; }
    list.forEach(u => {
      const row = el(`
        <div style="display:flex;align-items:center;gap:10px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;">
          ${u.avatar ? `<img src="${u.avatar}" style="width:24px;height:24px;border-radius:50%;">` : `<div style="width:24px;height:24px;border-radius:50%;background:var(--surface);"></div>`}
          <span style="flex:1;font-size:13.5px;">${u.name}</span>
          <button data-id="${u.id}" style="background:transparent;border:none;color:var(--muted-2);cursor:pointer;font-size:16px;">&times;</button>
        </div>
      `);
      row.querySelector('button').onclick = async (e) => {
        e.stopPropagation();
        const res = await api(`/api/guilds/${guildId}/antispam/ignore/user/${e.target.getAttribute('data-id')}`, { method: 'DELETE' });
        if (res.ok) { const data = await res.json(); renderIgnoreUsers(data.ignore_users); }
      };
      box.appendChild(row);
    });
  }

  function renderIgnoreRoles(list) {
    const box = asCard.querySelector('#asRoleList');
    box.innerHTML = '';
    if (!list.length) { box.appendChild(el(`<div class="soon-note">None ignored.</div>`)); return; }
    list.forEach(r => {
      const row = el(`
        <div style="display:flex;align-items:center;gap:10px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;">
          <div style="width:10px;height:10px;border-radius:50%;background:${r.color};"></div>
          <span style="flex:1;font-size:13.5px;">${r.name}</span>
          <button data-id="${r.id}" style="background:transparent;border:none;color:var(--muted-2);cursor:pointer;font-size:16px;">&times;</button>
        </div>
      `);
      row.querySelector('button').onclick = async (e) => {
        e.stopPropagation();
        const res = await api(`/api/guilds/${guildId}/antispam/ignore/role/${e.target.getAttribute('data-id')}`, { method: 'DELETE' });
        if (res.ok) { const data = await res.json(); renderIgnoreRoles(data.ignore_roles); }
      };
      box.appendChild(row);
    });
  }

  renderIgnoreUsers(as_.ignore_users);
  renderIgnoreRoles(as_.ignore_roles);

  asCard.querySelector('#asUserAdd').onclick = async (e) => {
    e.stopPropagation();
    const input = asCard.querySelector('#asUserId');
    const status = asCard.querySelector('#asUserStatus');
    const id = input.value.trim();
    if (!id) return;
    status.textContent = 'Adding...'; status.className = 'save-status';
    const res = await api(`/api/guilds/${guildId}/antispam/ignore/user`, { method: 'POST', body: JSON.stringify({ id }) });
    const data = await res.json();
    if (res.ok) { status.textContent = ''; input.value = ''; renderIgnoreUsers(data.ignore_users); }
    else { status.textContent = data.error || 'Failed to add.'; status.className = 'save-status err'; }
  };

  asCard.querySelector('#asRoleAdd').onclick = async (e) => {
    e.stopPropagation();
    const input = asCard.querySelector('#asRoleId');
    const status = asCard.querySelector('#asRoleStatus');
    const id = input.value.trim();
    if (!id) return;
    status.textContent = 'Adding...'; status.className = 'save-status';
    const res = await api(`/api/guilds/${guildId}/antispam/ignore/role`, { method: 'POST', body: JSON.stringify({ id }) });
    const data = await res.json();
    if (res.ok) { status.textContent = ''; input.value = ''; renderIgnoreRoles(data.ignore_roles); }
    else { status.textContent = data.error || 'Failed to add.'; status.className = 'save-status err'; }
  };

  asCard.querySelector('#saveAs').onclick = async (e) => {
    e.stopPropagation();
    const status = asCard.querySelector('#asStatus');
    status.textContent = 'Saving...'; status.className = 'save-status';
    const body = {
      trap_channel: asCard.querySelector('#asTrapChannel').value || null,
      log_channel: asCard.querySelector('#asLogChannel').value || null,
      punishment: asCard.querySelector('#asPunishment').value,
      threshold: parseInt(asCard.querySelector('#asThreshold').value, 10),
      window: parseInt(asCard.querySelector('#asWindow').value, 10),
      flood_count: parseInt(asCard.querySelector('#asFloodCount').value, 10),
      flood_window: parseInt(asCard.querySelector('#asFloodWindow').value, 10),
    };
    const res = await api(`/api/guilds/${guildId}/antispam`, { method: 'PATCH', body: JSON.stringify(body) });
    const data = await res.json();
    if (res.ok) { status.textContent = 'Saved.'; status.className = 'save-status ok'; }
    else { status.textContent = data.error || 'Failed to save — try again.'; status.className = 'save-status err'; }
  };

  // ---------------- Ticket System ----------------
  const catOptions = (selectedId) => categories.map(c => `<option value="${c.id}" ${selectedId === c.id ? 'selected' : ''}>${c.name}</option>`).join('');
  const chOptions = (selectedId) => channels.map(c => `<option value="${c.id}" ${selectedId === c.id ? 'selected' : ''}>#${c.name}</option>`).join('');
  const BUTTON_STYLE_OPTS = [['danger','Red'],['primary','Blurple'],['secondary','Gray'],['success','Green']];
  const BUTTON_STYLE_COLORS = { danger:'#da373c', primary:'#5865f2', secondary:'#4e5058', success:'#248046' };
  const styleOptions = (sel) => BUTTON_STYLE_OPTS.map(([v,l]) => `<option value="${v}" ${sel === v ? 'selected' : ''}>${l}</option>`).join('');

  function ticketFieldsHtml(cls, p) {
    p = p || { title:'Support Tickets', description:'Click the button below to open a support ticket.', welcome_message:'', category_id:null, log_channel_id:null, support_role_id:null, max_tickets:1, thumbnail:'', image:'', color:'8B0000', button_label:'Open Ticket', button_emoji:'', button_style:'danger', open_type:'button' };
    return `
      <div class="field">
        <label>Panel Title</label>
        <input type="text" class="${cls}Title" value="${p.title}">
      </div>
      <div class="field">
        <label>Panel Description</label>
        <textarea class="${cls}Desc" rows="2">${p.description}</textarea>
      </div>
      <div class="field">
        <label>Welcome Message (sent inside a new ticket)</label>
        <textarea class="${cls}Welcome" rows="2">${p.welcome_message}</textarea>
      </div>
      <div class="field-row">
        <div class="field"><label>Ticket Category</label>
          <select class="${cls}Category"><option value="">— None —</option>${catOptions(p.category_id)}</select>
        </div>
        <div class="field"><label>Log Channel</label>
          <select class="${cls}Log"><option value="">— None —</option>${chOptions(p.log_channel_id)}</select>
        </div>
      </div>
      <div class="field-row">
        <div class="field"><label>Support Role</label>
          <select class="${cls}Role"><option value="">— None —</option>${roleOptions(p.support_role_id)}</select>
        </div>
        <div class="field"><label>Max Open Tickets / User (1–5)</label>
          <input type="number" class="${cls}Max" min="1" max="5" value="${p.max_tickets}">
        </div>
      </div>
      <div class="custom-subhead" style="margin-top:8px;font-size:13.5px;">Appearance</div>
      <div class="field-row">
        <div class="field"><label>Thumbnail URL</label>
          <input type="url" class="${cls}Thumb" placeholder="https://example.com/icon.png" value="${p.thumbnail}">
        </div>
        <div class="field"><label>Banner URL</label>
          <input type="url" class="${cls}Banner" placeholder="https://example.com/banner.png" value="${p.image}">
        </div>
      </div>
      <div class="field">
        <label>Embed Color</label>
        <div style="display:flex;gap:10px;align-items:center;">
          <input type="color" class="${cls}ColorPick" value="#${p.color}" style="width:40px;height:40px;border:none;border-radius:8px;padding:0;background:none;cursor:pointer;">
          <input type="text" class="${cls}Color" value="${p.color}" maxlength="7" style="flex:1;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'JetBrains Mono',monospace;font-size:13px;">
        </div>
      </div>
      <div class="custom-subhead" style="margin-top:8px;font-size:13.5px;">Open Control</div>
      <div class="field-row">
        <div class="field"><label>Open Type</label>
          <select class="${cls}OpenType">
            <option value="button" ${p.open_type === 'button' ? 'selected' : ''}>Button</option>
            <option value="dropdown" ${p.open_type === 'dropdown' ? 'selected' : ''}>Dropdown</option>
          </select>
        </div>
        <div class="field"><label>Button Style</label>
          <select class="${cls}Style">${styleOptions(p.button_style)}</select>
        </div>
      </div>
      <div class="field-row">
        <div class="field"><label>Button Label</label>
          <input type="text" class="${cls}Label" maxlength="80" value="${p.button_label}">
        </div>
        <div class="field"><label>Button Emoji (optional)</label>
          <input type="text" class="${cls}Emoji" placeholder="🎫" value="${p.button_emoji}">
        </div>
      </div>
    `;
  }

  function ticketPreviewHtml(cls) {
    return `
      <div class="tix-preview">
        <div class="tix-preview-banner ${cls}PrevBanner"></div>
        <div class="tix-preview-body ${cls}PrevBody">
          <div class="tix-preview-head">
            <img class="tix-preview-thumb ${cls}PrevThumb" style="display:none;">
            <div class="tix-preview-title ${cls}PrevTitle">Support Tickets</div>
          </div>
          <div class="tix-preview-desc ${cls}PrevDesc">Click the button below to open a support ticket.</div>
          <div class="tix-preview-btn ${cls}PrevBtn">🎫 Open Ticket</div>
        </div>
      </div>
      <div class="tix-preview-caption">Live preview — updates as you type</div>
    `;
  }

  function bindTicketFields(scope, cls) {
    const colorPick = scope.querySelector(`.${cls}ColorPick`);
    const colorText = scope.querySelector(`.${cls}Color`);
    colorPick.oninput = () => { colorText.value = colorPick.value.replace('#','').toUpperCase(); colorText.dispatchEvent(new Event('input')); };
    colorText.addEventListener('input', () => { const v = colorText.value.replace('#','').trim(); if (/^[0-9A-Fa-f]{6}$/.test(v)) colorPick.value = '#' + v; });
    attachEmojiPicker(scope.querySelector(`.${cls}Emoji`), guildEmojis);
    return {
      title: () => scope.querySelector(`.${cls}Title`).value,
      description: () => scope.querySelector(`.${cls}Desc`).value,
      welcome_message: () => scope.querySelector(`.${cls}Welcome`).value,
      category_id: () => scope.querySelector(`.${cls}Category`).value || null,
      log_channel_id: () => scope.querySelector(`.${cls}Log`).value || null,
      support_role_id: () => scope.querySelector(`.${cls}Role`).value || null,
      max_tickets: () => parseInt(scope.querySelector(`.${cls}Max`).value, 10),
      thumbnail: () => scope.querySelector(`.${cls}Thumb`).value,
      image: () => scope.querySelector(`.${cls}Banner`).value,
      color: () => scope.querySelector(`.${cls}Color`).value,
      open_type: () => scope.querySelector(`.${cls}OpenType`).value,
      button_style: () => scope.querySelector(`.${cls}Style`).value,
      button_label: () => scope.querySelector(`.${cls}Label`).value,
      button_emoji: () => scope.querySelector(`.${cls}Emoji`).value,
    };
  }

  function wireTicketPreview(scope, cls, f) {
    const banner = scope.querySelector(`.${cls}PrevBanner`);
    const body   = scope.querySelector(`.${cls}PrevBody`);
    const thumb  = scope.querySelector(`.${cls}PrevThumb`);
    const title  = scope.querySelector(`.${cls}PrevTitle`);
    const desc   = scope.querySelector(`.${cls}PrevDesc`);
    const btn    = scope.querySelector(`.${cls}PrevBtn`);
    function refresh() {
      title.textContent = f.title() || 'Support Tickets';
      desc.textContent = f.description() || 'Click the button below to open a support ticket.';
      const thumbUrl = f.thumbnail();
      if (thumbUrl) { thumb.src = thumbUrl; thumb.style.display = ''; thumb.onerror = () => { thumb.style.display = 'none'; }; }
      else { thumb.style.display = 'none'; }
      const bannerUrl = f.image();
      banner.style.backgroundImage = bannerUrl ? `url('${bannerUrl}')` : '';
      const color = '#' + (f.color() || '8B0000').replace('#','');
      body.style.borderLeftColor = /^#[0-9A-Fa-f]{6}$/.test(color) ? color : '#8B0000';
      btn.style.background = BUTTON_STYLE_COLORS[f.button_style()] || BUTTON_STYLE_COLORS.danger;
      btn.textContent = (f.button_emoji() ? f.button_emoji() + ' ' : '') + (f.button_label() || 'Open Ticket');
    }
    scope.querySelectorAll(`.${cls}Title, .${cls}Desc, .${cls}Thumb, .${cls}Banner, .${cls}Color, .${cls}Style, .${cls}Label, .${cls}Emoji`).forEach(inp => {
      inp.addEventListener('input', refresh);
    });
    refresh();
  }

  const tixBadge = tix.panels.length ? `${tix.panels.length} panel${tix.panels.length === 1 ? '' : 's'}` : 'No panels yet';

  const tabsHtml = `<div class="tix-tabs">` +
    tix.panels.map((p, idx) => `<button class="tix-tab" data-tab="ex${idx}"><span class="tix-dot ${p.is_live ? 'on' : 'off'}"></span>${p.id}</button>`).join('') +
    `<button class="tix-tab tix-tab-new" data-tab="new">+ New Panel</button></div>`;

  const existingPanes = tix.panels.map((p, idx) => `
    <div class="tix-pane" data-pane="ex${idx}">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:18px;flex-wrap:wrap;">
        <span class="mono" style="font-size:13px;color:var(--gold);">${p.id}</span>
        ${p.is_live ? `<span class="status-badge on" style="font-size:10px;">Live${p.post_channel_name ? ' in #' + p.post_channel_name : ''}</span>` : `<span class="status-badge off" style="font-size:10px;">Not posted</span>`}
        ${p.types_count ? `<span class="soon-note" style="margin:0;">${p.types_count} ticket type${p.types_count === 1 ? '' : 's'} via /tickettype</span>` : ''}
      </div>
      <div class="tix-layout">
        <div>${ticketPreviewHtml('ex' + idx)}</div>
        <div>
          ${ticketFieldsHtml('ex' + idx, p)}
          <div class="save-row">
            <button class="btn btn-primary exSave" data-idx="${idx}">Save Settings</button>
            <span class="save-status exStatus" data-idx="${idx}"></span>
          </div>
          <div class="custom-subhead" style="margin-top:24px;font-size:13.5px;">Post / Move This Panel</div>
          <div class="custom-subnote">Sends this panel's current message to the channel below — updates it in place if it's still there, otherwise posts a fresh one.</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <select class="exPostChannel" data-idx="${idx}" style="flex:1;min-width:180px;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'Outfit',sans-serif;font-size:14px;">
              <option value="">— Pick a channel —</option>${chOptions(p.post_channel_id)}
            </select>
            <button class="btn btn-gold exPost" data-idx="${idx}">Post to Channel</button>
          </div>
          <span class="save-status exPostStatus" data-idx="${idx}"></span>
        </div>
      </div>
    </div>
  `).join('');

  const newPane = `
    <div class="tix-pane" data-pane="new">
      <div class="custom-subnote" style="margin-bottom:18px;">Give it a short ID (e.g. <span class="mono">support</span>), configure how it looks, pick a channel, and post it.</div>
      <div class="tix-layout">
        <div>${ticketPreviewHtml('new')}</div>
        <div>
          <div class="field">
            <label>Panel ID</label>
            <input type="text" id="newPanelId" placeholder="support" maxlength="32">
          </div>
          ${ticketFieldsHtml('new', null)}
          <div class="field">
            <label>Post In Channel</label>
            <select id="newPostChannel"><option value="">— Pick a channel —</option>${chOptions(null)}</select>
          </div>
          <div class="save-row">
            <button class="btn btn-gold" id="newPost">Create &amp; Post Panel</button>
            <span class="save-status" id="newPostStatus"></span>
          </div>
        </div>
      </div>
    </div>
  `;

  const tixBody = tabsHtml + `<div class="tix-panes">` + existingPanes + newPane + `</div>`;

  const tixCard = makePanelPage('tickets', '<svg viewBox="0 0 24 24" fill="none" stroke="#a80f2c" stroke-width="1.6"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 10h16"/></svg>', 'Ticket System', 'Support panels, categories, staff roles', tixBadge, tixBody);

  // ---- tab switching ----
  const tixTabBtns = tixCard.querySelectorAll('.tix-tab');
  const tixPanes = tixCard.querySelectorAll('.tix-pane');
  function showTixTab(name) {
    tixTabBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-tab') === name));
    tixPanes.forEach(p => p.classList.toggle('active', p.getAttribute('data-pane') === name));
  }
  tixTabBtns.forEach(b => { b.onclick = (e) => { e.stopPropagation(); showTixTab(b.getAttribute('data-tab')); }; });
  showTixTab(tix.panels.length ? 'ex0' : 'new');

  // ---- existing panels: fields, preview, save, post ----
  tixCard.querySelectorAll('[data-pane^="ex"]').forEach((pane, idx) => {
    const panelId = tix.panels[idx].id;
    const f = bindTicketFields(pane, 'ex' + idx);
    wireTicketPreview(pane, 'ex' + idx, f);

    pane.querySelector('.exSave').onclick = async (e) => {
      e.stopPropagation();
      const status = pane.querySelector('.exStatus');
      status.textContent = 'Saving...'; status.className = 'save-status exStatus';
      const body = {
        title: f.title(), description: f.description(), welcome_message: f.welcome_message(),
        category_id: f.category_id(), log_channel_id: f.log_channel_id(), support_role_id: f.support_role_id(),
        max_tickets: f.max_tickets(), thumbnail: f.thumbnail(), image: f.image(), color: f.color(),
        open_type: f.open_type(), button_style: f.button_style(), button_label: f.button_label(), button_emoji: f.button_emoji(),
      };
      const res = await api(`/api/guilds/${guildId}/tickets/${panelId}`, { method: 'PATCH', body: JSON.stringify(body) });
      const data = await res.json();
      if (res.ok) { status.textContent = 'Saved — live panel message updated too.'; status.className = 'save-status exStatus ok'; }
      else { status.textContent = data.error || 'Failed to save — try again.'; status.className = 'save-status exStatus err'; }
    };

    pane.querySelector('.exPost').onclick = async (e) => {
      e.stopPropagation();
      const status = pane.querySelector('.exPostStatus');
      const channelSel = pane.querySelector('.exPostChannel');
      if (!channelSel.value) { status.textContent = 'Pick a channel first.'; status.className = 'save-status exPostStatus err'; return; }
      status.textContent = 'Posting...'; status.className = 'save-status exPostStatus';
      const body = {
        panel_id: panelId, post_channel_id: channelSel.value,
        title: f.title(), description: f.description(), welcome_message: f.welcome_message(),
        category_id: f.category_id(), log_channel_id: f.log_channel_id(), support_role_id: f.support_role_id(),
        max_tickets: f.max_tickets(), thumbnail: f.thumbnail(), image: f.image(), color: f.color(),
        open_type: f.open_type(), button_style: f.button_style(), button_label: f.button_label(), button_emoji: f.button_emoji(),
      };
      const res = await api(`/api/guilds/${guildId}/tickets`, { method: 'POST', body: JSON.stringify(body) });
      const data = await res.json();
      if (res.ok) { status.textContent = 'Posted.'; status.className = 'save-status exPostStatus ok'; setTimeout(() => renderGuildEditor(guildId, me), 700); }
      else { status.textContent = data.error || 'Failed to post — try again.'; status.className = 'save-status exPostStatus err'; }
    };
  });

  // ---- new panel: fields, preview, create ----
  (function bindNewPanel() {
    const newPaneEl = tixCard.querySelector('[data-pane="new"]');
    const f = bindTicketFields(newPaneEl, 'new');
    wireTicketPreview(newPaneEl, 'new', f);
    newPaneEl.querySelector('#newPost').onclick = async (e) => {
      e.stopPropagation();
      const status = newPaneEl.querySelector('#newPostStatus');
      const idInput = newPaneEl.querySelector('#newPanelId');
      const channelSel = newPaneEl.querySelector('#newPostChannel');
      if (!idInput.value.trim()) { status.textContent = 'Give the panel an ID.'; status.className = 'save-status err'; return; }
      if (!channelSel.value) { status.textContent = 'Pick a channel to post in.'; status.className = 'save-status err'; return; }
      status.textContent = 'Posting...'; status.className = 'save-status';
      const body = {
        panel_id: idInput.value.trim(), post_channel_id: channelSel.value,
        title: f.title(), description: f.description(), welcome_message: f.welcome_message(),
        category_id: f.category_id(), log_channel_id: f.log_channel_id(), support_role_id: f.support_role_id(),
        max_tickets: f.max_tickets(), thumbnail: f.thumbnail(), image: f.image(), color: f.color(),
        open_type: f.open_type(), button_style: f.button_style(), button_label: f.button_label(), button_emoji: f.button_emoji(),
      };
      const res = await api(`/api/guilds/${guildId}/tickets`, { method: 'POST', body: JSON.stringify(body) });
      const data = await res.json();
      if (res.ok) { status.textContent = 'Posted! Reloading...'; status.className = 'save-status ok'; setTimeout(() => renderGuildEditor(guildId, me), 700); }
      else { status.textContent = data.error || 'Failed to post — try again.'; status.className = 'save-status err'; }
    };
  })();

  // ---------------- Message Components ----------------
  const RESPONSE_STYLE_COLORS = { primary:'#5865f2', secondary:'#4e5058', success:'#248046', danger:'#da373c' };
  const RESPONSE_STYLE_OPTS = [['secondary','Gray'],['primary','Blurple'],['success','Green'],['danger','Red']];
  const responseStyleOptions = (sel) => RESPONSE_STYLE_OPTS.map(([v,l]) => `<option value="${v}" ${sel === v ? 'selected' : ''}>${l}</option>`).join('');

  function componentFieldsHtml(cls, c) {
    c = c || { title:'', description:'', thumbnail:'', image:'', color:'8B0000' };
    return `
      <div class="field">
        <label>Title (optional)</label>
        <input type="text" class="${cls}Title" maxlength="256" value="${c.title}">
      </div>
      <div class="field">
        <label>Description</label>
        <textarea class="${cls}Desc" rows="3">${c.description}</textarea>
      </div>
      <div class="field-row">
        <div class="field"><label>Thumbnail URL</label>
          <input type="url" class="${cls}Thumb" placeholder="https://example.com/icon.png" value="${c.thumbnail}">
        </div>
        <div class="field"><label>Banner URL</label>
          <input type="url" class="${cls}Banner" placeholder="https://example.com/banner.png" value="${c.image}">
        </div>
      </div>
      <div class="field">
        <label>Accent Color</label>
        <div style="display:flex;gap:10px;align-items:center;">
          <input type="color" class="${cls}ColorPick" value="#${c.color}" style="width:40px;height:40px;border:none;border-radius:8px;padding:0;background:none;cursor:pointer;">
          <input type="text" class="${cls}Color" value="${c.color}" maxlength="7" style="flex:1;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'JetBrains Mono',monospace;font-size:13px;">
        </div>
      </div>
    `;
  }

  function componentPreviewHtml(cls) {
    return `
      <div class="tix-preview">
        <div class="tix-preview-banner ${cls}PrevBanner"></div>
        <div class="tix-preview-body ${cls}PrevBody">
          <div class="tix-preview-head">
            <img class="tix-preview-thumb ${cls}PrevThumb" style="display:none;">
            <div class="tix-preview-title ${cls}PrevTitle"></div>
          </div>
          <div class="tix-preview-desc ${cls}PrevDesc">Nothing set yet.</div>
          <div class="${cls}PrevButtons" style="display:flex;flex-wrap:wrap;gap:6px;"></div>
        </div>
      </div>
      <div class="tix-preview-caption">Live preview — updates as you type</div>
    `;
  }

  function bindComponentFields(scope, cls) {
    const colorPick = scope.querySelector(`.${cls}ColorPick`);
    const colorText = scope.querySelector(`.${cls}Color`);
    colorPick.oninput = () => { colorText.value = colorPick.value.replace('#','').toUpperCase(); colorText.dispatchEvent(new Event('input')); };
    colorText.addEventListener('input', () => { const v = colorText.value.replace('#','').trim(); if (/^[0-9A-Fa-f]{6}$/.test(v)) colorPick.value = '#' + v; });
    return {
      title: () => scope.querySelector(`.${cls}Title`).value,
      description: () => scope.querySelector(`.${cls}Desc`).value,
      thumbnail: () => scope.querySelector(`.${cls}Thumb`).value,
      image: () => scope.querySelector(`.${cls}Banner`).value,
      color: () => scope.querySelector(`.${cls}Color`).value,
    };
  }

  function componentButtonPreviewHtml(btn) {
    if (btn.kind === 'link') {
      return `<span style="display:inline-flex;align-items:center;gap:5px;padding:7px 12px;border-radius:5px;font-size:12px;font-weight:600;border:1px solid var(--line-2);color:var(--ink);">${btn.emoji ? btn.emoji + ' ' : ''}${btn.label} &#8599;</span>`;
    }
    const bg = RESPONSE_STYLE_COLORS[btn.style] || RESPONSE_STYLE_COLORS.secondary;
    return `<span style="display:inline-flex;align-items:center;gap:5px;padding:7px 12px;border-radius:5px;font-size:12px;font-weight:600;color:#fff;background:${bg};">${btn.emoji ? btn.emoji + ' ' : ''}${btn.label}</span>`;
  }

  function wireComponentPreview(scope, cls, f, getButtons) {
    const banner = scope.querySelector(`.${cls}PrevBanner`);
    const body   = scope.querySelector(`.${cls}PrevBody`);
    const thumb  = scope.querySelector(`.${cls}PrevThumb`);
    const title  = scope.querySelector(`.${cls}PrevTitle`);
    const desc   = scope.querySelector(`.${cls}PrevDesc`);
    const btnsEl = scope.querySelector(`.${cls}PrevButtons`);
    function refresh() {
      const t = f.title();
      title.textContent = t; title.style.display = t ? '' : 'none';
      desc.textContent = f.description() || 'Nothing set yet.';
      const thumbUrl = f.thumbnail();
      if (thumbUrl) { thumb.src = thumbUrl; thumb.style.display = ''; thumb.onerror = () => { thumb.style.display = 'none'; }; }
      else { thumb.style.display = 'none'; }
      const bannerUrl = f.image();
      banner.style.backgroundImage = bannerUrl ? `url('${bannerUrl}')` : '';
      const color = '#' + (f.color() || '8B0000').replace('#','');
      body.style.borderLeftColor = /^#[0-9A-Fa-f]{6}$/.test(color) ? color : '#8B0000';
      const buttons = getButtons ? getButtons() : [];
      btnsEl.innerHTML = buttons.length ? buttons.map(componentButtonPreviewHtml).join('') : '';
    }
    scope.querySelectorAll(`.${cls}Title, .${cls}Desc, .${cls}Thumb, .${cls}Banner, .${cls}Color`).forEach(inp => inp.addEventListener('input', refresh));
    refresh();
    return refresh;
  }

  function buttonManagerHtml(cls, buttons, maxButtons) {
    const rows = buttons.map(b => `
      <div style="display:flex;align-items:center;gap:10px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:9px 12px;">
        <span style="flex:1;font-size:12.5px;">${b.description}</span>
        <button class="btnRemove" data-idx="${b.index}" style="background:transparent;border:none;color:var(--muted-2);cursor:pointer;font-size:16px;">&times;</button>
      </div>
    `).join('');
    return `
      <div class="${cls}ButtonSection">
      <div class="custom-subhead" style="margin-top:8px;font-size:13.5px;">Buttons <span class="soon-note" style="margin:0;display:inline;">(${buttons.length}/${maxButtons})</span></div>
      <div class="${cls}BtnList" style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px;">${rows || `<div class="soon-note">No buttons yet — add one below.</div>`}</div>
      <div style="display:flex;gap:8px;margin-bottom:12px;">
        <button type="button" class="${cls}KindBtn" data-kind="link" style="flex:1;">+ Link Button</button>
        <button type="button" class="${cls}KindBtn" data-kind="response" style="flex:1;">+ Response Button</button>
      </div>
      <div class="${cls}LinkForm" style="display:none;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:16px;margin-bottom:12px;">
        <div class="field-row">
          <div class="field"><label>Label</label><input type="text" class="${cls}LinkLabel" maxlength="80" placeholder="Visit Website"></div>
          <div class="field"><label>Emoji (optional)</label><input type="text" class="${cls}LinkEmoji" placeholder="🔗"></div>
        </div>
        <div class="field"><label>URL</label><input type="url" class="${cls}LinkUrl" placeholder="https://example.com"></div>
        <button type="button" class="btn btn-primary btn-sm ${cls}LinkAdd">Add Link Button</button>
        <span class="save-status ${cls}LinkStatus"></span>
      </div>
      <div class="${cls}RespForm" style="display:none;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:16px;margin-bottom:12px;">
        <div class="field-row">
          <div class="field"><label>Label</label><input type="text" class="${cls}RespLabel" maxlength="80" placeholder="More Info"></div>
          <div class="field"><label>Emoji (optional)</label><input type="text" class="${cls}RespEmoji" placeholder="💬"></div>
        </div>
        <div class="field"><label>Button Style</label><select class="${cls}RespStyle">${responseStyleOptions('secondary')}</select></div>
        <div class="field"><label>Response Title</label><input type="text" class="${cls}RespTitle" maxlength="256" placeholder="Shown when clicked"></div>
        <div class="field"><label>Response Description</label><textarea class="${cls}RespDesc" rows="2"></textarea></div>
        <div class="field-row">
          <div class="field"><label>Response Thumbnail (optional)</label><input type="url" class="${cls}RespThumb"></div>
          <div class="field"><label>Response Banner (optional)</label><input type="url" class="${cls}RespBanner"></div>
        </div>
        <button type="button" class="btn btn-primary btn-sm ${cls}RespAdd">Add Response Button</button>
        <span class="save-status ${cls}RespStatus"></span>
      </div>
      </div>
    `;
  }

  function bindButtonManager(scope, cls, guildIdRef, componentIdRef, onChanged) {
    const linkBtn = scope.querySelector(`.${cls}KindBtn[data-kind="link"]`);
    const respBtn = scope.querySelector(`.${cls}KindBtn[data-kind="response"]`);
    const linkForm = scope.querySelector(`.${cls}LinkForm`);
    const respForm = scope.querySelector(`.${cls}RespForm`);
    if (linkBtn) linkBtn.onclick = (e) => { e.stopPropagation(); linkForm.style.display = linkForm.style.display === 'none' ? '' : 'none'; respForm.style.display = 'none'; };
    if (respBtn) respBtn.onclick = (e) => { e.stopPropagation(); respForm.style.display = respForm.style.display === 'none' ? '' : 'none'; linkForm.style.display = 'none'; };
    attachEmojiPicker(scope.querySelector(`.${cls}LinkEmoji`), guildEmojis);
    attachEmojiPicker(scope.querySelector(`.${cls}RespEmoji`), guildEmojis);

    const linkAdd = scope.querySelector(`.${cls}LinkAdd`);
    if (linkAdd) linkAdd.onclick = async (e) => {
      e.stopPropagation();
      const status = scope.querySelector(`.${cls}LinkStatus`);
      status.textContent = 'Adding...'; status.className = 'save-status';
      const body = {
        kind: 'link',
        label: scope.querySelector(`.${cls}LinkLabel`).value,
        url: scope.querySelector(`.${cls}LinkUrl`).value,
        emoji: scope.querySelector(`.${cls}LinkEmoji`).value,
      };
      const res = await api(`/api/guilds/${guildIdRef}/components/${componentIdRef()}/buttons`, { method: 'POST', body: JSON.stringify(body) });
      const data = await res.json();
      if (res.ok) { onChanged(data); }
      else { status.textContent = data.error || 'Failed to add.'; status.className = 'save-status err'; }
    };

    const respAdd = scope.querySelector(`.${cls}RespAdd`);
    if (respAdd) respAdd.onclick = async (e) => {
      e.stopPropagation();
      const status = scope.querySelector(`.${cls}RespStatus`);
      status.textContent = 'Adding...'; status.className = 'save-status';
      const body = {
        kind: 'response',
        label: scope.querySelector(`.${cls}RespLabel`).value,
        emoji: scope.querySelector(`.${cls}RespEmoji`).value,
        style: scope.querySelector(`.${cls}RespStyle`).value,
        response_title: scope.querySelector(`.${cls}RespTitle`).value,
        response_description: scope.querySelector(`.${cls}RespDesc`).value,
        response_thumbnail: scope.querySelector(`.${cls}RespThumb`).value,
        response_banner: scope.querySelector(`.${cls}RespBanner`).value,
      };
      const res = await api(`/api/guilds/${guildIdRef}/components/${componentIdRef()}/buttons`, { method: 'POST', body: JSON.stringify(body) });
      const data = await res.json();
      if (res.ok) { onChanged(data); }
      else { status.textContent = data.error || 'Failed to add.'; status.className = 'save-status err'; }
    };

    scope.querySelectorAll(`.${cls}BtnList .btnRemove`).forEach(btn => {
      btn.onclick = async (e) => {
        e.stopPropagation();
        const idx = btn.getAttribute('data-idx');
        const res = await api(`/api/guilds/${guildIdRef}/components/${componentIdRef()}/buttons/${idx}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok) onChanged(data);
      };
    });
  }

  const compsBadge = comps.components.length ? `${comps.components.length} message${comps.components.length === 1 ? '' : 's'}` : 'No messages yet';

  const compTabsHtml = `<div class="tix-tabs">` +
    comps.components.map((c, idx) => `<button class="tix-tab" data-tab="comp${idx}"><span class="tix-dot ${c.is_live ? 'on' : 'off'}"></span>${c.id}</button>`).join('') +
    `<button class="tix-tab tix-tab-new" data-tab="compnew">+ New Message</button></div>`;

  const compExistingPanes = comps.components.map((c, idx) => `
    <div class="tix-pane" data-pane="comp${idx}">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:18px;flex-wrap:wrap;">
        <span class="mono" style="font-size:13px;color:var(--gold);">${c.id}</span>
        ${c.is_live ? `<span class="status-badge on" style="font-size:10px;">Live${c.post_channel_name ? ' in #' + c.post_channel_name : ''}</span>` : `<span class="status-badge off" style="font-size:10px;">Not posted</span>`}
      </div>
      <div class="tix-layout">
        <div>${componentPreviewHtml('cx' + idx)}</div>
        <div>
          ${componentFieldsHtml('cx' + idx, c)}
          <div class="save-row">
            <button class="btn btn-primary cxSave" data-idx="${idx}">Save Settings</button>
            <span class="save-status cxStatus" data-idx="${idx}"></span>
          </div>
          <div class="custom-divider"></div>
          ${buttonManagerHtml('cx' + idx, c.buttons, comps.max_buttons)}
          <div class="custom-subhead" style="margin-top:24px;font-size:13.5px;">Post / Move This Message</div>
          <div class="custom-subnote">Sends this message's current content to the channel below — updates it in place if it's still there, otherwise posts a fresh one.</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
            <select class="cxPostChannel" data-idx="${idx}" style="flex:1;min-width:180px;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:10px 12px;color:var(--ink);font-family:'Outfit',sans-serif;font-size:14px;">
              <option value="">— Pick a channel —</option>${chOptions(c.post_channel_id)}
            </select>
            <button class="btn btn-gold cxPost" data-idx="${idx}">Post to Channel</button>
          </div>
          <span class="save-status cxPostStatus" data-idx="${idx}"></span>
        </div>
      </div>
    </div>
  `).join('');

  const compNewPane = `
    <div class="tix-pane" data-pane="compnew">
      <div class="custom-subnote" style="margin-bottom:18px;">Give it a short ID (e.g. <span class="mono">rules</span>), write your message, pick a channel, and post it. Buttons can be added once it's created.</div>
      <div class="tix-layout">
        <div>${componentPreviewHtml('cnew')}</div>
        <div>
          <div class="field">
            <label>Message ID</label>
            <input type="text" id="compNewId" placeholder="rules" maxlength="32">
          </div>
          ${componentFieldsHtml('cnew', null)}
          <div class="field">
            <label>Post In Channel</label>
            <select id="compNewPostChannel"><option value="">— Pick a channel —</option>${chOptions(null)}</select>
          </div>
          <div class="save-row">
            <button class="btn btn-gold" id="compNewPost">Create &amp; Post Message</button>
            <span class="save-status" id="compNewPostStatus"></span>
          </div>
        </div>
      </div>
    </div>
  `;

  const compBody = compTabsHtml + `<div class="tix-panes">` + compExistingPanes + compNewPane + `</div>`;

  const compCard = makePanelPage('components', '<svg viewBox="0 0 24 24" fill="none" stroke="#a80f2c" stroke-width="1.6"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6M9 13h6M9 17h3"/></svg>', 'Message Components', 'Custom messages with link &amp; response buttons', compsBadge, compBody);

  // ---- tab switching ----
  const compTabBtns = compCard.querySelectorAll('.tix-tab');
  const compPanes = compCard.querySelectorAll('.tix-pane');
  function showCompTab(name) {
    compTabBtns.forEach(b => b.classList.toggle('active', b.getAttribute('data-tab') === name));
    compPanes.forEach(p => p.classList.toggle('active', p.getAttribute('data-pane') === name));
  }
  compTabBtns.forEach(b => { b.onclick = (e) => { e.stopPropagation(); showCompTab(b.getAttribute('data-tab')); }; });
  showCompTab(comps.components.length ? 'comp0' : 'compnew');

  // ---- existing messages: fields, preview, buttons, save, post ----
  compCard.querySelectorAll('[data-pane^="comp"]:not([data-pane="compnew"])').forEach((pane, idx) => {
    const componentId = comps.components[idx].id;
    let currentButtons = comps.components[idx].buttons;
    const f = bindComponentFields(pane, 'cx' + idx);
    const refreshPreview = wireComponentPreview(pane, 'cx' + idx, f, () => currentButtons);

    function rebindButtons() {
      bindButtonManager(pane, 'cx' + idx, guildId, () => componentId, (data) => {
        const updated = data.components.find(c => c.id === componentId);
        currentButtons = updated ? updated.buttons : [];
        const section = pane.querySelector(`.cx${idx}ButtonSection`);
        section.outerHTML = buttonManagerHtml('cx' + idx, currentButtons, comps.max_buttons);
        rebindButtons();
        refreshPreview();
      });
    }
    rebindButtons();

    pane.querySelector('.cxSave').onclick = async (e) => {
      e.stopPropagation();
      const status = pane.querySelector('.cxStatus');
      status.textContent = 'Saving...'; status.className = 'save-status cxStatus';
      const body = { title: f.title(), description: f.description(), thumbnail: f.thumbnail(), image: f.image(), color: f.color() };
      const res = await api(`/api/guilds/${guildId}/components/${componentId}`, { method: 'PATCH', body: JSON.stringify(body) });
      const data = await res.json();
      if (res.ok) { status.textContent = 'Saved — live message updated too.'; status.className = 'save-status cxStatus ok'; }
      else { status.textContent = data.error || 'Failed to save — try again.'; status.className = 'save-status cxStatus err'; }
    };

    pane.querySelector('.cxPost').onclick = async (e) => {
      e.stopPropagation();
      const status = pane.querySelector('.cxPostStatus');
      const channelSel = pane.querySelector('.cxPostChannel');
      if (!channelSel.value) { status.textContent = 'Pick a channel first.'; status.className = 'save-status cxPostStatus err'; return; }
      status.textContent = 'Posting...'; status.className = 'save-status cxPostStatus';
      const body = { component_id: componentId, post_channel_id: channelSel.value, title: f.title(), description: f.description(), thumbnail: f.thumbnail(), image: f.image(), color: f.color() };
      const res = await api(`/api/guilds/${guildId}/components`, { method: 'POST', body: JSON.stringify(body) });
      const data = await res.json();
      if (res.ok) { status.textContent = 'Posted.'; status.className = 'save-status cxPostStatus ok'; setTimeout(() => renderGuildEditor(guildId, me), 700); }
      else { status.textContent = data.error || 'Failed to post — try again.'; status.className = 'save-status cxPostStatus err'; }
    };
  });

  // ---- new message: fields, preview, create (buttons added after creation) ----
  (function bindNewComponent() {
    const newPaneEl = compCard.querySelector('[data-pane="compnew"]');
    const f = bindComponentFields(newPaneEl, 'cnew');
    wireComponentPreview(newPaneEl, 'cnew', f, () => []);
    newPaneEl.querySelector('#compNewPost').onclick = async (e) => {
      e.stopPropagation();
      const status = newPaneEl.querySelector('#compNewPostStatus');
      const idInput = newPaneEl.querySelector('#compNewId');
      const channelSel = newPaneEl.querySelector('#compNewPostChannel');
      if (!idInput.value.trim()) { status.textContent = 'Give the message an ID.'; status.className = 'save-status err'; return; }
      if (!channelSel.value) { status.textContent = 'Pick a channel to post in.'; status.className = 'save-status err'; return; }
      status.textContent = 'Posting...'; status.className = 'save-status';
      const body = { component_id: idInput.value.trim(), post_channel_id: channelSel.value, title: f.title(), description: f.description(), thumbnail: f.thumbnail(), image: f.image(), color: f.color() };
      const res = await api(`/api/guilds/${guildId}/components`, { method: 'POST', body: JSON.stringify(body) });
      const data = await res.json();
      if (res.ok) { status.textContent = 'Posted! Reloading...'; status.className = 'save-status ok'; setTimeout(() => renderGuildEditor(guildId, me), 700); }
      else { status.textContent = data.error || 'Failed to post — try again.'; status.className = 'save-status err'; }
    };
  })();

  // ---------------- Sidebar registration + default view ----------------
  addSidebarItem('leveling', '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>', 'Level & XP', lvl.enabled ? 'on' : 'off');
  addSidebarItem('antinuke', '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>', 'Anti-Nuke', an.enabled ? 'on' : 'off');
  addSidebarItem('antispam', '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z"/><path d="M9 12l2 2 4-4"/></svg>', 'Antispam', 'neutral');
  addSidebarItem('tickets', '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M4 10h16"/></svg>', 'Ticket System', tix.panels.length ? 'on' : 'off');
  addSidebarItem('components', '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6M9 13h6M9 17h3"/></svg>', 'Message Components', comps.components.length ? 'on' : 'off');
  sidebarNav.appendChild(el(`<div class="sidebar-divider"></div>`));
  addSidebarComingSoon('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z"/></svg>', 'Moderation');
  addSidebarComingSoon('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9 12l2 2 4-4M7.5 3.5L12 2l4.5 1.5L18 8l-1 8-5 4-5-4-1-8z"/></svg>', 'Verification');
  showSystem('leveling');
}

async function boot() {
  const meRes = await api('/api/me');
  const me = await meRes.json();
  renderNav(me);
  if (!me.logged_in) { renderLogin(); return; }

  const parts = window.location.pathname.split('/').filter(Boolean);
  if (parts.length === 2 && parts[0] === 'dashboard' && parts[1] === 'checkout') {
    renderCheckout(me);
  } else if (parts.length === 2 && parts[0] === 'dashboard') {
    renderGuildEditor(parts[1], me);
  } else {
    renderGuildPicker(me);
  }
}
boot();
</script>
</body>
</html>"""
