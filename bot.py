import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import random
import threading
from telethon import TelegramClient, Button, events
from telethon.errors import FloodWaitError, PeerFloodError, SessionPasswordNeededError, UserPrivacyRestrictedError
from telethon.sessions import StringSession

# =========================================================================
# MULTI-ACCOUNT VIP BOT (ZERO-DUPLICATE EDITION):
# 1. Outreach Accounts (➕ Add Number):
#    - Watch configured groups & queue users
#    - Send Outreach DM + Direct Group Reply (with Sent by account & Delete Timer)
#    - Have their OWN separate Incoming DM Reply message ("💬 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠 𝗥𝗲𝗽𝗹𝘆")
#      when a group user replies back to the outreach account in DM!
# 2. Personal Auto DM Account (🤖 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠):
#    - Separate OTP login for your personal/dedicated ID
#    - NEVER touches groups
#    - Has its OWN separate Incoming DM message ("🤖 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠")
# 3. Duplicate Prevention:
#    - Strict event & supervisor lock so NO message ever sends twice!
# =========================================================================
api_id = int(os.getenv("TG_API_ID", "32946015"))
api_hash = os.getenv("TG_API_HASH", "318e71a31d57a9d0fa0b9c055ba16ede").strip()

LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN", "9238275421:AAGfzrIaiRR4vADpdnRWB4tVDUtKZhz39b4").strip()
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID", "8731314485"))

ENABLE_HEALTH_SERVER = os.getenv("ENABLE_HEALTH_SERVER", "1").strip() == "1"
PORT = int(os.getenv("PORT", "8080"))

DEFAULT_DM_MESSAGE = (
    "FREE LIKE KE LIYE YE BOT TRY KARO FF ME FREE LIKE DETA HA 220 MAST HA\n\n"
    "https://t.me/Likeandcc8_bot?start=7161571409"
)

DEFAULT_OUTREACH_INCOMING_REPLY = (
    "Bhai link par click karke start karo, free likes mil jayenge! 🔥"
)

DEFAULT_PERSONAL_AUTO_DM = (
    "Hello! Abhi busy hoon, thodi der mein reply karta hoon. 👍"
)

DEFAULT_GROUP_REPLY_MESSAGE = (
    "Bhai DM check karo, message bhej diya hai!"
)

CONFIG_FILE = os.getenv("CONFIG_FILE", "bot_config.json")
SAVED_IDS_FILE = os.getenv("SAVED_IDS_FILE", "username_ids.json")
SENT_FILE = os.getenv("SENT_FILE", "sent_users.json")
SKIPPED_FILE = os.getenv("SKIPPED_FILE", "skipped_users.json")
ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", "added_accounts.json")

DEFAULT_CONFIG = {
    "groups": ["@FreeFireLikes69", "@noobnation69"],
    "min_delay": 75,
    "max_delay": 150,
    "group_delete_seconds": 10,
    "daily_limit": 20,
    "group_reply_daily_limit": 20,
    "paused": False,
    "today_date": datetime.now().strftime("%Y-%m-%d"),
    "today_sent": 0,
    "today_dm_sent": 0,
    "today_group_sent": 0,
    "today_attempts": 0,
    "today_by_group": {},
    "flood_until": 0,
    "custom_dm_message": DEFAULT_DM_MESSAGE,                     # 1. Group outreach DM
    "outreach_dm_reply_message": DEFAULT_OUTREACH_INCOMING_REPLY, # 2. Outreach ID incoming DM reply
    "outreach_dm_reply_enabled": True,
    "personal_auto_dm_message": DEFAULT_PERSONAL_AUTO_DM,        # 3. Personal ID incoming DM reply
    "personal_auto_dm_enabled": True,
    "auto_dm_account": {},                                       # {"phone": ..., "session": ..., "label": ...}
    "group_reply_message": DEFAULT_GROUP_REPLY_MESSAGE,          # 4. Group direct reply text
    "dm_reply_account_phone": "",
}


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


config = load_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
for k, v in DEFAULT_CONFIG.items():
    config.setdefault(k, v)

try:
    config["daily_limit"] = max(1, int(config.get("daily_limit", 20)))
except Exception:
    config["daily_limit"] = 20

try:
    config["group_reply_daily_limit"] = max(1, int(config.get("group_reply_daily_limit", 20)))
except Exception:
    config["group_reply_daily_limit"] = 20

try:
    config["group_delete_seconds"] = max(0, int(config.get("group_delete_seconds", 10)))
except Exception:
    config["group_delete_seconds"] = 10

if not (config.get("custom_dm_message") or "").strip():
    config["custom_dm_message"] = DEFAULT_DM_MESSAGE
if not (config.get("outreach_dm_reply_message") or "").strip():
    config["outreach_dm_reply_message"] = DEFAULT_OUTREACH_INCOMING_REPLY
if not (config.get("personal_auto_dm_message") or "").strip():
    config["personal_auto_dm_message"] = (config.get("auto_dm_message") or DEFAULT_PERSONAL_AUTO_DM).strip()
if not (config.get("group_reply_message") or "").strip():
    config["group_reply_message"] = DEFAULT_GROUP_REPLY_MESSAGE
if not isinstance(config.get("auto_dm_account"), dict):
    config["auto_dm_account"] = {}

normalized_groups = []
for g in config.get("groups", []):
    g_clean = str(g).replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").strip()
    if g_clean and not g_clean.lstrip("-").isdigit() and not g_clean.startswith("@"):
        g_clean = "@" + g_clean
    if g_clean and g_clean not in normalized_groups:
        normalized_groups.append(g_clean)
if "@noobnation69" not in [x.lower() for x in normalized_groups]:
    normalized_groups.append("@noobnation69")
config["groups"] = normalized_groups
save_json(CONFIG_FILE, config)

saved_username_ids = load_json(SAVED_IDS_FILE, [])
if not isinstance(saved_username_ids, list):
    saved_username_ids = []
save_json(SAVED_IDS_FILE, saved_username_ids)

sent_users = set(load_json(SENT_FILE, []))
save_json(SENT_FILE, list(sent_users))

skipped_users = set(load_json(SKIPPED_FILE, []))
save_json(SKIPPED_FILE, list(skipped_users))

added_accounts = load_json(ACCOUNTS_FILE, [])
if not isinstance(added_accounts, list):
    added_accounts = []

log_session_name = os.getenv("LOG_SESSION_NAME", "log_bot_session")
client_kwargs = {
    "connection_retries": 10,
    "retry_delay": 4,
    "timeout": 20,
    "auto_reconnect": True,
}
log_bot = TelegramClient(log_session_name, api_id, api_hash, **client_kwargs)

dm_queue = asyncio.Queue()
queued_user_ids = set()
log_target_entity = None

# Deduplication guards to guarantee nothing ever runs twice
replied_outreach_dm_keys = set()
replied_personal_dm_keys = set()
processed_admin_msg_ids = set()
processed_group_msg_keys = set()
attached_client_phones = set()
worker_started = False

active_clients = {}          # Outreach accounts (Group + Outreach DM + Outreach DM Reply)
personal_dm_client_info = None  # {"client": TelegramClient, "phone": str, "label": str}
pending_logins = {}
wizard_states = {}

live_task_state = {
    "active_user": None,
    "active_uid": None,
    "active_group": None,
    "active_account": None,
    "eta_timestamp": 0.0,
    "total_delay": 0,
    "last_event": "𝗕𝗼𝘁 𝘀𝘁𝗮𝗿𝘁 𝗵𝗼 𝗿𝗮𝗵𝗮 𝗵𝗮𝗶...",
    "last_updated": datetime.now().strftime("%H:%M:%S"),
}


def set_live_status(
    last_event: str,
    active_user: str = None,
    active_uid=None,
    active_group: str = None,
    active_account: str = None,
    eta_timestamp: float = 0.0,
    total_delay: int = 0,
):
    live_task_state["last_event"] = last_event
    live_task_state["active_user"] = active_user
    live_task_state["active_uid"] = active_uid
    live_task_state["active_group"] = active_group
    live_task_state["active_account"] = active_account
    live_task_state["eta_timestamp"] = eta_timestamp
    live_task_state["total_delay"] = total_delay
    live_task_state["last_updated"] = datetime.now().strftime("%H:%M:%S")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - VIP Bot Running")

    def log_message(self, format, *args):
        return


def start_health_server():
    if not ENABLE_HEALTH_SERVER:
        return
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print(f"[Health] HTTP server listening on port {PORT}")
    except Exception as e:
        print(f"[Health] Could not start HTTP server: {e}")


def is_non_contact_limit_error(err: Exception) -> bool:
    msg = str(err).lower()
    return (
        isinstance(err, PeerFloodError)
        or "account is limited" in msg
        or "spam-reported" in msg
        or "cannot message non-contacts" in msg
        or "peer_flood" in msg
    )


def refresh_daily_counter():
    today = datetime.now().strftime("%Y-%m-%d")
    if config.get("today_date") != today:
        config["today_date"] = today
        config["today_sent"] = 0
        config["today_dm_sent"] = 0
        config["today_group_sent"] = 0
        config["today_attempts"] = 0
        config["today_by_group"] = {}
        save_json(CONFIG_FILE, config)
    else:
        config.setdefault("today_dm_sent", 0)
        config.setdefault("today_group_sent", 0)
        if not isinstance(config.get("today_by_group"), dict):
            config["today_by_group"] = {}
        save_json(CONFIG_FILE, config)


def record_delivery_stats(group_label: str, dm_ok: bool, group_reply_ok: bool):
    refresh_daily_counter()
    config["today_attempts"] = int(config.get("today_attempts", 0)) + 1
    if dm_ok:
        config["today_dm_sent"] = int(config.get("today_dm_sent", 0)) + 1
    if group_reply_ok:
        config["today_group_sent"] = int(config.get("today_group_sent", 0)) + 1
    if dm_ok or group_reply_ok:
        config["today_sent"] = int(config.get("today_sent", 0)) + 1
        by_grp = config.setdefault("today_by_group", {})
        by_grp[group_label] = int(by_grp.get(group_label, 0)) + 1
    save_json(CONFIG_FILE, config)


