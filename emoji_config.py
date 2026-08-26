"""
VALLENT EXS — Emoji Configuration
===================================
Isi semua ID emoji dari server Discord lu di sini.

Cara ambil ID emoji:
1. Upload emoji ke server Discord lu
2. Di chat Discord ketik  \:nama_emoji:  (pakai backslash)
3. Send — Discord akan tampilkan format lengkapnya: <:nama:1234567890>
4. Copy angka ID-nya, paste di bawah

Format:
  - Emoji biasa  : "<:nama:ID>"
  - Emoji animasi: "<a:nama:ID>"

Contoh:
  BADGE_FOUNDER = "<:founder:1234567890123456>"
  BADGE_STAFF   = "<a:staff:9876543210987654>"   # animated
"""

# ══════════════════════════════════════════════════════════════════
# BADGE EMOJI
# ══════════════════════════════════════════════════════════════════

BADGE_FOUNDER    = "<:owner:1531552182573203486>"   # Emoji untuk badge FOUNDER
BADGE_DEVELOPER  = "<:Dev:1531552304447098950>"   # Emoji untuk badge DEVELOPER
BADGE_MANAGEMENT = "<:emoji_47:1528958972441137202>"   # Emoji untuk badge MANAGEMENT
BADGE_MODERATOR       = "<:emoji_68:1530536974723715222>"   # Emoji untuk badge MODERATOR — isi ID emoji lu di sini
BADGE_SERVER_MANAGER  = "<:emoji_49:1528959014304481311>"   # Emoji untuk badge SERVER MANAGER — isi ID emoji lu di sini
BADGE_STAFF      = "<:emoji_54:1528959142297997332>"   # Emoji untuk badge STAFF
BADGE_PREMIUM    = "<:premium:1528961463094612110>"   # Emoji untuk badge PREMIUM
BADGE_NOPREFIX   = "<:emoji_51:1528919382389035018>"   # Emoji untuk badge NO PREFIX
BADGE_USER       = "<:users:1531551241132441722>"   # Emoji untuk badge USER
BADGE_MOONKEEPER = "<a:emoji_55:1528919570918670396>"   # Emoji untuk badge MOONKEEPER — isi ID emoji lu di sini (fallback: 🌙)

# ══════════════════════════════════════════════════════════════════
# UI / SECTION EMOJI (untuk help, info, dll)
# ══════════════════════════════════════════════════════════════════

# Section headers di !vx help
ICON_MODERATION  = "<:emoji_50:1531718594906292434>"   # Icon untuk section Moderation
ICON_ROLE        = "<:emoji_48:1531718538178334831>"   # Icon untuk section Role & Voice
ICON_INFO        = "<:emoji_66:1531720356065316914>"   # Icon untuk section Info
ICON_TICKET      = "<:emoji_68:1531754780450488624>"   # Icon untuk section Ticket
ICON_LEVEL       = "<:emoji_46:1531718489222414436>"   # Icon untuk section Level & XP
ICON_GIVEAWAY    = "<:emoji_61:1531719361939771532>"   # Icon untuk section Giveaway
ICON_ANTISPAM    = "<:emoji_70:1531721230632353964>"   # Icon untuk section Antispam
ICON_OWNER       = "<:emoji_44:1531718460147503347>"   # Icon untuk section Owner Only
ICON_BOOST       = "<:emoji_54:1531719119441887385>"   # Icon default notifikasi server boost — isi ID emoji boost lu di sini
ICON_ANTINUKE    = "<:emoji_44:1531718440107245588>"   # Icon untuk section & alert Anti-Nuke — isi ID emoji lu di sini
ICON_VERIFICATION = "<:emoji_60:1531719328993378445>"   # Icon untuk section & panel Verifikasi (captcha) — isi ID emoji lu di sini (fallback: 🔐)
ICON_IGNORE      = "<:emoji_52:1531719064161095680>"
ICON_AUTOMOD     = "<:emoji_53:1531719087405793510>"
ICON_AUTORESPONSE = "<:emoji_68:1531722117702221945>"   # Icon untuk section Auto-Response — isi ID emoji lu di sini
ICON_AFK          = "<:emoji_68:1531720475380814008>"   # Icon untuk section & notifikasi AFK — isi ID emoji lu di sini (fallback: 💤)
# Status / result icons
ICON_SUCCESS     = "<:emoji_70:1532220901431578644>"   # Icon sukses (checklist, dll)
ICON_ERROR       = "<:emoji_67:1531720428257542174>"   # Icon error / gagal
ICON_WARNING     = "<:emoji_43:1531718404560388198>"   # Icon warning / peringatan
ICON_LOADING     = "<a:emoji_53:1529240301539954778>"   # Icon loading / proses

