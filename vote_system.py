"""
VALLENT EXS — Vote System (top.gg webhook + reward)
======================================================
Self-contained, same design as ticket_types.py / antinuke.py — never
imports anything from vallent.py. This module only knows how to (1)
verify & parse top.gg's vote webhook, and (2) track a vote ledger as
plain data. vallent.py wires it into its own web server, config file,
and XP boost system — this file never touches bot/cfg state directly.

How top.gg webhooks actually work
----------------------------------
On your bot's top.gg page → Webhooks tab, you configure:
  Webhook URL:   http://YOUR_PUBLIC_HOST:PORT/topgg/vote
  Authorization: any secret string you make up yourself

Every time someone votes, top.gg sends a POST to that URL with an
`Authorization` header (must match what you set above) and a JSON body
like `{"user": "123456789012345678", "type": "upvote"}`. `type` is
`"test"` when you hit top.gg's own "Test" button in the dashboard —
those pings are acknowledged but never grant a reward.

For top.gg to actually reach that URL, your bot's host needs to expose
that port publicly (a VPS with the port opened/forwarded, a platform
like Railway/Render that gives you a public URL + PORT env var, a
reverse proxy, a tunnel like Cloudflare Tunnel/ngrok, etc). If you're
not sure what your host supports, that's a hosting question, not a
code one — tell your bot dev (or Claude) what you're hosting on and
go from there.

Reward shape
------------
Every confirmed real vote is worth a flat +10% XP multiplier for 20
minutes (BOOST_MINUTES / BOOST_MULTIPLIER below) — vallent.py grants
this through its own existing xp_boost system, this module just tells
it a vote happened and keeps the vote ledger (total votes / streak /
last vote time) so commands can show useful status.
"""

import datetime
import logging
from typing import Awaitable, Callable, Optional

from aiohttp import web

log = logging.getLogger("vote_system")

VOTE_COOLDOWN_HOURS = 12   # top.gg's own per-user vote cooldown
BOOST_MINUTES        = 20
BOOST_MULTIPLIER     = 1.10


def record_vote(votes: dict, uid: str) -> dict:
    """Update the vote ledger for a user after a CONFIRMED real vote.
    `votes` is the caller's own persisted dict (e.g. cfg['votes']) —
    this only mutates it in place; saving it to disk is the caller's
    job, same pattern as everywhere else in this codebase. Returns the
    updated entry for convenience."""
    now   = datetime.datetime.now(datetime.timezone.utc)
    entry = votes.setdefault(uid, {"total_votes": 0, "last_vote": None, "streak": 0})
    last  = entry.get("last_vote")
    streak_alive = False
    if last:
        try:
            last_dt = datetime.datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
            # Streak survives if this vote lands within ~36h of the last one
            # (12h cooldown + slack for a late vote); otherwise it resets to 1.
            streak_alive = (now - last_dt) <= datetime.timedelta(hours=36)
        except Exception:
            streak_alive = False
    entry["streak"]       = entry.get("streak", 0) + 1 if streak_alive else 1
    entry["total_votes"]  = entry.get("total_votes", 0) + 1
    entry["last_vote"]    = now.isoformat()
    return entry


def next_vote_time(votes: dict, uid: str) -> Optional[datetime.datetime]:
    """When this user is next allowed to vote (top.gg's cooldown), or
    None if they've never voted or are already clear to vote again."""
    entry = votes.get(uid)
    if not entry or not entry.get("last_vote"):
        return None
    try:
        last_dt = datetime.datetime.fromisoformat(entry["last_vote"])
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None
    ready_at = last_dt + datetime.timedelta(hours=VOTE_COOLDOWN_HOURS)
    now = datetime.datetime.now(datetime.timezone.utc)
    return ready_at if ready_at > now else None


def build_webhook_app(auth_secret: str, on_vote: Callable[[int], Awaitable[None]], app: Optional[web.Application] = None) -> web.Application:
    """Build (or add routes to) the aiohttp web app that receives top.gg's
    vote webhook. `on_vote(user_id)` is awaited for every CONFIRMED real
    vote (`type == "upvote"`) — vallent.py supplies this callback to grant
    the actual XP boost + save config, so this module stays free of any
    bot/cfg dependency. `type == "test"` pings are answered 200 OK but
    never call `on_vote`, so hitting top.gg's dashboard "Test" button
    can't be used to farm free boosts.

    Pass an existing `app` (e.g. one the dashboard module already built
    routes onto) to add this route to it instead of creating a second
    aiohttp app — Railway (and most hosts) only expose one port per
    service, so the vote webhook and the dashboard have to share a single
    app/port rather than each trying to bind their own."""
    app = app if app is not None else web.Application()

    async def handle_vote(request: web.Request) -> web.Response:
        got = request.headers.get("Authorization")
        if got != auth_secret:
            if got is None:
                # Dump every header top.gg actually sent (names + values —
                # these are all top.gg's own request headers, never our
                # secret, so safe to log in full). If top.gg has moved to
                # a signature-based scheme (e.g. a header carrying an HMAC
                # signature instead of the raw secret), this is what will
                # reveal it — the raw secret alone can't reproduce that
                # signature, so we'd need to switch this handler to verify
                # a signature instead of a straight string match.
                all_headers = {k: v for k, v in request.headers.items()}
                log.warning(
                    "Rejected a top.gg webhook call: no Authorization header was sent at all. "
                    f"Full headers received instead: {all_headers}"
                )
            elif got.strip() == (auth_secret or "").strip():
                log.warning(
                    "Rejected a top.gg webhook call: Authorization matched after trimming whitespace — "
                    "check for a stray leading/trailing space on either side (top.gg's field or the env var)."
                )
            else:
                log.warning(
                    f"Rejected a top.gg webhook call: Authorization header present ({len(got)} chars) but "
                    f"didn't match the configured secret ({len(auth_secret or '')} chars) — "
                    "make sure both sides have the EXACT same value."
                )
            return web.Response(status=401, text="unauthorized")
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="bad json")

        uid_raw   = payload.get("user")
        vote_type = payload.get("type")
        if not uid_raw:
            return web.Response(status=400, text="missing user")
        if vote_type == "test":
            return web.Response(status=200, text="ok (test ping — no reward given)")

        try:
            await on_vote(int(uid_raw))
        except Exception:
            log.exception("vote_system on_vote callback raised")
            return web.Response(status=500, text="internal error")
        return web.Response(status=200, text="ok")

    app.router.add_post("/topgg/vote", handle_vote)
    return app