def format_group_breakdown() -> str:
    by_grp = config.get("today_by_group", {})
    if not by_grp:
        return "  • 𝗔𝗮𝗷 𝗮𝗯𝗵𝗶 𝗸𝗶𝘀𝗶 𝗴𝗿𝗼𝘂𝗽 𝘀𝗲 𝗱𝗲𝗹𝗶𝘃𝗲𝗿𝘆 𝗻𝗮𝗵𝗶 𝗵𝘂𝗶"
    return "\n".join([f"  • <code>{grp}</code>: <b>{cnt}</b>" for grp, cnt in by_grp.items()])


def parse_chat_target(raw: str):
    raw = raw.strip()
    if "t.me/" in raw:
        raw = raw.split("t.me/", 1)[1].split("/", 1)[0].split("?", 1)[0].strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    if not raw.startswith("@"):
        return "@" + raw
    return raw


def is_admin(event) -> bool:
    return event.chat_id == LOG_CHAT_ID or event.sender_id == LOG_CHAT_ID


async def resolve_log_target():
    global log_target_entity
    try:
        log_target_entity = await log_bot.get_input_entity(LOG_CHAT_ID)
    except Exception:
        log_target_entity = LOG_CHAT_ID


async def send_log(text: str, also_print: bool = True):
    global log_target_entity
    if also_print:
        clean_console = text.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
        print(clean_console)
    try:
        target = log_target_entity if log_target_entity is not None else LOG_CHAT_ID
        await log_bot.send_message(target, text, parse_mode="html")
    except Exception:
        try:
            await resolve_log_target()
            await log_bot.send_message(log_target_entity, text, parse_mode="html")
        except Exception as e2:
            print(f"[Log Error] {e2}")


def get_dm_message_text() -> str:
    custom = (config.get("custom_dm_message") or "").strip()
    return custom if custom else DEFAULT_DM_MESSAGE


def get_outreach_dm_reply_text() -> str:
    txt = (config.get("outreach_dm_reply_message") or "").strip()
    return txt if txt else DEFAULT_OUTREACH_INCOMING_REPLY


def get_personal_auto_dm_text() -> str:
    txt = (config.get("personal_auto_dm_message") or "").strip()
    return txt if txt else DEFAULT_PERSONAL_AUTO_DM


def get_group_reply_text() -> str:
    txt = (config.get("group_reply_message") or "").strip()
    return txt if txt else DEFAULT_GROUP_REPLY_MESSAGE


def get_delete_timer_seconds() -> int:
    try:
        return max(0, int(config.get("group_delete_seconds", 10)))
    except Exception:
        return 10


