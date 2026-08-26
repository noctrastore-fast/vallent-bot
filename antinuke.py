"""
VALLENT EXS — Anti-Nuke Protection
===================================
Deteksi aksi berbahaya lewat Discord Audit Log secara real-time (event
`on_audit_log_entry_create`, tersedia sejak discord.py 2.4 — gak perlu
polling manual). Begitu ada staff/admin yang tiba-tiba mass-delete channel,
mass-delete role, mass-ban, mass-kick, spam bikin webhook, atau kasih role
Administrator ke sembarangan orang, bot langsung ambil tindakan sebelum
server abis di-nuke total.

Cara kerja deteksi "mass action": tiap aksi dicatat dengan timestamp-nya
per (guild, pelaku, jenis aksi). Kalau jumlah aksi dalam window waktu
tertentu (misal 3x channel delete dalam 15 detik) ngelewatin threshold,
pelaku langsung kena tindakan (strip semua role / kick / ban, tergantung
konfigurasi) dan alert dikirim ke log channel.

Data tracking ini SENGAJA disimpan di memori (bukan config JSON) karena
sifatnya jendela waktu pendek — gak perlu ikut ke-restore kalau bot restart.
"""

import logging
import time
from collections import defaultdict, deque
from typing import Optional

import discord

log = logging.getLogger("antinuke")

# ══════════════════════════════════════════════════════════════════
# THRESHOLD DEFAULT — bisa di-override per guild lewat config
# ══════════════════════════════════════════════════════════════════

DEFAULT_THRESHOLDS = {
    "channel_delete": {"count": 3, "seconds": 15},
    "channel_create": {"count": 5, "seconds": 15},
    "role_delete":    {"count": 3, "seconds": 15},
    "ban":            {"count": 3, "seconds": 15},
    "kick":           {"count": 3, "seconds": 15},
    "webhook_create": {"count": 2, "seconds": 15},
}

# Aksi-aksi ini gak butuh threshold sama sekali — sekali kejadian langsung
# dianggap serangan (misal kasih role Administrator ke orang random).
INSTANT_ACTIONS = {"dangerous_permission"}

ACTION_LABELS = {
    "channel_delete":      "Mass Channel Delete",
    "channel_create":      "Mass Channel Create",
    "role_delete":         "Mass Role Delete",
    "ban":                 "Mass Ban",
    "kick":                "Mass Kick",
    "webhook_create":      "Mass Webhook Create",
    "dangerous_permission": "Dangerous Permission Grant",
}

DEFAULT_PUNISHMENT = "strip_roles"  # strip_roles | kick | ban

# ══════════════════════════════════════════════════════════════════
# TRACKER — sliding window in-memory, per (guild_id, user_id, action)
# ══════════════════════════════════════════════════════════════════

_events: dict = defaultdict(deque)  # key -> deque[timestamp]

def _record_and_check(guild_id: int, user_id: int, action: str, count: int, seconds: int) -> bool:
    """Catat 1 kejadian, buang yang udah expired dari window, return True
    kalau jumlah kejadian dalam window udah nyampe/ngelewatin threshold."""
    key = (guild_id, user_id, action)
    now = time.monotonic()
    dq  = _events[key]
    dq.append(now)
    cutoff = now - seconds
    while dq and dq[0] < cutoff:
        dq.popleft()
    return len(dq) >= count

def reset_tracker(guild_id: int, user_id: int, action: Optional[str] = None):
    """Bersihin tracker — dipakai setelah punishment dijatuhkan biar gak
    langsung ke-trigger ulang gara-gara sisa hitungan lama."""
    if action:
        _events.pop((guild_id, user_id, action), None)
    else:
        for key in list(_events.keys()):
            if key[0] == guild_id and key[1] == user_id:
                _events.pop(key, None)

# ══════════════════════════════════════════════════════════════════
# CLASSIFICATION — audit log entry -> action key internal kita
# ══════════════════════════════════════════════════════════════════

def classify_entry(entry: discord.AuditLogEntry) -> Optional[str]:
    action = entry.action
    if action == discord.AuditLogAction.channel_delete:
        return "channel_delete"
    if action == discord.AuditLogAction.channel_create:
        return "channel_create"
    if action == discord.AuditLogAction.role_delete:
        return "role_delete"
    if action == discord.AuditLogAction.ban:
        return "ban"
    if action == discord.AuditLogAction.kick:
        return "kick"
    if action == discord.AuditLogAction.webhook_create:
        return "webhook_create"
    if action == discord.AuditLogAction.role_create:
        after_perms = getattr(entry.after, "permissions", None)
        if after_perms and after_perms.administrator:
            return "dangerous_permission"
        return None
    if action == discord.AuditLogAction.role_update:
        after_perms = getattr(entry.after, "permissions", None)
        before_perms = getattr(entry.before, "permissions", None)
        if after_perms and after_perms.administrator and not (before_perms and before_perms.administrator):
            return "dangerous_permission"
        return None
    return None

def is_whitelisted(guild: discord.Guild, user_id: int, owner_id: int, whitelist: list) -> bool:
    if user_id == owner_id:
        return True
    if user_id == guild.owner_id:
        return True
    if user_id == guild.me.id:
        return True
    if user_id in whitelist:
        return True
    return False

# ══════════════════════════════════════════════════════════════════
# PUNISHMENT
# ══════════════════════════════════════════════════════════════════

async def punish(guild: discord.Guild, member: discord.Member, punishment: str, reason: str) -> str:
    """Eksekusi hukuman, return string ringkas buat dilaporkan ke log channel."""
    try:
        if punishment == "ban":
            await guild.ban(member, reason=reason, delete_message_seconds=0)
            return "**BANNED**"
        elif punishment == "kick":
            await guild.kick(member, reason=reason)
            return "**KICKED**"
        else:  # strip_roles — default paling aman, gak ngusir orang kalau ternyata false positive
            roles_to_remove = [r for r in member.roles if not r.is_default() and r < guild.me.top_role]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=reason)
            return f"**ROLES STRIPPED** ({len(roles_to_remove)} role dicabut)"
    except discord.Forbidden:
        return "⚠️ GAGAL — bot gak punya izin/role bot lebih rendah dari target"
    except Exception as e:
        log.error(f"Gagal eksekusi punishment anti-nuke: {e}")
        return f"⚠️ GAGAL — {e}"
