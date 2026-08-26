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
import time
import logging
from typing import Callable, Optional
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
    set_leveling: Callable[[int, dict], None],
    get_antinuke: Callable[[int], dict],
    set_antinuke: Callable[[int, dict], Optional[str]],
    add_antinuke_whitelist: Callable[[int, int], Optional[str]],
    remove_antinuke_whitelist: Callable[[int, int], None],
    get_antispam: Callable[[int], dict],
    set_antispam: Callable[[int, dict], Optional[str]],
    add_antispam_ignore: Callable[[int, str, int], Optional[str]],
    remove_antispam_ignore: Callable[[int, str, int], None],
) -> None:
    """Registers every /auth, /dashboard, and /api route onto the shared
    aiohttp `app` (the same one the top.gg webhook runs on). Everything
    this module needs from the bot/config side comes in as a callable:
      - get_bot()                    -> the discord.py Bot instance
      - get_leveling(guild_id)       -> {"enabled": bool, "channel_id": str|None, "difficulty": float}
      - set_leveling(guild_id, dict) -> applies + persists a partial update
      - get_antinuke(guild_id)       -> {"enabled", "log_channel", "punishment", "whitelist": [...], "bot_has_audit_log_perm"}
      - set_antinuke(guild_id, dict) -> applies + persists a partial update; returns an error string or None
      - add/remove_antinuke_whitelist(guild_id, user_id) -> mutate the whitelist by one user
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
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
            "prompt": "none",
        }
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
        resp = web.HTTPFound("/dashboard")
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
        set_leveling(int(guild_id), update)
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

    # ---------------- Frontend shell ----------------

    async def serve_dashboard_shell(request: web.Request) -> web.Response:
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    app.router.add_get("/auth/discord/login", handle_login)
    app.router.add_get("/auth/discord/callback", handle_callback)
    app.router.add_get("/auth/discord/logout", handle_logout)
    app.router.add_get("/api/me", api_me)
    app.router.add_get("/api/guilds/{guild_id}/leveling", api_get_leveling)
    app.router.add_patch("/api/guilds/{guild_id}/leveling", api_patch_leveling)
    app.router.add_get("/api/guilds/{guild_id}/channels", api_guild_channels)
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
    --void:#0a0605; --surface:#130b0c; --surface-2:#1c1112; --line:rgba(245,240,236,0.09);
    --crimson:#a80f2c; --crimson-deep:#3d0010; --crimson-glow:rgba(168,15,44,0.35);
    --gold:#f5a623; --gold-glow:rgba(245,166,35,0.30);
    --ink:#f5f0ec; --muted:#a3908d; --muted-2:#6e5c5a;
  }
  *{ margin:0; padding:0; box-sizing:border-box; }
  body{ background:var(--void); color:var(--ink); font-family:'Outfit',sans-serif; min-height:100vh; }
  a{ color:inherit; text-decoration:none; }
  h1,h2,.display{ font-family:'Big Shoulders',sans-serif; font-weight:900; text-transform:uppercase; }
  .mono{ font-family:'JetBrains Mono',monospace; }
  .wrap{ max-width:960px; margin:0 auto; padding:0 28px; }
  .hex{ width:38px; height:39px; clip-path:polygon(50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%); display:flex; align-items:center; justify-content:center; background:linear-gradient(155deg,var(--crimson),var(--crimson-deep)); box-shadow:0 0 20px var(--crimson-glow); flex-shrink:0; }
  .hex span{ font-family:'Big Shoulders',sans-serif; font-weight:900; font-size:18px; }
  nav{ border-bottom:1px solid var(--line); padding:18px 0; }
  nav .row{ display:flex; align-items:center; justify-content:space-between; }
  .brand{ display:flex; align-items:center; gap:12px; font-family:'Big Shoulders',sans-serif; font-weight:700; font-size:18px; letter-spacing:0.02em; }
  .brand b{ color:var(--gold); }
  .userchip{ display:flex; align-items:center; gap:10px; font-size:13px; color:var(--muted); }
  .userchip img{ width:28px; height:28px; border-radius:50%; }
  .btn{ display:inline-flex; align-items:center; gap:8px; padding:10px 20px; font-weight:600; font-size:13px; border-radius:6px; border:none; cursor:pointer; }
  .btn-primary{ background:linear-gradient(135deg,var(--crimson),#5c0a1a); color:#fff; }
  .btn-ghost{ background:transparent; border:1px solid var(--line); color:var(--ink); }
  main{ padding:56px 0 100px; }
  .loading{ text-align:center; padding:80px 0; color:var(--muted-2); font-size:14px; }
  .login-card{ max-width:420px; margin:80px auto; text-align:center; padding:48px 32px; border:1px solid var(--line); border-radius:14px; background:var(--surface); }
  .login-card h1{ font-size:26px; margin:18px 0 10px; }
  .login-card p{ color:var(--muted); font-size:14px; margin-bottom:28px; line-height:1.6; }
  .guild-grid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:14px; margin-top:28px; }
  .guild-card{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:20px; cursor:pointer; transition:border-color .2s, transform .2s; display:flex; align-items:center; gap:12px; }
  .guild-card:hover{ border-color:rgba(245,240,236,0.2); transform:translateY(-2px); }
  .guild-card.disabled{ opacity:0.45; cursor:not-allowed; }
  .guild-icon{ width:40px; height:40px; border-radius:50%; background:var(--surface-2); flex-shrink:0; object-fit:cover; }
  .guild-name{ font-size:14px; font-weight:600; }
  .guild-note{ font-size:11px; color:var(--muted-2); margin-top:2px; }
  .page-title{ font-size:32px; margin-bottom:6px; }
  .page-sub{ color:var(--muted); font-size:14px; margin-bottom:36px; }
  .sys-card{ background:var(--surface); border:1px solid var(--line); border-radius:12px; margin-bottom:14px; overflow:hidden; }
  .sys-card-head{ display:flex; align-items:center; gap:14px; padding:20px 24px; cursor:pointer; user-select:none; }
  .sys-card-head:hover{ background:rgba(255,255,255,0.02); }
  .sys-card-icon{ width:38px; height:38px; border-radius:9px; background:var(--surface-2); border:1px solid var(--line); display:flex; align-items:center; justify-content:center; flex-shrink:0; }
  .sys-card-icon svg{ width:18px; height:18px; }
  .sys-card-title{ flex:1; min-width:0; }
  .sys-card-title h2{ font-family:'Outfit',sans-serif; text-transform:none; font-weight:700; font-size:16px; letter-spacing:0; margin-bottom:2px; }
  .sys-card-title p{ font-size:12.5px; color:var(--muted-2); }
  .status-badge{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; padding:4px 10px; border-radius:20px; flex-shrink:0; }
  .status-badge.on{ background:rgba(74,222,128,0.15); color:#4ade80; }
  .status-badge.off{ background:rgba(255,255,255,0.06); color:var(--muted-2); }
  .sys-card-chevron{ width:20px; height:20px; flex-shrink:0; transition:transform .2s ease; color:var(--muted); }
  .sys-card.open .sys-card-chevron{ transform:rotate(180deg); }
  .sys-card-body{ max-height:0; overflow:hidden; transition:max-height .25s ease; }
  .sys-card.open .sys-card-body{ max-height:2000px; }
  .sys-card-body-inner{ padding:4px 24px 26px; border-top:1px solid var(--line); padding-top:22px; }

  .panel{ background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:28px; margin-bottom:20px; }
  .panel-head{ display:flex; align-items:center; justify-content:space-between; margin-bottom:20px; }
  .panel-head h2{ font-family:'Outfit',sans-serif; text-transform:none; font-weight:700; font-size:17px; letter-spacing:0; }
  .field{ margin-bottom:20px; }
  .field label{ display:block; font-size:12.5px; color:var(--muted); text-transform:uppercase; letter-spacing:0.06em; margin-bottom:8px; }
  .field select, .field input[type=number]{
    width:100%; background:var(--surface-2); border:1px solid var(--line); border-radius:6px; padding:10px 12px;
    color:var(--ink); font-family:'Outfit',sans-serif; font-size:14px; outline:none;
  }
  .field select:focus, .field input:focus{ border-color:var(--crimson); }
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
</style>
</head>
<body>
<nav><div class="wrap row">
  <div class="brand"><div class="hex"><span>V</span></div>VALLENT <b>EXS</b> <span class="mono" style="font-size:11px;color:var(--muted-2);margin-left:4px;">DASHBOARD</span></div>
  <div id="navRight"></div>
</div></nav>
<main class="wrap" id="app"><div class="loading">Loading…</div></main>

<script>
const app = document.getElementById('app');
const navRight = document.getElementById('navRight');

function el(html){ const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstChild; }

async function api(path, opts={}) {
  const res = await fetch(path, { credentials: 'same-origin', headers: {'X-Requested-With':'vallent-dashboard','Content-Type':'application/json'}, ...opts });
  return res;
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
  app.appendChild(el(`
    <div class="login-card">
      <div class="hex" style="margin:0 auto;"><span>V</span></div>
      <h1>Sign in to manage<br>your server</h1>
      <p>Log in with Discord to configure VALLENT EXS on any server where you have Manage Server permission.</p>
      <a href="/auth/discord/login" class="btn btn-primary" style="width:100%;justify-content:center;">Continue with Discord</a>
    </div>
  `));
}

function renderGuildPicker(me) {
  app.innerHTML = '';
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

function badgeHtml(enabled) {
  if (enabled === null) return `<span class="status-badge" data-badge style="background:rgba(245,166,35,0.15);color:var(--gold);">Always Active</span>`;
  return `<span class="status-badge ${enabled ? 'on' : 'off'}" data-badge>${enabled ? 'Enabled' : 'Disabled'}</span>`;
}

function makeSysCard(icon, title, subtitle, enabled, bodyHtml) {
  const card = el(`
    <div class="sys-card">
      <div class="sys-card-head">
        <div class="sys-card-icon">${icon}</div>
        <div class="sys-card-title"><h2>${title}</h2><p>${subtitle}</p></div>
        ${badgeHtml(enabled)}
        <svg class="sys-card-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
      </div>
      <div class="sys-card-body"><div class="sys-card-body-inner">${bodyHtml}</div></div>
    </div>
  `);
  card.querySelector('.sys-card-head').addEventListener('click', () => card.classList.toggle('open'));
  return card;
}

function setBadge(card, enabled) {
  const badge = card.querySelector('[data-badge]');
  badge.textContent = enabled ? 'Enabled' : 'Disabled';
  badge.className = `status-badge ${enabled ? 'on' : 'off'}`;
}

async function renderGuildEditor(guildId) {
  app.innerHTML = '<div class="loading">Loading server settings…</div>';
  const [lvlRes, chRes, anRes, asRes] = await Promise.all([
    api(`/api/guilds/${guildId}/leveling`),
    api(`/api/guilds/${guildId}/channels`),
    api(`/api/guilds/${guildId}/antinuke`),
    api(`/api/guilds/${guildId}/antispam`),
  ]);
  if (lvlRes.status === 403 || lvlRes.status === 404) {
    app.innerHTML = `<div class="loading">You don't have access to manage this server.</div>`;
    return;
  }
  const lvl = await lvlRes.json();
  const channels = await chRes.json();
  const an = await anRes.json();
  const as_ = await asRes.json();

  app.innerHTML = '';
  app.appendChild(el(`<a href="/dashboard" class="back-link">&larr; All Servers</a>`));
  app.appendChild(el(`<h1 class="page-title">Server Settings</h1><p class="page-sub">Click a system below to open its settings. More systems (Moderation, Tickets, Verification...) are on the way.</p>`));

  // ---------------- Level & XP ----------------
  const lvlCard = makeSysCard('<svg viewBox="0 0 24 24" fill="none" stroke="#f5a623" stroke-width="1.6"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>', 'Level &amp; XP', 'XP gain, level-up announcements, difficulty', lvl.enabled, `
    <div class="field">
      <label>Enabled</label>
      <label class="toggle"><input type="checkbox" id="lvlEnabled" ${lvl.enabled ? 'checked' : ''}><span class="toggle-slider"></span></label>
    </div>
    <div class="field">
      <label>Level-Up Announcement Channel</label>
      <select id="lvlChannel">
        <option value="">— None (no announcement) —</option>
        ${channels.map(c => `<option value="${c.id}" ${lvl.channel_id === c.id ? 'selected' : ''}>#${c.name}</option>`).join('')}
      </select>
    </div>
    <div class="field">
      <label>XP Difficulty Multiplier (0.1 – 10)</label>
      <input type="number" id="lvlDifficulty" min="0.1" max="10" step="0.1" value="${lvl.difficulty}">
    </div>
    <div class="save-row">
      <button class="btn btn-primary" id="saveLvl">Save Changes</button>
      <span class="save-status" id="lvlStatus"></span>
    </div>
  `);
  app.appendChild(lvlCard);

  document.getElementById('saveLvl').onclick = async (e) => {
    e.stopPropagation();
    const status = document.getElementById('lvlStatus');
    status.textContent = 'Saving...'; status.className = 'save-status';
    const body = {
      enabled: document.getElementById('lvlEnabled').checked,
      channel_id: document.getElementById('lvlChannel').value || null,
      difficulty: parseFloat(document.getElementById('lvlDifficulty').value),
    };
    const res = await api(`/api/guilds/${guildId}/leveling`, { method: 'PATCH', body: JSON.stringify(body) });
    if (res.ok) { status.textContent = 'Saved.'; status.className = 'save-status ok'; setBadge(lvlCard, body.enabled); }
    else { status.textContent = 'Failed to save — try again.'; status.className = 'save-status err'; }
  };

  // ---------------- Anti-Nuke ----------------
  const anCard = makeSysCard('<svg viewBox="0 0 24 24" fill="none" stroke="#a80f2c" stroke-width="1.6"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></svg>', 'Anti-Nuke', 'Raid protection, mass-action detection', an.enabled, `
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
  app.appendChild(anCard);

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
  const asCard = makeSysCard('<svg viewBox="0 0 24 24" fill="none" stroke="#a80f2c" stroke-width="1.6"><path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z"/><path d="M9 12l2 2 4-4"/></svg>', 'Antispam', 'Flood &amp; cross-channel spam detection', null, `
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
  app.appendChild(asCard);

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

  // ---------------- Coming soon ----------------
  app.appendChild(el(`
    <div class="panel" style="opacity:0.5;">
      <div class="panel-head"><h2>Moderation, Tickets, Verification &amp; more</h2></div>
      <div class="soon-note">Coming in a future update — for now, configure these with commands in Discord.</div>
    </div>
  `));
}

async function boot() {
  const meRes = await api('/api/me');
  const me = await meRes.json();
  renderNav(me);
  if (!me.logged_in) { renderLogin(); return; }

  const parts = window.location.pathname.split('/').filter(Boolean);
  if (parts.length === 2 && parts[0] === 'dashboard') {
    renderGuildEditor(parts[1]);
  } else {
    renderGuildPicker(me);
  }
}
boot();
</script>
</body>
</html>"""