# ══════════════════════════════════════════════════════════════════
# BOT STATUS UPDATE ICONS (dipakai command `botstatus` — notif di channel
# status support server: online/maintenance/update/offline/degraded)
# ══════════════════════════════════════════════════════════════════
 
ICON_STATUS_ONLINE      = "<a:Status:1529931214054752427>"   # isi ID emoji lu di sini (fallback: 🟢)
ICON_STATUS_OFFLINE     = "<a:Offline:1529931159549776132>"   # isi ID emoji lu di sini (fallback: 🔴)
ICON_STATUS_MAINTENANCE = "<:yellow_status:1529931730935611526>"   # isi ID emoji lu di sini (fallback: 🟠)
ICON_STATUS_UPDATE      = "<a:online:1529932716529946645>"   # isi ID emoji lu di sini (fallback: 🔵)
ICON_STATUS_DEGRADED    = "<a:Loading:1529932224655527948>"   # isi ID emoji lu di sini (fallback: 🟡)

# ══════════════════════════════════════════════════════════════════
# EMBED BUILDER ICONS (dipakai command `embed` / `/embed` — help menu
# section icon & tombol Send di panel builder)
# ══════════════════════════════════════════════════════════════════
 
ICON_EMBED       = "<:emoji_58:1531719226270810273>"   # Icon untuk section Embed Builder di help menu — isi ID emoji lu di sini (fallback: 🖼️)
ICON_EMBED_SEND  = "<:emoji_76:1532226202872316075>"   # Icon untuk tombol Send di panel /embed — isi ID emoji lu di sini (fallback: ✅)
ICON_COMPONENT   = "<:emoji_73:1532220978581733416>"   # Icon untuk section Message Component Builder (/component) di help menu — isi ID emoji lu di sini (fallback: 🔘)
 
# Profile card icons
ICON_PROFILE     = "<:emoji_52:1528948967314817024>"   # Icon di header profile
ICON_BADGES      = "<a:emoji_47:1528089656783142993>"   # Icon di ALL BADGES
ICON_COMMANDS    = "<a:music_2:1528961515879927949>"   # Icon di Commands Runned
ICON_PREMIUM_TAG = "<a:emoji_52:1529240243167826021>"   # Icon di keterangan premium

# Ticket icons
ICON_TICKET_OPEN  = "<:emoji_53:1528949967207534702>"  # Icon tombol Open Ticket
ICON_TICKET_CLOSE = "<:emoji_53:1528949983645138984>"  # Icon tombol Close Ticket

# Giveaway icons
ICON_GIVEAWAY_REACT = "<a:emoji_81:1535491674191433798>" # Icon reaksi giveaway (default 🎉 kalau kosong)
ICON_GIVEAWAY_PARTICIPANTS = "<:emoji_83:1535492163750862848>"
ICON_WINNER          = "<a:emoji_82:1535491699336421466>" # Icon pengumuman pemenang

# ══════════════════════════════════════════════════════════════════
# HELPER FUNCTION
# ══════════════════════════════════════════════════════════════════

def e(emoji_str: str, fallback: str = "") -> str:
    """
    Return emoji kalau sudah diisi, fallback kalau masih kosong.
    Contoh: e(BADGE_FOUNDER, "👑") → "<:founder:123>" atau "👑"
    """
    return emoji_str if emoji_str.strip() else fallback