def record_username_id(uid: int, username: str, name: str, group_label: str):
    if not username or not username.startswith("@"):
        return
    for item in saved_username_ids:
        if item.get("id") == uid or item.get("username", "").lower() == username.lower():
            return
    saved_username_ids.append({
        "id": uid,
        "username": username,
        "name": name,
        "group": group_label,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_json(SAVED_IDS_FILE, saved_username_ids)


# Classic VIP Bottom Keyboard (Distinct buttons for Outreach vs Personal Auto DM)
def bottom_ui_keyboard():
    return [
        [Button.text("➕ 𝗔𝗱𝗱 𝗡𝘂𝗺𝗯𝗲𝗿", resize=True), Button.text("👥 𝗔𝗱𝗱 𝗚𝗿𝗼𝘂𝗽", resize=True)],
        [Button.text("✉️ 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠", resize=True), Button.text("💬 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗥𝗲𝗽𝗹𝘆", resize=True)],
        [Button.text("🤖 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠", resize=True), Button.text("📢 𝗚𝗿𝗼𝘂𝗽 𝗥𝗲𝗽𝗹𝘆 𝗠𝘀𝗴", resize=True)],
        [Button.text("🗑️ 𝗗𝗲𝗹𝗲𝘁𝗲 𝗧𝗶𝗺𝗲𝗿", resize=True), Button.text("⏱️ 𝗦𝗲𝗻𝗱 𝗧𝗶𝗺𝗲𝗿", resize=True)],
        [Button.text("🎯 𝗗𝗠 𝗟𝗶𝗺𝗶𝘁", resize=True), Button.text("📌 𝗥𝗲𝗽𝗹𝘆 𝗟𝗶𝗺𝗶𝘁", resize=True)],
        [Button.text("📣 𝗔𝗱𝗺𝗶𝗻 𝗠𝘀𝗴", resize=True), Button.text("📱 𝗔𝗰𝗰𝗼𝘂𝗻𝘁𝘀", resize=True)],
        [Button.text("🆔 𝗦𝗮𝘃𝗲𝗱 𝗜𝗗𝘀", resize=True), Button.text("📊 𝗩𝗜𝗣 𝗦𝘁𝗮𝘁𝘂𝘀", resize=True)],
    ]


# ---------------- MULTI-ACCOUNT HANDLERS (STRICT DEDUPLICATION) ----------------

async def attach_outreach_handlers(client: TelegramClient, phone: str, label: str):
    """Outreach ID: Watches groups + Replies to users who DM this outreach ID with Outreach Reply Msg."""
    guard_key = ("outreach", phone)
    if guard_key in attached_client_phones:
        return
    attached_client_phones.add(guard_key)

    @client.on(events.NewMessage())
    async def _on_group_msg(event):
        if event.out or not event.is_group:
            return
        # Prevent duplicate queueing if multiple outreach accounts are in the same group
        msg_dedup = (event.chat_id, event.id)
        if msg_dedup in processed_group_msg_keys:
            return
        chat = await event.get_chat()
        if not is_watched_group(chat):
            return
        sender = await event.get_sender()
        if not sender or getattr(sender, "bot", False):
            return

        processed_group_msg_keys.add(msg_dedup)
        if len(processed_group_msg_keys) > 5000:
            processed_group_msg_keys.clear()

        uname = f"@{sender.username}" if getattr(sender, "username", None) else ""
        group_label = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)
        if uname:
            record_username_id(sender.id, uname, sender.first_name or "No Name", group_label)

        if sender.id in sent_users or sender.id in skipped_users or sender.id in queued_user_ids:
            return
        queued_user_ids.add(sender.id)
        user_info = {
            "id": sender.id,
            "chat_id": event.chat_id,
            "message_id": event.id,
            "name": sender.first_name or "No Name",
            "username": uname or "No Username",
            "group": group_label,
        }
        await dm_queue.put(user_info)
        print(f"[Queued via {label}] {user_info['name']} ({user_info['username']} / {user_info['id']}) msg_id={event.id} from {group_label}")

    @client.on(events.NewMessage(incoming=True))
    async def _on_outreach_private_dm(event):
        if not event.is_private or event.out:
            return
        if not config.get("outreach_dm_reply_enabled", True):
            return
        sender = await event.get_sender()
        if not sender or getattr(sender, "bot", False):
            return
        # Deduplicate per (outreach_phone, sender_id) so it NEVER replies twice
        key = ("outreach_dm", phone, sender.id)
        if key in replied_outreach_dm_keys:
            return
        reply_txt = get_outreach_dm_reply_text()
        if not reply_txt:
            return
        replied_outreach_dm_keys.add(key)
        try:
            await event.reply(reply_txt)
            sender_tag = f"@{sender.username}" if getattr(sender, "username", None) else str(sender.id)
            print(f"[Outreach DM Reply via {label}] -> {sender_tag}")
        except Exception as e:
            print(f"[Outreach DM Reply Error on {phone}] {e}")


async def attach_personal_auto_dm_handler(client: TelegramClient, phone: str, label: str):
    """Personal Auto DM ID: NEVER watches or sends in groups; ONLY replies to incoming DMs with Personal Auto DM Msg."""
    guard_key = ("personal", phone)
    if guard_key in attached_client_phones:
        return
    attached_client_phones.add(guard_key)

    @client.on(events.NewMessage(incoming=True))
    async def _on_personal_private_dm(event):
        if not event.is_private or event.out:
            return
        if not config.get("personal_auto_dm_enabled", True):
            return
        sender = await event.get_sender()
        if not sender or getattr(sender, "bot", False):
            return
        key = ("personal_dm", phone, sender.id)
        if key in replied_personal_dm_keys:
            return
        reply_txt = get_personal_auto_dm_text()
        if not reply_txt:
            return
        replied_personal_dm_keys.add(key)
        try:
            await event.reply(reply_txt)
            sender_tag = f"@{sender.username}" if getattr(sender, "username", None) else str(sender.id)
            print(f"[Personal Auto DM via {label}] -> {sender_tag}")
        except Exception as e:
            print(f"[Personal Auto DM Error on {phone}] {e}")


async def connect_stored_account(rec: dict):
    phone = rec.get("phone", "").strip()
    sess_str = rec.get("session", "").strip()
    personal_phone = (config.get("auto_dm_account") or {}).get("phone", "").strip()
    if not phone or not sess_str:
        return None
    if personal_phone and phone == personal_phone:
        return None
    if phone in active_clients and active_clients[phone]["client"].is_connected():
        return active_clients[phone]["client"]
    client = TelegramClient(StringSession(sess_str), api_id, api_hash, **client_kwargs)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return None
    me = await client.get_me()
    uname = f"@{me.username}" if getattr(me, "username", None) else ""
    label = f"{getattr(me, 'first_name', '') or phone} {uname}".strip()
    rec["label"] = label
    active_clients[phone] = {"client": client, "phone": phone, "label": label}
    if not config.get("dm_reply_account_phone"):
        config["dm_reply_account_phone"] = phone
        save_json(CONFIG_FILE, config)
    await attach_outreach_handlers(client, phone, label)
    return client


async def connect_personal_auto_dm_account():
    global personal_dm_client_info
    rec = config.get("auto_dm_account") or {}
    phone = (rec.get("phone") or "").strip()
    sess_str = (rec.get("session") or "").strip()
    if not phone or not sess_str:
        personal_dm_client_info = None
        return None
    if personal_dm_client_info and personal_dm_client_info.get("phone") == phone and personal_dm_client_info["client"].is_connected():
        return personal_dm_client_info["client"]
    client = TelegramClient(StringSession(sess_str), api_id, api_hash, **client_kwargs)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        personal_dm_client_info = None
        return None
    me = await client.get_me()
    uname = f"@{me.username}" if getattr(me, "username", None) else ""
    label = f"{getattr(me, 'first_name', '') or phone} {uname}".strip()
    rec["label"] = label
    config["auto_dm_account"] = rec
    save_json(CONFIG_FILE, config)
    personal_dm_client_info = {"client": client, "phone": phone, "label": label}
    await attach_personal_auto_dm_handler(client, phone, label)
    return client


async def load_all_stored_accounts():
    for rec in added_accounts:
        try:
            await connect_stored_account(rec)
        except Exception as e:
            print(f"[Account Load Error] {rec.get('phone')}: {e}")
    save_json(ACCOUNTS_FILE, added_accounts)
    try:
        await connect_personal_auto_dm_account()
    except Exception as e:
        print(f"[Personal Auto DM Load Error] {e}")


def normalize_phone_input(phone_str: str) -> str:
    phone_clean = "".join(ch for ch in phone_str.strip() if ch.isdigit() or ch == "+")
    if phone_clean.startswith("91") and len(phone_clean) == 12:
        phone_clean = "+" + phone_clean
    elif not phone_clean.startswith("+") and len(phone_clean) == 10:
        phone_clean = "+91" + phone_clean
    elif phone_clean and not phone_clean.startswith("+"):
        phone_clean = "+" + phone_clean
    return phone_clean


async def start_number_otp(chat_id: int, phone_str: str, purpose: str = "outreach"):
    phone_clean = normalize_phone_input(phone_str)

    old_sess = pending_logins.pop(chat_id, None)
    if old_sess and old_sess.get("client"):
        try:
            await old_sess["client"].disconnect()
        except Exception:
            pass

    temp_client = TelegramClient(StringSession(), api_id, api_hash, **client_kwargs)
    await temp_client.connect()
    try:
        sent_code = await temp_client.send_code_request(phone_clean)
    except Exception:
        try:
            await temp_client.disconnect()
        except Exception:
            pass
        raise

    pending_logins[chat_id] = {
        "client": temp_client,
        "phone": phone_clean,
        "phone_code_hash": sent_code.phone_code_hash,
        "purpose": purpose,
    }
    return phone_clean


async def finalize_account_login(chat_id: int, client: TelegramClient, phone_clean: str, purpose: str = "outreach"):
    global personal_dm_client_info, added_accounts
    me = await client.get_me()
    uname = f"@{me.username}" if getattr(me, "username", None) else ""
    label = f"{getattr(me, 'first_name', '') or phone_clean} {uname}".strip()
    session_str = client.session.save()

    if purpose == "personal_auto_dm":
        # Remove from outreach if previously added there so it NEVER sends group messages
        if phone_clean in active_clients:
            try:
                await active_clients.pop(phone_clean)["client"].disconnect()
            except Exception:
                pass
        attached_client_phones.discard(("outreach", phone_clean))
        added_accounts = [rec for rec in added_accounts if rec.get("phone") != phone_clean]
        save_json(ACCOUNTS_FILE, added_accounts)

        if personal_dm_client_info and personal_dm_client_info.get("client"):
            old_p = personal_dm_client_info.get("phone")
            attached_client_phones.discard(("personal", old_p))
            try:
                await personal_dm_client_info["client"].disconnect()
            except Exception:
                pass

        config["auto_dm_account"] = {
            "phone": phone_clean,
            "session": session_str,
            "label": label,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        config["personal_auto_dm_enabled"] = True
        save_json(CONFIG_FILE, config)
        personal_dm_client_info = {"client": client, "phone": phone_clean, "label": label}
        await attach_personal_auto_dm_handler(client, phone_clean, label)
        pending_logins.pop(chat_id, None)
        return label

    # Outreach Account
    updated = False
    for rec in added_accounts:
        if rec.get("phone") == phone_clean:
            rec["session"] = session_str
            rec["label"] = label
            updated = True
            break
    if not updated:
        added_accounts.append({
            "phone": phone_clean,
            "session": session_str,
            "label": label,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    save_json(ACCOUNTS_FILE, added_accounts)

    if phone_clean in active_clients:
        attached_client_phones.discard(("outreach", phone_clean))
        try:
            await active_clients[phone_clean]["client"].disconnect()
        except Exception:
            pass
    active_clients[phone_clean] = {"client": client, "phone": phone_clean, "label": label}
    if not config.get("dm_reply_account_phone"):
        config["dm_reply_account_phone"] = phone_clean
        save_json(CONFIG_FILE, config)
    await attach_outreach_handlers(client, phone_clean, label)
    pending_logins.pop(chat_id, None)
    return label


async def broadcast_admin_message(msg_text: str):
    sent_groups = 0
    for grp in config.get("groups", []):
        target = parse_chat_target(str(grp))
        try:
            await log_bot.send_message(target, msg_text)
            sent_groups += 1
            continue
        except Exception:
            pass
        for acc in active_clients.values():
            try:
                await acc["client"].send_message(target, msg_text)
                sent_groups += 1
                break
            except Exception:
                continue
    return sent_groups


async def delete_group_reply_after_delay(client_obj: TelegramClient, chat_target, msg_id: int, delay_sec: int):
    if not msg_id or delay_sec <= 0:
        return
    await asyncio.sleep(delay_sec)
    try:
        await client_obj.delete_messages(chat_target, [msg_id])
        print(f"[Auto-Delete] Deleted group reply msg_id={msg_id} after {delay_sec}s")
    except Exception as e:
        print(f"[Auto-Delete Error] msg_id={msg_id}: {e}")


# ---------------- CLASSIC VIP UI PANELS ----------------

def build_vip_dashboard_text() -> str:
    dm_lim = int(config.get("daily_limit", 20))
    grp_lim = int(config.get("group_reply_daily_limit", 20))
    del_sec = get_delete_timer_seconds()
    del_txt = f"{del_sec}s" if del_sec > 0 else "OFF"
    out_rep_st = "🟢 ON" if config.get("outreach_dm_reply_enabled", True) else "🔴 OFF"
    per_rep_st = "🟢 ON" if config.get("personal_auto_dm_enabled", True) else "🔴 OFF"

    auto_dm_rec = config.get("auto_dm_account") or {}
    if personal_dm_client_info:
        personal_line = f"🟢 <code>{personal_dm_client_info['label']}</code> ({per_rep_st})"
    elif auto_dm_rec.get("phone"):
        personal_line = f"⚪ <code>{auto_dm_rec.get('label') or auto_dm_rec.get('phone')}</code>"
    else:
        personal_line = "🔴 𝗡𝗼𝘁 𝗦𝗲𝘁"

    return (
        f"╔══════════════════════════╗\n"
        f"     👑 <b>𝗩𝗜𝗣 𝗖𝗢𝗡𝗧𝗥𝗢𝗟 𝗣𝗔𝗡𝗘𝗟</b> 👑\n"
        f"╚══════════════════════════╝\n"
        f"┌ 📡 <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗜𝗗𝘀:</b> <b>{len(active_clients)}</b>/{len(added_accounts)} 𝗔𝗰𝘁𝗶𝘃𝗲\n"
        f"├ 💬 <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠 𝗥𝗲𝗽𝗹𝘆:</b> {out_rep_st} (𝗚𝗿𝗼𝘂𝗽 𝗜𝗗𝘀)\n"
        f"├ 🤖 <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠:</b> {personal_line}\n"
        f"├ 👥 <b>𝗪𝗮𝘁𝗰𝗵𝗲𝗱 𝗚𝗿𝗼𝘂𝗽𝘀:</b> <b>{len(config['groups'])}</b> 𝗚𝗿𝗼𝘂𝗽𝘀\n"
        f"├ 🎯 <b>𝗗𝗮𝗶𝗹𝘆 𝗟𝗶𝗺𝗶𝘁𝘀:</b> 𝗗𝗠 <b>{dm_lim}</b> | 𝗥𝗲𝗽𝗹𝘆 <b>{grp_lim}</b>\n"
        f"├ ⏱️ <b>𝗦𝗲𝗻𝗱 𝗧𝗶𝗺𝗲𝗿:</b> <b>{config['min_delay']}s – {config['max_delay']}s</b>\n"
        f"└ 🗑️ <b>𝗗𝗲𝗹𝗲𝘁𝗲 𝗧𝗶𝗺𝗲𝗿:</b> <b>{del_txt}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>𝗡𝗶𝗰𝗵𝗲 𝗸𝗲 𝗩𝗜𝗣 𝗯𝘂𝘁𝘁𝗼𝗻𝘀 𝘀𝗲 𝗮𝗹𝗮𝗴-𝗮𝗹𝗮𝗴 𝘀𝗲𝘁 𝗸𝗮𝗿𝗲𝗶𝗻:</i>"
    )


async def show_outreach_dm_reply_panel(event):
    st = "🟢 ON" if config.get("outreach_dm_reply_enabled", True) else "🔴 OFF"
    curr_msg = get_outreach_dm_reply_text()
    btns = [
        [Button.inline("✏️ 𝗦𝗲𝘁 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠 𝗥𝗲𝗽𝗹𝘆", b"outreach_rep_set")],
        [Button.inline(f"🔁 𝗧𝗼𝗴𝗴𝗹𝗲 ({st})", b"outreach_rep_toggle")],
    ]
    await event.reply(
        f"╔══════════════════════════╗\n"
        f"   💬 <b>𝗢𝗨𝗧𝗥𝗘𝗔𝗖𝗛 𝗜𝗗 𝗗𝗠 𝗥𝗘𝗣𝗟𝗬</b> 💬\n"
        f"╚══════════════════════════╝\n"
        f"• <b>𝗦𝘁𝗮𝘁𝘂𝘀:</b> {st}\n"
        f"• <b>𝗞𝗮𝗮𝗺:</b> 𝗝𝗮𝗯 𝗴𝗿𝗼𝘂𝗽 𝗺𝗲𝗶𝗻 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝗯𝗵𝗲𝗷𝗻𝗲 𝘄𝗮𝗹𝗲 𝗜𝗗 (➕ 𝗔𝗱𝗱 𝗡𝘂𝗺𝗯𝗲𝗿) 𝗽𝗮𝗿 𝗸𝗼𝗶 𝘂𝘀𝗲𝗿 𝗗𝗠 𝗸𝗮𝗿𝗲𝗴𝗮, 𝘁𝗼𝗵 𝘆𝗲 𝗮𝗹𝗮𝗴 𝗿𝗲𝗽𝗹𝘆 𝗷𝗮𝘆𝗲𝗴𝗮:\n\n"
        f"📩 <b>𝗦𝗮𝘃𝗲𝗱 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗥𝗲𝗽𝗹𝘆:</b>\n"
        f"<code>{curr_msg}</code>",
        parse_mode="html",
        buttons=btns,
    )


async def show_personal_auto_dm_panel(event):
    auto_dm_rec = config.get("auto_dm_account") or {}
    if personal_dm_client_info:
        acc_status = f"🟢 <code>{personal_dm_client_info['label']}</code> (𝗔𝗰𝘁𝗶𝘃𝗲 — 𝗗𝗠 𝗢𝗻𝗹𝘆)"
    elif auto_dm_rec.get("phone"):
        acc_status = f"⚪ <code>{auto_dm_rec.get('label') or auto_dm_rec.get('phone')}</code> (𝗢𝗳𝗳𝗹𝗶𝗻𝗲)"
    else:
        acc_status = "🔴 𝗞𝗼𝗶 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗜𝗗 𝗹𝗼𝗴𝗶𝗻 𝗻𝗮𝗵𝗶 𝗵𝗮𝗶"

    st = "🟢 ON" if config.get("personal_auto_dm_enabled", True) else "🔴 OFF"
    curr_msg = get_personal_auto_dm_text()
    btns = [
        [Button.inline("📲 𝗟𝗼𝗴𝗶𝗻 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗜𝗗 (𝗢𝗧𝗣)", b"personal_dm_login")],
        [Button.inline("✏️ 𝗦𝗲𝘁 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗠𝘀𝗴", b"personal_dm_set")],
        [Button.inline(f"🔁 𝗧𝗼𝗴𝗴𝗹𝗲 ({st})", b"personal_dm_toggle")],
    ]
    await event.reply(
        f"╔══════════════════════════╗\n"
        f"   🤖 <b>𝗣𝗘𝗥𝗦𝗢𝗡𝗔𝗟 𝗔𝗨𝗧𝗢 𝗗𝗠 𝗜𝗗</b> 🤖\n"
        f"╚══════════════════════════╝\n"
        f"• <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗜𝗗:</b> {acc_status}\n"
        f"• <b>𝗦𝘁𝗮𝘁𝘂𝘀:</b> {st}\n"
        f"• <b>𝗞𝗮𝗮𝗺:</b> 𝗜𝘀 𝗜𝗗 𝘀𝗲 𝗴𝗿𝗼𝘂𝗽 𝗺𝗲𝗶𝗻 𝗸𝗼𝗶 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝗻𝗮𝗵𝗶 𝗷𝗮𝘆𝗲𝗴𝗮 — 𝗷𝗮𝗯 𝗸𝗼𝗶 𝗶𝘀 𝗽𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗜𝗗 𝗽𝗮𝗿 𝗗𝗠 𝗸𝗮𝗿𝗲𝗴𝗮 𝘁𝗼𝗵 𝘀𝗶𝗿𝗳 𝘆𝗲 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝗷𝗮𝘆𝗲𝗴𝗮:\n\n"
        f"📩 <b>𝗦𝗮𝘃𝗲𝗱 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠:</b>\n"
        f"<code>{curr_msg}</code>",
        parse_mode="html",
        buttons=btns,
    )


@log_bot.on(events.CallbackQuery())
async def on_callback_handler(event):
    if not is_admin(event):
        await event.answer("Unauthorized", alert=True)
        return
    data = event.data or b""
    chat_id = event.chat_id

    if data == b"outreach_rep_set":
        wizard_states[chat_id] = {"mode": "awaiting_outreach_dm_reply"}
        await event.answer()
        await event.respond(
            "✏️ <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗜𝗗 𝗗𝗠 𝗥𝗲𝗽𝗹𝘆 𝗠𝗲𝘀𝘀𝗮𝗴𝗲</b>\n"
            "𝗚𝗿𝗼𝘂𝗽 𝘄𝗮𝗹𝗲 𝗜𝗗 𝗽𝗮𝗿 𝗷𝗮𝗯 𝗸𝗼𝗶 𝗗𝗠 𝗸𝗮𝗿𝗲 𝘁𝗼𝗵 𝗷𝗼 𝗿𝗲𝗽𝗹𝘆 𝗯𝗵𝗲𝗷𝗻𝗮 𝗵𝗮𝗶 𝘄𝗼 𝗹𝗶𝗸𝗵 𝗸𝗮𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
    elif data == b"outreach_rep_toggle":
        curr = not bool(config.get("outreach_dm_reply_enabled", True))
        config["outreach_dm_reply_enabled"] = curr
        save_json(CONFIG_FILE, config)
        await event.answer(f"Outreach DM Reply {'ON' if curr else 'OFF'}")
        await show_outreach_dm_reply_panel(event)

    elif data == b"personal_dm_login":
        wizard_states[chat_id] = {"mode": "awaiting_personal_phone"}
        await event.answer()
        await event.respond(
            "📲 <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗟𝗼𝗴𝗶𝗻</b>\n"
            "𝗔𝗽𝗻𝗮 𝗽𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗻𝘂𝗺𝗯𝗲𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻 (𝗷𝗮𝗶𝘀𝗲 <code>+919876543210</code>):",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
    elif data == b"personal_dm_set":
        wizard_states[chat_id] = {"mode": "awaiting_personal_dm_msg"}
        await event.answer()
        await event.respond(
            "✏️ <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗠𝗲𝘀𝘀𝗮𝗴𝗲</b>\n"
            "𝗝𝗮𝗯 𝗸𝗼𝗶 𝗮𝗮𝗽𝗸𝗶 𝗽𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗜𝗗 𝗽𝗮𝗿 𝗗𝗠 𝗸𝗮𝗿𝗲 𝘁𝗼𝗵 𝗷𝗼 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝗯𝗵𝗲𝗷𝗻𝗮 𝗵𝗮𝗶 𝘄𝗼 𝗹𝗶𝗸𝗵 𝗸𝗮𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
    elif data == b"personal_dm_toggle":
        curr = not bool(config.get("personal_auto_dm_enabled", True))
        config["personal_auto_dm_enabled"] = curr
        save_json(CONFIG_FILE, config)
        await event.answer(f"Personal Auto DM {'ON' if curr else 'OFF'}")
        await show_personal_auto_dm_panel(event)


async def show_accounts_list(event):
    lines = []
    if added_accounts:
        lines.append("📡 <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗔𝗰𝗰𝗼𝘂𝗻𝘁𝘀 (𝗚𝗿𝗼𝘂𝗽 + 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠):</b>")
        for idx, rec in enumerate(added_accounts, start=1):
            p = rec.get("phone")
            lbl = rec.get("label") or p
            active_mark = "🟢 𝗔𝗰𝘁𝗶𝘃𝗲" if p in active_clients else "⚪ 𝗢𝗳𝗳𝗹𝗶𝗻𝗲"
            lines.append(f"  {idx}. <code>{lbl}</code> — {active_mark}")
    else:
        lines.append("📡 <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗔𝗰𝗰𝗼𝘂𝗻𝘁𝘀:</b> 𝗡𝗼𝗻𝗲 (➕ 𝗔𝗱𝗱 𝗡𝘂𝗺𝗯𝗲𝗿 𝘀𝗲 𝗮𝗱𝗱 𝗸𝗮𝗿𝗲𝗶𝗻)")

    lines.append("")
    auto_dm_rec = config.get("auto_dm_account") or {}
    if personal_dm_client_info:
        lines.append(f"🤖 <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗜𝗗:</b>\n  • <code>{personal_dm_client_info['label']}</code> — 🟢 𝗔𝗰𝘁𝗶𝘃𝗲 (𝗗𝗠 𝗢𝗻𝗹𝘆)")
    elif auto_dm_rec.get("phone"):
        lines.append(f"🤖 <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗜𝗗:</b>\n  • <code>{auto_dm_rec.get('label') or auto_dm_rec.get('phone')}</code> — ⚪ 𝗢𝗳𝗳𝗹𝗶𝗻𝗲")
    else:
        lines.append("🤖 <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗜𝗗:</b> 𝗡𝗼𝘁 𝗦𝗲𝘁 (🤖 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝘀𝗲 𝗹𝗼𝗴𝗶𝗻 𝗸𝗮𝗿𝗲𝗶𝗻)")

    await event.reply(
        f"╔══════════════════════════╗\n"
        f"     📱 <b>𝗖𝗢𝗡𝗡𝗘𝗖𝗧𝗘𝗗 𝗜𝗗𝘀</b> 📱\n"
        f"╚══════════════════════════╝\n" + "\n".join(lines),
        parse_mode="html",
        buttons=bottom_ui_keyboard(),
    )


async def show_saved_ids(event):
    if not saved_username_ids:
        await event.reply(
            "🆔 <b>𝗦𝗮𝘃𝗲𝗱 𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲 𝗜𝗗𝘀 (0)</b>\n𝗔𝗯𝗵𝗶 𝗸𝗶𝘀𝗶 𝘂𝘀𝗲𝗿𝗻𝗮𝗺𝗲 𝘄𝗮𝗹𝗲 𝘂𝘀𝗲𝗿 𝗸𝗶 𝗜𝗗 𝘀𝗮𝘃𝗲 𝗻𝗮𝗵𝗶 𝗵𝘂𝗶 𝗵𝗮𝗶.",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
        return
    recent = saved_username_ids[-25:]
    lines = [f"  • <code>{x.get('username')}</code> — <code>{x.get('id')}</code>" for x in recent]
    await event.reply(
        f"🆔 <b>𝗦𝗮𝘃𝗲𝗱 𝗨𝘀𝗲𝗿𝗻𝗮𝗺𝗲 𝗜𝗗𝘀 (𝗧𝗼𝘁𝗮𝗹: {len(saved_username_ids)})</b>\n" + "\n".join(lines),
        parse_mode="html",
        buttons=bottom_ui_keyboard(),
    )


@log_bot.on(events.NewMessage())
async def on_message_handler(event):
    if event.out or not is_admin(event):
        return

    # Strict message ID deduplication so no command or reply ever runs twice
    msg_uid = (event.chat_id, event.id)
    if msg_uid in processed_admin_msg_ids:
        return
    processed_admin_msg_ids.add(msg_uid)
    if len(processed_admin_msg_ids) > 2000:
        processed_admin_msg_ids.clear()

    text = (event.raw_text or "").strip()
    chat_id = event.chat_id
    dm_lim = int(config.get("daily_limit", 20))
    grp_lim = int(config.get("group_reply_daily_limit", 20))

    if text == "/start":
        wizard_states.pop(chat_id, None)
        await event.reply(
            build_vip_dashboard_text(),
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
        return

    if text in ("➕ 𝗔𝗱𝗱 𝗡𝘂𝗺𝗯𝗲𝗿", "➕ Add Number"):
        wizard_states[chat_id] = {"mode": "awaiting_add_phone"}
        await event.reply(
            "➕ <b>𝗔𝗱𝗱 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗡𝘂𝗺𝗯𝗲𝗿</b>\n𝗚𝗿𝗼𝘂𝗽 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝗯𝗵𝗲𝗷𝗻𝗲 𝘄𝗮𝗹𝗮 𝗻𝘂𝗺𝗯𝗲𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻 (𝗷𝗮𝗶𝘀𝗲 <code>+919876543210</code>):",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
        return

    if text in ("👥 𝗔𝗱𝗱 𝗚𝗿𝗼𝘂𝗽", "👥 Add Group"):
        wizard_states[chat_id] = {"mode": "awaiting_add_group"}
        grp_list = ", ".join([f"<code>{g}</code>" for g in config.get("groups", [])]) or "None"
        await event.reply(
            f"👥 <b>𝗔𝗱𝗱 𝗪𝗮𝘁𝗰𝗵 𝗚𝗿𝗼𝘂𝗽</b>\n𝗖𝘂𝗿𝗿𝗲𝗻𝘁: {grp_list}\n\n𝗡𝗮𝘆𝗮 𝗴𝗿𝗼𝘂𝗽 <code>@username</code> 𝘆𝗮 𝗹𝗶𝗻𝗸 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
        return

    if text in ("✉️ 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠", "✉️ DM Msg"):
        wizard_states[chat_id] = {"mode": "awaiting_custom_dm_text"}
        await event.reply(
            f"✉️ <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠 𝗠𝗲𝘀𝘀𝗮𝗴𝗲 (𝗚𝗿𝗼𝘂𝗽 𝗨𝘀𝗲𝗿𝘀 𝗸𝗼 𝗷𝗮𝘆𝗲𝗴𝗮)</b>\n𝗖𝘂𝗿𝗿𝗲𝗻𝘁:\n<code>{get_dm_message_text()[:350]}</code>\n\n𝗡𝗮𝘆𝗮 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
        return

    if text in ("💬 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗥𝗲𝗽𝗹𝘆",):
        wizard_states.pop(chat_id, None)
        await show_outreach_dm_reply_panel(event)
        return

    if text in ("🤖 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠", "🤖 Auto DM"):
        wizard_states.pop(chat_id, None)
        await show_personal_auto_dm_panel(event)
        return

    if text in ("📢 𝗚𝗿𝗼𝘂𝗽 𝗥𝗲𝗽𝗹𝘆 𝗠𝘀𝗴", "💬 Group Reply Msg"):
        wizard_states[chat_id] = {"mode": "awaiting_group_reply_text"}
        del_sec = get_delete_timer_seconds()
        await event.reply(
            f"📢 <b>𝗚𝗿𝗼𝘂𝗽 𝗥𝗲𝗽𝗹𝘆 𝗠𝗲𝘀𝘀𝗮𝗴𝗲</b> (𝗗𝗲𝗹𝗲𝘁𝗲 𝗧𝗶𝗺𝗲𝗿: <b>{del_sec}s</b>)\n"
            f"𝗖𝘂𝗿𝗿𝗲𝗻𝘁:\n<code>{get_group_reply_text()[:350]}</code>\n\n"
            f"𝗡𝗮𝘆𝗮 𝗴𝗿𝗼𝘂𝗽 𝗿𝗲𝗽𝗹𝘆 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
        return

    if text in ("🗑️ 𝗗𝗲𝗹𝗲𝘁𝗲 𝗧𝗶𝗺𝗲𝗿", "🗑️ Delete Timer"):
        wizard_states[chat_id] = {"mode": "awaiting_delete_timer"}
        curr_del = get_delete_timer_seconds()
        await event.reply(
            f"🗑️ <b>𝗚𝗿𝗼𝘂𝗽 𝗥𝗲𝗽𝗹𝘆 𝗗𝗲𝗹𝗲𝘁𝗲 𝗧𝗶𝗺𝗲𝗿</b>\n"
            f"𝗖𝘂𝗿𝗿𝗲𝗻𝘁: <b>{curr_del} seconds</b> {'(Auto-Delete OFF)' if curr_del == 0 else ''}\n\n"
            f"𝗞𝗶𝘁𝗻𝗲 𝘀𝗲𝗰𝗼𝗻𝗱𝘀 𝗯𝗮𝗮𝗱 𝗴𝗿𝗼𝘂𝗽 𝗿𝗲𝗽𝗹𝘆 𝗱𝗲𝗹𝗲𝘁𝗲 𝗵𝗼 𝘄𝗼 𝗻𝘂𝗺𝗯𝗲𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻 (𝗷𝗮𝗶𝘀𝗲 <code>10</code>, <code>30</code>, 𝘆𝗮 <code>0</code>):",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
        return

    if text in ("⏱️ 𝗦𝗲𝗻𝗱 𝗧𝗶𝗺𝗲𝗿", "⏱️ Timer"):
        wizard_states[chat_id] = {"mode": "awaiting_timer_values"}
        await event.reply(
            f"⏱️ <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗦𝗲𝗻𝗱 𝗧𝗶𝗺𝗲𝗿</b> (𝗖𝘂𝗿𝗿𝗲𝗻𝘁: <b>{config['min_delay']}s – {config['max_delay']}s</b>)\n𝗠𝗶𝗻 𝗮𝘂𝗿 𝗺𝗮𝘅 𝘀𝗲𝗰𝗼𝗻𝗱𝘀 𝗯𝗵𝗲𝗷𝗲𝗶𝗻 (𝗷𝗮𝗶𝘀𝗲 <code>75 150</code>):",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )
        return

    if text in ("🎯 𝗗𝗠 𝗟𝗶𝗺𝗶𝘁", "🎯 DM Limit"):
        wizard_states[chat_id] = {"mode": "awaiting_daily_limit"}
        await event.reply(f"🎯 <b>𝗗𝗠 𝗗𝗮𝗶𝗹𝘆 𝗟𝗶𝗺𝗶𝘁</b> (𝗖𝘂𝗿𝗿𝗲𝗻𝘁: <b>{dm_lim}</b>/day)\n𝗡𝗮𝘆𝗮 𝗻𝘂𝗺𝗯𝗲𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:", parse_mode="html", buttons=bottom_ui_keyboard())
        return

    if text in ("📌 𝗥𝗲𝗽𝗹𝘆 𝗟𝗶𝗺𝗶𝘁", "📌 Reply Limit"):
        wizard_states[chat_id] = {"mode": "awaiting_group_reply_limit"}
        await event.reply(f"📌 <b>𝗚𝗿𝗼𝘂𝗽 𝗥𝗲𝗽𝗹𝘆 𝗗𝗮𝗶𝗹𝘆 𝗟𝗶𝗺𝗶𝘁</b> (𝗖𝘂𝗿𝗿𝗲𝗻𝘁: <b>{grp_lim}</b>/day)\n𝗡𝗮𝘆𝗮 𝗻𝘂𝗺𝗯𝗲𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:", parse_mode="html", buttons=bottom_ui_keyboard())
        return

    if text in ("📣 𝗔𝗱𝗺𝗶𝗻 𝗠𝘀𝗴", "📣 Admin Msg"):
        wizard_states[chat_id] = {"mode": "awaiting_admin_broadcast"}
        await event.reply("📣 𝗝𝗼 𝗮𝗱𝗺𝗶𝗻 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝘀𝗮𝗯 𝘄𝗮𝘁𝗰𝗵𝗲𝗱 𝗴𝗿𝗼𝘂𝗽𝘀 𝗺𝗲𝗶𝗻 𝗯𝗵𝗲𝗷𝗻𝗮 𝗵𝗮𝗶 𝘄𝗼 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:", parse_mode="html", buttons=bottom_ui_keyboard())
        return

    if text in ("📱 𝗔𝗰𝗰𝗼𝘂𝗻𝘁𝘀", "📱 Accounts"):
        await show_accounts_list(event)
        return

    if text in ("🆔 𝗦𝗮𝘃𝗲𝗱 𝗜𝗗𝘀", "🆔 IDs"):
        await show_saved_ids(event)
        return

    if text in ("📊 𝗩𝗜𝗣 𝗦𝘁𝗮𝘁𝘂𝘀", "📊 Status", "/status"):
        await send_status_msg(event)
        return

    if text.startswith("/addgroup"):
        parts = text.split(maxsplit=1)
        if len(parts) >= 2:
            grp = parse_chat_target(parts[1])
            if grp not in config["groups"]:
                config["groups"].append(grp)
                save_json(CONFIG_FILE, config)
            await event.reply(f"✅ 𝗚𝗿𝗼𝘂𝗽 𝗮𝗱𝗱𝗲𝗱: <code>{grp}</code>", parse_mode="html", buttons=bottom_ui_keyboard())
        return

    if text == "/pause":
        config["paused"] = True
        save_json(CONFIG_FILE, config)
        await event.reply("⏸️ 𝗣𝗮𝘂𝘀𝗲𝗱.", parse_mode="html", buttons=bottom_ui_keyboard())
        return

    if text == "/resume":
        config["paused"] = False
        config["flood_until"] = 0
        save_json(CONFIG_FILE, config)
        await event.reply("▶️ 𝗥𝗲𝘀𝘂𝗺𝗲𝗱.", parse_mode="html", buttons=bottom_ui_keyboard())
        return

    wiz = wizard_states.get(chat_id)
    if not wiz:
        return

    mode = wiz.get("mode")

    if mode == "awaiting_add_phone":
        try:
            clean_p = await start_number_otp(chat_id, text, purpose="outreach")
            wizard_states[chat_id] = {"mode": "awaiting_add_code"}
            await event.reply(
                f"📲 <code>{clean_p}</code> 𝗽𝗮𝗿 𝗢𝗧𝗣 𝗯𝗵𝗲𝗷 𝗱𝗶𝘆𝗮 𝗴𝗮𝘆𝗮 𝗵𝗮𝗶.\n𝗧𝗲𝗹𝗲𝗴𝗿𝗮𝗺 𝗺𝗲𝗶𝗻 𝗮𝗮𝘆𝗮 𝗵𝘂𝗮 𝗢𝗧𝗣 𝗰𝗼𝗱𝗲 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:",
                parse_mode="html",
                buttons=bottom_ui_keyboard(),
            )
        except Exception as e:
            wizard_states.pop(chat_id, None)
            await event.reply(f"❌ OTP error: <code>{e}</code>", parse_mode="html", buttons=bottom_ui_keyboard())

    elif mode == "awaiting_personal_phone":
        try:
            clean_p = await start_number_otp(chat_id, text, purpose="personal_auto_dm")
            wizard_states[chat_id] = {"mode": "awaiting_add_code"}
            await event.reply(
                f"📲 <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗜𝗗:</b> <code>{clean_p}</code> 𝗽𝗮𝗿 𝗢𝗧𝗣 𝗯𝗵𝗲𝗷 𝗱𝗶𝘆𝗮 𝗴𝗮𝘆𝗮 𝗵𝗮𝗶.\n𝗢𝗧𝗣 𝗰𝗼𝗱𝗲 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:",
                parse_mode="html",
                buttons=bottom_ui_keyboard(),
            )
        except Exception as e:
            wizard_states.pop(chat_id, None)
            await event.reply(f"❌ OTP error: <code>{e}</code>", parse_mode="html", buttons=bottom_ui_keyboard())

    elif mode == "awaiting_add_code":
        sess = pending_logins.get(chat_id)
        if not sess:
            wizard_states.pop(chat_id, None)
            await event.reply("⚠️ 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗲𝘅𝗽𝗶𝗿𝗲 𝗵𝗼 𝗴𝗮𝘆𝗮. 𝗗𝘂𝗯𝗮𝗿𝗮 𝘁𝗿𝘆 𝗸𝗮𝗿𝗲𝗶𝗻.", parse_mode="html", buttons=bottom_ui_keyboard())
            return
        purpose = sess.get("purpose", "outreach")
        try:
            await sess["client"].sign_in(
                phone=sess["phone"],
                code=text.replace(" ", ""),
                phone_code_hash=sess["phone_code_hash"],
            )
            label = await finalize_account_login(chat_id, sess["client"], sess["phone"], purpose=purpose)
            wizard_states.pop(chat_id, None)
            if purpose == "personal_auto_dm":
                await event.reply(
                    f"✅ <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗜𝗗 𝗟𝗼𝗴𝗴𝗲𝗱 𝗜𝗻:</b> <code>{label}</code>\n"
                    f"• 𝗜𝘀 𝗜𝗗 𝘀𝗲 𝗴𝗿𝗼𝘂𝗽 𝗺𝗲𝗶𝗻 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝗻𝗮𝗵𝗶 𝗷𝗮𝘆𝗲𝗴𝗮.\n"
                    f"• 𝗔𝗯 𝗷𝗮𝗯 𝗸𝗼𝗶 𝗶𝘀 𝗽𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗜𝗗 𝗽𝗮𝗿 𝗗𝗠 𝗸𝗮𝗿𝗲𝗴𝗮 𝘁𝗼𝗵 𝘀𝗶𝗿𝗳 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗺𝗲𝘀𝘀𝗮𝗴𝗲 𝗷𝗮𝘆𝗲𝗴𝗮.",
                    parse_mode="html",
                    buttons=bottom_ui_keyboard(),
                )
            else:
                await event.reply(
                    f"✅ <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗡𝘂𝗺𝗯𝗲𝗿 𝗔𝗱𝗱𝗲𝗱:</b> <code>{label}</code>\n𝗧𝗼𝘁𝗮𝗹 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗜𝗗𝘀: <b>{len(added_accounts)}</b>",
                    parse_mode="html",
                    buttons=bottom_ui_keyboard(),
                )
        except SessionPasswordNeededError:
            wizard_states[chat_id] = {"mode": "awaiting_add_2fa"}
            await event.reply("🔑 𝟮𝗙𝗔 𝗽𝗮𝘀𝘀𝘄𝗼𝗿𝗱 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:", parse_mode="html", buttons=bottom_ui_keyboard())
        except Exception as e:
            await event.reply(f"❌ Code error: <code>{e}</code>\n𝗦𝗮𝗵𝗶 𝗰𝗼𝗱𝗲 𝗯𝗵𝗲𝗷𝗲𝗶𝗻:", parse_mode="html", buttons=bottom_ui_keyboard())

    elif mode == "awaiting_add_2fa":
        sess = pending_logins.get(chat_id)
        if not sess:
            wizard_states.pop(chat_id, None)
            return
        purpose = sess.get("purpose", "outreach")
        try:
            await sess["client"].sign_in(password=text)
            label = await finalize_account_login(chat_id, sess["client"], sess["phone"], purpose=purpose)
            wizard_states.pop(chat_id, None)
            if purpose == "personal_auto_dm":
                await event.reply(
                    f"✅ <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝟮𝗙𝗔 𝗩𝗲𝗿𝗶𝗳𝗶𝗲𝗱:</b> <code>{label}</code>",
                    parse_mode="html",
                    buttons=bottom_ui_keyboard(),
                )
            else:
                await event.reply(
                    f"✅ <b>𝟮𝗙𝗔 𝗩𝗲𝗿𝗶𝗳𝗶𝗲𝗱 & 𝗔𝗱𝗱𝗲𝗱:</b> <code>{label}</code>",
                    parse_mode="html",
                    buttons=bottom_ui_keyboard(),
                )
        except Exception as e:
            await event.reply(f"❌ 2FA error: <code>{e}</code>", parse_mode="html", buttons=bottom_ui_keyboard())

    elif mode == "awaiting_outreach_dm_reply":
        config["outreach_dm_reply_message"] = text
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"✅ <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗜𝗗 𝗗𝗠 𝗥𝗲𝗽𝗹𝘆 𝗦𝗮𝘃𝗲𝗱!</b>\n"
            f"𝗝𝗮𝗯 𝗴𝗿𝗼𝘂𝗽 𝘄𝗮𝗹𝗲 𝗜𝗗 𝗽𝗮𝗿 𝗸𝗼𝗶 𝗗𝗠 𝗸𝗮𝗿𝗲𝗴𝗮 𝘁𝗼𝗵 𝘆𝗲 𝗿𝗲𝗽𝗹𝘆 𝗷𝗮𝘆𝗲𝗴𝗮:\n<code>{text}</code>",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )

    elif mode == "awaiting_personal_dm_msg":
        config["personal_auto_dm_message"] = text
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        acc_lbl = personal_dm_client_info["label"] if personal_dm_client_info else "Not Logged In"
        await event.reply(
            f"✅ <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠 𝗠𝗲𝘀𝘀𝗮𝗴𝗲 𝗦𝗮𝘃𝗲𝗱!</b>\n"
            f"• <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗜𝗗:</b> <code>{acc_lbl}</code>\n"
            f"• <b>𝗠𝗲𝘀𝘀𝗮𝗴𝗲:</b>\n<code>{text}</code>",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )

    elif mode == "awaiting_delete_timer":
        digits = text.strip()
        if not digits.isdigit():
            await event.reply("⚠️ 𝗦𝗶𝗿𝗳 𝘀𝗲𝗰𝗼𝗻𝗱𝘀 𝗺𝗲𝗶𝗻 𝗻𝘂𝗺𝗯𝗲𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻 (𝗷𝗮𝗶𝘀𝗲 <code>10</code>, <code>30</code>, 𝘆𝗮 <code>0</code>):", parse_mode="html", buttons=bottom_ui_keyboard())
            return
        sec_val = int(digits)
        config["group_delete_seconds"] = sec_val
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        status_str = f"<b>{sec_val} seconds</b> 𝗯𝗮𝗮𝗱 𝗱𝗲𝗹𝗲𝘁𝗲 𝗵𝗼𝗴𝗮" if sec_val > 0 else "<b>𝗔𝘂𝘁𝗼-𝗗𝗲𝗹𝗲𝘁𝗲 𝗢𝗙𝗙</b>"
        await event.reply(
            f"✅ <b>𝗗𝗲𝗹𝗲𝘁𝗲 𝗧𝗶𝗺𝗲𝗿 𝗨𝗽𝗱𝗮𝘁𝗲𝗱:</b> {status_str}",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )

    elif mode == "awaiting_add_group":
        grp = parse_chat_target(text)
        if grp and grp not in config["groups"]:
            config["groups"].append(grp)
            save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"✅ <b>𝗚𝗿𝗼𝘂𝗽 𝗔𝗱𝗱𝗲𝗱:</b> <code>{grp}</code>\n𝗧𝗼𝘁𝗮𝗹 𝗚𝗿𝗼𝘂𝗽𝘀: <b>{len(config['groups'])}</b>",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )

    elif mode == "awaiting_daily_limit":
        digits = text.strip()
        if not digits.isdigit() or int(digits) < 1:
            await event.reply("⚠️ 𝗦𝗮𝗵𝗶 𝗻𝘂𝗺𝗯𝗲𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻 (𝗷𝗮𝗶𝘀𝗲 <code>20</code> 𝘆𝗮 <code>50</code>):", parse_mode="html", buttons=bottom_ui_keyboard())
            return
        new_lim = int(digits)
        config["daily_limit"] = new_lim
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(f"✅ <b>𝗗𝗠 𝗟𝗶𝗺𝗶𝘁 𝗨𝗽𝗱𝗮𝘁𝗲𝗱:</b> <b>{new_lim}</b>/day", parse_mode="html", buttons=bottom_ui_keyboard())

    elif mode == "awaiting_group_reply_limit":
        digits = text.strip()
        if not digits.isdigit() or int(digits) < 1:
            await event.reply("⚠️ 𝗦𝗮𝗵𝗶 𝗻𝘂𝗺𝗯𝗲𝗿 𝗯𝗵𝗲𝗷𝗲𝗶𝗻 (𝗷𝗮𝗶𝘀𝗲 <code>20</code> 𝘆𝗮 <code>50</code>):", parse_mode="html", buttons=bottom_ui_keyboard())
            return
        new_glim = int(digits)
        config["group_reply_daily_limit"] = new_glim
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(f"✅ <b>𝗚𝗿𝗼𝘂𝗽 𝗥𝗲𝗽𝗹𝘆 𝗟𝗶𝗺𝗶𝘁 𝗨𝗽𝗱𝗮𝘁𝗲𝗱:</b> <b>{new_glim}</b>/day", parse_mode="html", buttons=bottom_ui_keyboard())

    elif mode == "awaiting_timer_values":
        parts = text.split()
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            await event.reply("⚠️ 𝗗𝗼 𝗻𝘂𝗺𝗯𝗲𝗿𝘀 𝗯𝗵𝗲𝗷𝗲𝗶𝗻, 𝗷𝗮𝗶𝘀𝗲: <code>75 150</code>", parse_mode="html", buttons=bottom_ui_keyboard())
            return
        min_d, max_d = int(parts[0]), int(parts[1])
        if min_d < 30 or max_d < min_d:
            await event.reply("⚠️ 𝗠𝗶𝗻 𝗸𝗮𝗺 𝘀𝗲 𝗸𝗮𝗺 𝟯𝟬𝘀 𝗿𝗮𝗸𝗵𝗲𝗶𝗻 𝗮𝘂𝗿 𝗺𝗮𝘅 >= 𝗺𝗶𝗻 𝗵𝗼.", parse_mode="html", buttons=bottom_ui_keyboard())
            return
        config["min_delay"] = min_d
        config["max_delay"] = max_d
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(f"✅ <b>𝗦𝗲𝗻𝗱 𝗧𝗶𝗺𝗲𝗿 𝗨𝗽𝗱𝗮𝘁𝗲𝗱:</b> <b>{min_d}s – {max_d}s</b>", parse_mode="html", buttons=bottom_ui_keyboard())

    elif mode == "awaiting_custom_dm_text":
        config["custom_dm_message"] = text
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"✅ <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠 𝗠𝗲𝘀𝘀𝗮𝗴𝗲 𝗨𝗽𝗱𝗮𝘁𝗲𝗱!</b>\n<code>{text}</code>",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )

    elif mode == "awaiting_group_reply_text":
        config["group_reply_message"] = text
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        del_sec = get_delete_timer_seconds()
        await event.reply(
            f"✅ <b>𝗚𝗿𝗼𝘂𝗽 𝗥𝗲𝗽𝗹𝘆 𝗠𝘀𝗴 𝗨𝗽𝗱𝗮𝘁𝗲𝗱 ({del_sec}s auto-delete):</b>\n<code>{text}</code>",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )

    elif mode == "awaiting_admin_broadcast":
        wizard_states.pop(chat_id, None)
        count_sent = await broadcast_admin_message(text)
        await event.reply(
            f"📣 <b>𝗔𝗱𝗺𝗶𝗻 𝗠𝗲𝘀𝘀𝗮𝗴𝗲 𝗦𝗲𝗻𝘁</b> 𝘁𝗼 <b>{count_sent}</b> / {len(config['groups'])} 𝗴𝗿𝗼𝘂𝗽𝘀.",
            parse_mode="html",
            buttons=bottom_ui_keyboard(),
        )


async def send_status_msg(event):
    refresh_daily_counter()
    now_ts = datetime.now().timestamp()
    eta_ts = float(live_task_state.get("eta_timestamp") or 0.0)
    next_dm_rem = max(0, int(eta_ts - now_ts)) if eta_ts > now_ts else 0
    dm_lim = int(config.get("daily_limit", 20))
    grp_lim = int(config.get("group_reply_daily_limit", 20))
    del_sec = get_delete_timer_seconds()

    if config.get("paused"):
        state = "⏸️ 𝗣𝗮𝘂𝘀𝗲𝗱"
    elif not active_clients:
        state = "🔐 𝗔𝗱𝗱 𝗡𝘂𝗺𝗯𝗲𝗿 𝗣𝗲𝗻𝗱𝗶𝗻𝗴"
    else:
        state = "🟢 𝗥𝘂𝗻𝗻𝗶𝗻𝗴"

    if live_task_state.get("active_user") and next_dm_rem > 0:
        acc_txt = f" via <code>{live_task_state['active_account']}</code>" if live_task_state.get("active_account") else ""
        timer_line = f"<b>{next_dm_rem}s</b> mein <code>{live_task_state['active_user']}</code>{acc_txt}"
    elif dm_queue.qsize() > 0:
        timer_line = f"Queue mein {dm_queue.qsize()} user, agla timer shuru ho raha hai"
    else:
        timer_line = "Queue khaali hai (group message ka intezar)"

    dm_today = int(config.get("today_dm_sent", 0))
    grp_today = int(config.get("today_group_sent", 0))
    out_st = "🟢 ON" if config.get("outreach_dm_reply_enabled", True) else "🔴 OFF"
    per_st = "🟢 ON" if config.get("personal_auto_dm_enabled", True) else "🔴 OFF"
    personal_lbl = f"<code>{personal_dm_client_info['label']}</code>" if personal_dm_client_info else "Not Set"
    grp_tags = ", ".join([f"<code>{g}</code>" for g in config.get("groups", [])])

    await event.reply(
        f"╔══════════════════════════╗\n"
        f"     📊 <b>𝗩𝗜𝗣 𝗟𝗜𝗩𝗘 𝗦𝗧𝗔𝗧𝗨𝗦</b> 📊\n"
        f"╚══════════════════════════╝\n"
        f"• <b>𝗦𝘁𝗮𝘁𝗲:</b> {state}\n"
        f"• <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗜𝗗𝘀:</b> <b>{len(active_clients)}</b>/{len(added_accounts)} | <b>𝗜𝗗𝘀:</b> <b>{len(saved_username_ids)}</b>\n"
        f"• <b>𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗗𝗠 𝗥𝗲𝗽𝗹𝘆:</b> {out_st}\n"
        f"• <b>𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠:</b> {personal_lbl} ({per_st})\n"
        f"• <b>𝗚𝗿𝗼𝘂𝗽𝘀:</b> {grp_tags}\n"
        f"• <b>𝗦𝗲𝗻𝗱 𝗧𝗶𝗺𝗲𝗿:</b> {config['min_delay']}s – {config['max_delay']}s | <b>𝗡𝗲𝘅𝘁:</b> {timer_line}\n"
        f"• <b>𝗗𝗠 𝗔𝗮𝗷:</b> <b>{dm_today}</b>/<b>{dm_lim}</b> | <b>𝗥𝗲𝗽𝗹𝘆 𝗔𝗮𝗷:</b> <b>{grp_today}</b>/<b>{grp_lim}</b>\n"
        f"• <b>𝗗𝗲𝗹𝗲𝘁𝗲 𝗧𝗶𝗺𝗲𝗿:</b> <b>{del_sec}s</b>\n"
        f"• <b>𝗚𝗿𝗼𝘂𝗽-𝘄𝗶𝘀𝗲 𝗔𝗮𝗷:</b>\n{format_group_breakdown()}\n"
        f"• <b>𝗤𝘂𝗲𝘂𝗲:</b> {dm_queue.qsize()}\n"
        f"• <b>𝗔𝗰𝘁𝗶𝘃𝗶𝘁𝘆 ({live_task_state['last_updated']}):</b> {live_task_state['last_event']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="html",
        buttons=bottom_ui_keyboard(),
    )


def is_watched_group(chat) -> bool:
    if not chat or not config["groups"]:
        return False
    chat_id = getattr(chat, "id", None)
    username = getattr(chat, "username", None)

    for g in config["groups"]:
        g_str = str(g).strip()
        if g_str.lstrip("-").isdigit():
            raw_num = int(g_str)
            if chat_id == raw_num or str(chat_id) == g_str.replace("-100", "", 1):
                return True
        else:
            clean_u = g_str.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").lstrip("@").lower()
            if username and username.lower() == clean_u:
                return True
    return False


async def send_direct_group_reply(user_info: dict, preferred_client: TelegramClient, preferred_label: str) -> tuple[bool, str]:
    refresh_daily_counter()
    grp_lim = int(config.get("group_reply_daily_limit", 20))
    if int(config.get("today_group_sent", 0)) >= grp_lim:
        return False, "Reply Limit Full"

    group_target = user_info.get("chat_id") or parse_chat_target(user_info.get("group", ""))
    reply_msg_id = user_info.get("message_id")
    if not group_target:
        return False, "Invalid Group"

    base_reply_text = get_group_reply_text()
    del_sec = get_delete_timer_seconds()

    candidates = []
    if preferred_client:
        candidates.append((preferred_client, preferred_label))
    for acc in active_clients.values():
        if acc["client"] is not preferred_client:
            candidates.append((acc["client"], acc["label"]))

    for client_obj, lbl in candidates:
        try:
            final_reply_text = f"{base_reply_text}\n\n📤 𝗦𝗲𝗻𝘁 𝗯𝘆: {lbl}"
            sent_msg = await client_obj.send_message(group_target, final_reply_text, reply_to=reply_msg_id)
            sent_id = getattr(sent_msg, "id", None)
            if sent_id and del_sec > 0:
                asyncio.create_task(delete_group_reply_after_delay(client_obj, group_target, sent_id, del_sec))
            return True, lbl
        except Exception:
            continue
    return False, "Failed"


async def dm_worker():
    rotation_idx = 0

    while True:
        user_info = await dm_queue.get()
        uid = user_info["id"]
        queued_user_ids.discard(uid)

        try:
            refresh_daily_counter()
            dm_lim = int(config.get("daily_limit", 20))
            grp_lim = int(config.get("group_reply_daily_limit", 20))

            if uid in sent_users or uid in skipped_users:
                continue

            while config.get("paused", False):
                set_live_status("𝗣𝗮𝘂𝘀𝗲𝗱")
                await asyncio.sleep(5)

            dm_available = int(config.get("today_dm_sent", 0)) < dm_lim
            grp_available = int(config.get("today_group_sent", 0)) < grp_lim

            if not dm_available and not grp_available:
                set_live_status(f"DM ({dm_lim}) aur Reply ({grp_lim}) dono limits full")
                continue

            primary_phone = config.get("dm_reply_account_phone", "")
            raw_clients = list(active_clients.values())
            clients_list = sorted(
                raw_clients,
                key=lambda item: (0 if item.get("phone") == primary_phone else 1),
            )
            if not clients_list:
                set_live_status("Koi outreach account nahi — ➕ 𝗔𝗱𝗱 𝗡𝘂𝗺𝗯𝗲𝗿 se add karein")
                await asyncio.sleep(5)
                continue

            num_accs = len(clients_list)
            dm_ok = False
            dm_used_client = None
            dm_used_label = ""
            dm_status_note = "DM Limit Full" if not dm_available else "DM Failed"

            if dm_available:
                for attempt_i in range(num_accs):
                    refresh_daily_counter()
                    dm_lim = int(config.get("daily_limit", 20))
                    if int(config.get("today_dm_sent", 0)) >= dm_lim:
                        dm_status_note = "DM Limit Full"
                        break

                    acc_entry = clients_list[rotation_idx % num_accs]
                    rotation_idx += 1
                    sender_client = acc_entry["client"]
                    sender_label = acc_entry["label"]
                    dm_used_client = sender_client
                    dm_used_label = sender_label

                    delay = random.randint(int(config["min_delay"]), int(config["max_delay"]))
                    eta_ts = datetime.now().timestamp() + delay
                    set_live_status(
                        f"{delay}s mein {sender_label} se {user_info['username']} ko DM + Group Reply",
                        active_user=user_info["username"],
                        active_uid=uid,
                        active_group=user_info["group"],
                        active_account=sender_label,
                        eta_timestamp=eta_ts,
                        total_delay=delay,
                    )
                    await send_log(
                        f"⏱️ <b>𝗧𝗶𝗺𝗲𝗿 ({delay}s):</b> {user_info['name']} ({user_info['username']} / <code>{uid}</code>)\n"
                        f"• 𝗔𝗰𝗰𝗼𝘂𝗻𝘁: <code>{sender_label}</code> | 𝗚𝗿𝗼𝘂𝗽: <code>{user_info['group']}</code>"
                    )

                    for rem in range(delay, 0, -1):
                        if rem == delay or rem in (30, 15, 5, 1):
                            print(f"[Timer] {rem}s mein {user_info['name']} ({uid}) ko {sender_label} se DM + Group Reply hoga...")
                        await asyncio.sleep(1)

                    text_to_send = get_dm_message_text()
                    target_peer = user_info["username"] if user_info["username"].startswith("@") else uid

                    try:
                        await sender_client.send_message(target_peer, text_to_send)
                        dm_ok = True
                        dm_status_note = f"DM Sent ({sender_label})"
                        break
                    except FloodWaitError as fw:
                        wait_sec = min(int(fw.seconds) + 5, 120)
                        dm_status_note = f"FloodWait ({wait_sec}s)"
                        await asyncio.sleep(wait_sec)
                    except UserPrivacyRestrictedError:
                        dm_status_note = "Privacy Restricted"
                    except Exception as e:
                        dm_status_note = "Spam/Non-Contact Limit" if is_non_contact_limit_error(e) else str(e)[:45]
            else:
                acc_entry = clients_list[rotation_idx % num_accs]
                rotation_idx += 1
                dm_used_client = acc_entry["client"]
                dm_used_label = acc_entry["label"]

            grp_reply_ok, grp_reply_info = await send_direct_group_reply(user_info, dm_used_client, dm_used_label)
            record_delivery_stats(user_info["group"], dm_ok=dm_ok, group_reply_ok=grp_reply_ok)

            if dm_ok or grp_reply_ok:
                sent_users.add(uid)
                save_json(SENT_FILE, list(sent_users))
            else:
                skipped_users.add(uid)
                save_json(SKIPPED_FILE, list(skipped_users))

            del_sec = get_delete_timer_seconds()
            del_note = f" ({del_sec}s delete)" if del_sec > 0 else " (no delete)"
            dm_badge = f"✅ Sent via <code>{dm_used_label}</code>" if dm_ok else f"❌ {dm_status_note}"
            grp_badge = f"✅ Replied via <code>{grp_reply_info}</code>{del_note}" if grp_reply_ok else f"❌ {grp_reply_info}"

            set_live_status(f"Done {user_info['username']} | DM: {'OK' if dm_ok else 'NO'} | Reply: {'OK' if grp_reply_ok else 'NO'}")
            await send_log(
                f"📋 <b>𝗥𝗲𝗽𝗼𝗿𝘁:</b> {user_info['name']} ({user_info['username']} / <code>{uid}</code>)\n"
                f"• <b>𝗗𝗠:</b> {dm_badge}\n"
                f"• <b>𝗚𝗿𝗼𝘂𝗽 𝗥𝗲𝗽𝗹𝘆:</b> {grp_badge}\n"
                f"• <b>𝗔𝗮𝗷 𝗗𝗠:</b> <b>{config['today_dm_sent']}</b>/<b>{dm_lim}</b> | <b>𝗥𝗲𝗽𝗹𝘆:</b> <b>{config['today_group_sent']}</b>/<b>{grp_lim}</b>"
            )

        finally:
            dm_queue.task_done()


async def run_supervisor():
    global worker_started
    start_health_server()

    if not worker_started:
        worker_started = True
        asyncio.create_task(dm_worker())

    while True:
        try:
            if not log_bot.is_connected():
                await log_bot.connect()
            await log_bot.start(bot_token=LOG_BOT_TOKEN)
            await resolve_log_target()
            await load_all_stored_accounts()

            set_live_status("𝗥𝗲𝗮𝗱𝘆 — /start 𝘀𝗲 𝗩𝗜𝗣 𝗣𝗮𝗻𝗲𝗹 𝗸𝗵𝗼𝗹𝗲𝗶𝗻")

            personal_lbl = personal_dm_client_info["label"] if personal_dm_client_info else "Not Set"
            await send_log(
                f"👑 <b>𝗩𝗜𝗣 𝗕𝗼𝘁 𝗦𝘁𝗮𝗿𝘁𝗲𝗱 (𝗭𝗲𝗿𝗼-𝗗𝘂𝗽𝗹𝗶𝗰𝗮𝘁𝗲 𝗠𝗼𝗱𝗲)!</b>\n"
                f"• 𝗢𝘂𝘁𝗿𝗲𝗮𝗰𝗵 𝗜𝗗𝘀: <b>{len(active_clients)}</b> | 𝗣𝗲𝗿𝘀𝗼𝗻𝗮𝗹 𝗔𝘂𝘁𝗼 𝗗𝗠: <code>{personal_lbl}</code>\n"
                f"• 𝗚𝗿𝗼𝘂𝗽𝘀: <b>{len(config['groups'])}</b> | 𝗗𝗲𝗹𝗲𝘁𝗲 𝗧𝗶𝗺𝗲𝗿: <b>{get_delete_timer_seconds()}s</b>",
                also_print=True,
            )

            await log_bot.run_until_disconnected()

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Supervisor Restart] {e}")
            set_live_status(f"Restarting in 10s: {str(e)[:60]}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(run_supervisor())
    except KeyboardInterrupt:
        print("Stopped by user.")
