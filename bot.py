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
# MULTI-ACCOUNT BOT:
# - DM to user + Direct Reply to that user's group message (reply_to=msg_id)
# - Separate DM Daily Limit & Group Reply Daily Limit (both editable)
# - Separate DM Message & Group Reply Message (both editable)
# - Auto-Reply on incoming DM sends the configured DM Message
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

DEFAULT_GROUP_REPLY_MESSAGE = (
    "Bhai aapka DM check karo, maine aapko message bhej diya hai!"
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
    "daily_limit": 20,              # DM daily limit
    "group_reply_daily_limit": 20,  # Group reply daily limit
    "paused": False,
    "today_date": datetime.now().strftime("%Y-%m-%d"),
    "today_sent": 0,
    "today_dm_sent": 0,
    "today_group_sent": 0,
    "today_attempts": 0,
    "today_by_group": {},
    "flood_until": 0,
    "custom_dm_message": DEFAULT_DM_MESSAGE,
    "group_reply_message": DEFAULT_GROUP_REPLY_MESSAGE,
    "dm_reply_account_phone": "",
    "dm_reply_enabled": True,
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

if not (config.get("custom_dm_message") or "").strip():
    config["custom_dm_message"] = DEFAULT_DM_MESSAGE
if not (config.get("group_reply_message") or "").strip():
    config["group_reply_message"] = DEFAULT_GROUP_REPLY_MESSAGE

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
replied_dm_keys = set()

active_clients = {}
pending_logins = {}
wizard_states = {}

live_task_state = {
    "active_user": None,
    "active_uid": None,
    "active_group": None,
    "active_account": None,
    "eta_timestamp": 0.0,
    "total_delay": 0,
    "last_event": "Bot start ho raha hai...",
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
        self.wfile.write(b"OK - Multi-Account Bot running")

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
        return "Aaj abhi kisi group se delivery nahi hui."
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


def get_group_reply_text() -> str:
    txt = (config.get("group_reply_message") or "").strip()
    return txt if txt else DEFAULT_GROUP_REPLY_MESSAGE


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


def main_menu_buttons():
    return [
        [
            Button.inline("➕ Add Number", data=b"btn_add_number"),
            Button.inline("📱 Accounts List", data=b"btn_accounts"),
        ],
        [
            Button.inline("👥 Add Group", data=b"btn_add_group"),
            Button.inline("🆔 IDs", data=b"btn_ids"),
        ],
        [
            Button.inline("🎯 DM Limit", data=b"btn_daily_limit"),
            Button.inline("📌 Group Reply Limit", data=b"btn_group_reply_limit"),
        ],
        [
            Button.inline("✉️ DM Message", data=b"btn_set_dm_text"),
            Button.inline("💬 Group Reply Msg", data=b"btn_set_group_text"),
        ],
        [
            Button.inline("⏱️ Timer & Change", data=b"btn_timer"),
            Button.inline("🤖 DM Auto-Reply", data=b"btn_dm_reply"),
        ],
        [
            Button.inline("📣 Admin Msg", data=b"btn_admin_msg"),
            Button.inline("📊 Status", data=b"btn_status"),
        ],
    ]


# ---------------- MULTI-ACCOUNT & GROUP MANAGEMENT ----------------

async def attach_group_and_dm_handlers(client: TelegramClient, phone: str, label: str):
    @client.on(events.NewMessage())
    async def _on_group_msg(event):
        if event.out or not event.is_group:
            return
        chat = await event.get_chat()
        if not is_watched_group(chat):
            return
        sender = await event.get_sender()
        if not sender or getattr(sender, "bot", False):
            return
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
            "message_id": event.id,  # Exact group message ID for direct reply
            "name": sender.first_name or "No Name",
            "username": uname or "No Username",
            "group": group_label,
        }
        await dm_queue.put(user_info)
        print(f"[Queued via {label}] {user_info['name']} ({user_info['username']} / {user_info['id']}) msg_id={event.id} from {group_label} | Queue: {dm_queue.qsize()}")

    @client.on(events.NewMessage(incoming=True))
    async def _on_private_dm_reply(event):
        if not event.is_private or event.out:
            return
        if not config.get("dm_reply_enabled", True):
            return
        selected_phone = (config.get("dm_reply_account_phone") or "").strip()
        if selected_phone and selected_phone != phone:
            return
        sender = await event.get_sender()
        if not sender or getattr(sender, "bot", False):
            return
        key = (phone, sender.id)
        if key in replied_dm_keys:
            return
        # Jab koi user DM kare toh wahi configured DM message bhejta hai
        reply_txt = get_dm_message_text()
        if not reply_txt:
            return
        replied_dm_keys.add(key)
        try:
            await event.reply(reply_txt)
        except Exception as e:
            print(f"[DM Auto-Reply Error on {phone}] {e}")


async def connect_stored_account(rec: dict):
    phone = rec.get("phone", "").strip()
    sess_str = rec.get("session", "").strip()
    if not phone or not sess_str:
        return None
    if phone in active_clients:
        return active_clients[phone]
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
    await attach_group_and_dm_handlers(client, phone, label)
    return client


async def load_all_stored_accounts():
    for rec in added_accounts:
        try:
            await connect_stored_account(rec)
        except Exception as e:
            print(f"[Account Load Error] {rec.get('phone')}: {e}")
    save_json(ACCOUNTS_FILE, added_accounts)


async def start_number_otp(chat_id: int, phone_str: str):
    phone_clean = phone_str.strip()
    temp_client = TelegramClient(StringSession(), api_id, api_hash, **client_kwargs)
    await temp_client.connect()
    sent_code = await temp_client.send_code_request(phone_clean)
    pending_logins[chat_id] = {
        "client": temp_client,
        "phone": phone_clean,
        "phone_code_hash": sent_code.phone_code_hash,
    }


async def finalize_account_login(chat_id: int, client: TelegramClient, phone_clean: str):
    me = await client.get_me()
    uname = f"@{me.username}" if getattr(me, "username", None) else ""
    label = f"{getattr(me, 'first_name', '') or phone_clean} {uname}".strip()
    session_str = client.session.save()

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
        try:
            await active_clients[phone_clean]["client"].disconnect()
        except Exception:
            pass
    active_clients[phone_clean] = {"client": client, "phone": phone_clean, "label": label}
    if not config.get("dm_reply_account_phone"):
        config["dm_reply_account_phone"] = phone_clean
        save_json(CONFIG_FILE, config)
    await attach_group_and_dm_handlers(client, phone_clean, label)
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


# ---------------- BOT BUTTONS & INTERACTIVE HANDLERS ----------------

@log_bot.on(events.CallbackQuery)
async def on_callback(event):
    if not is_admin(event):
        await event.answer("Admin only", alert=False)
        return

    data = event.data
    chat_id = event.chat_id
    dm_lim = int(config.get("daily_limit", 20))
    grp_lim = int(config.get("group_reply_daily_limit", 20))

    if data == b"btn_add_number":
        wizard_states[chat_id] = {"mode": "awaiting_add_phone"}
        await event.answer("Phone number bhejein")
        await event.respond(
            "➕ <b>Add Number</b>\n"
            "Naya Telegram number bhejein (jaise <code>+919876543210</code>):",
            parse_mode="html",
        )

    elif data == b"btn_accounts":
        await event.answer("Accounts list")
        if not added_accounts:
            await event.respond(
                "📱 Abhi koi number add nahi hai. <b>Add Number</b> button se number add karein.",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )
            return
        lines = []
        for idx, rec in enumerate(added_accounts, start=1):
            p = rec.get("phone")
            lbl = rec.get("label") or p
            active_mark = "🟢 Active" if p in active_clients else "⚪ Offline"
            ar_mark = " (DM Auto-Reply)" if config.get("dm_reply_account_phone") == p else ""
            lines.append(f"{idx}. <code>{lbl}</code> — {active_mark}{ar_mark}")
        await event.respond(
            f"📱 <b>Added Accounts ({len(added_accounts)}):</b>\n" + "\n".join(lines),
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif data == b"btn_add_group":
        wizard_states[chat_id] = {"mode": "awaiting_add_group"}
        await event.answer("Group username ya link bhejein")
        grp_list = ", ".join([f"<code>{g}</code>" for g in config.get("groups", [])]) or "None"
        await event.respond(
            f"👥 <b>Add Group</b>\n"
            f"Current Groups: {grp_list}\n\n"
            f"Naya group username ya link bhejein (jaise <code>@MyGroup</code> ya <code>https://t.me/MyGroup</code>):",
            parse_mode="html",
        )

    elif data == b"btn_ids":
        await event.answer("Saved Username IDs")
        if not saved_username_ids:
            await event.respond(
                "🆔 <b>Saved Username IDs (0)</b>\nAbhi kisi username wale user ki ID save nahi hui hai.",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )
            return
        recent = saved_username_ids[-25:]
        lines = [f"• <code>{x.get('username')}</code> — ID: <code>{x.get('id')}</code>" for x in recent]
        await event.respond(
            f"🆔 <b>Saved Username IDs (Total: {len(saved_username_ids)})</b>\n" + "\n".join(lines),
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif data == b"btn_daily_limit":
        wizard_states[chat_id] = {"mode": "awaiting_daily_limit"}
        await event.answer("DM daily limit bhejein")
        await event.respond(
            f"🎯 <b>DM Daily Limit Setup</b> (Current: <b>{dm_lim}</b>/day)\n"
            f"1 din mein kitne users ko DM bhejna hai? Number bhejein (jaise <code>20</code>, <code>30</code>, ya <code>50</code>):",
            parse_mode="html",
        )

    elif data == b"btn_group_reply_limit":
        wizard_states[chat_id] = {"mode": "awaiting_group_reply_limit"}
        await event.answer("Group reply limit bhejein")
        await event.respond(
            f"📌 <b>Group Reply Daily Limit Setup</b> (Current: <b>{grp_lim}</b>/day)\n"
            f"1 din mein group mein kitne users ko reply dena hai? Number bhejein (jaise <code>20</code>, <code>30</code>, ya <code>50</code>):",
            parse_mode="html",
        )

    elif data == b"btn_timer":
        wizard_states[chat_id] = {"mode": "awaiting_timer_values"}
        await event.answer("Min aur Max timer bhejein")
        await event.respond(
            f"⏱️ <b>Timer Setup</b> (Current: <b>{config['min_delay']}s – {config['max_delay']}s</b>)\n"
            f"Naya min aur max delay seconds mein bhejein, jaise: <code>75 150</code>",
            parse_mode="html",
        )

    elif data == b"btn_set_dm_text":
        wizard_states[chat_id] = {"mode": "awaiting_custom_dm_text"}
        current_msg = get_dm_message_text()
        await event.answer("DM message bhejein")
        await event.respond(
            f"✉️ <b>Change DM Message (Yahi message DM + Incoming DM Auto-Reply mein jayega)</b>\n"
            f"Current DM Message:\n<code>{current_msg[:350]}</code>\n\n"
            f"Naya DM message likh kar bhejein:",
            parse_mode="html",
        )

    elif data == b"btn_set_group_text":
        wizard_states[chat_id] = {"mode": "awaiting_group_reply_text"}
        current_gmsg = get_group_reply_text()
        await event.answer("Group reply message bhejein")
        await event.respond(
            f"💬 <b>Change Group Reply Message (User ke group message par direct reply)</b>\n"
            f"Current Group Reply:\n<code>{current_gmsg[:350]}</code>\n\n"
            f"Naya group reply message likh kar bhejein:",
            parse_mode="html",
        )

    elif data == b"btn_admin_msg":
        wizard_states[chat_id] = {"mode": "awaiting_admin_broadcast"}
        await event.answer("Admin message bhejein")
        await event.respond(
            "📣 <b>Admin Message</b>\n"
            "Jo admin message aap sab watched groups mein bhejna chahte hain wo yahan likh kar bhejein:",
            parse_mode="html",
        )

    elif data == b"btn_dm_reply":
        await event.answer("Account chunein jisme DM auto-reply ON/OFF karna hai")
        if not added_accounts:
            await event.respond(
                "⚠️ Pehle <b>Add Number</b> se kam se kam 1 number add karein.",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )
            return
        acc_btns = []
        for rec in added_accounts:
            p = rec.get("phone")
            lbl = rec.get("label") or p
            acc_btns.append([Button.inline(f"🤖 {lbl}", data=f"pick_reply_acc:{p}".encode("utf-8"))])
        acc_btns.append([Button.inline("🔙 Back", data=b"btn_back_menu")])
        await event.respond(
            f"🤖 <b>DM Auto-Reply Account Chunein</b>\n"
            f"Jab koi is number par DM karega toh aapka set kiya hua <b>DM Message</b> usko reply mein chala jayega:\n"
            f"<code>{get_dm_message_text()[:250]}</code>",
            parse_mode="html",
            buttons=acc_btns,
        )

    elif data.startswith(b"pick_reply_acc:"):
        phone_sel = data.decode("utf-8").split(":", 1)[1].strip()
        config["dm_reply_account_phone"] = phone_sel
        config["dm_reply_enabled"] = True
        save_json(CONFIG_FILE, config)
        wizard_states[chat_id] = {"mode": "awaiting_dm_reply_toggle", "phone": phone_sel}
        await event.answer(f"Selected: {phone_sel}")
        await event.respond(
            f"✅ <b>DM Auto-Reply ON on <code>{phone_sel}</code>!</b>\n"
            f"Ab jab bhi koi is number par DM karega, usko aapka <b>DM Message</b> bhej diya jayega.\n"
            f"<i>(OFF karne ke liye <code>OFF</code> aur ON karne ke liye <code>ON</code> bhejein, ya menu ke liye /start dabayein.)</i>",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif data == b"btn_status":
        await event.answer("Status")
        await send_status_msg(event)

    elif data == b"btn_back_menu":
        wizard_states.pop(chat_id, None)
        await event.answer("Menu")
        await event.respond("🤖 <b>Control Menu</b>", parse_mode="html", buttons=main_menu_buttons())


@log_bot.on(events.NewMessage())
async def on_message_handler(event):
    if event.out or not is_admin(event):
        return
    text = (event.raw_text or "").strip()
    chat_id = event.chat_id
    dm_lim = int(config.get("daily_limit", 20))
    grp_lim = int(config.get("group_reply_daily_limit", 20))

    if text == "/start":
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"🤖 <b>Multi-Account Auto-DM & Group Reply Panel</b>\n"
            f"• <b>DM Limit:</b> <b>{dm_lim}</b>/day | <b>Group Reply Limit:</b> <b>{grp_lim}</b>/day\n"
            f"• <b>Added Accounts:</b> <b>{len(added_accounts)}</b> | <b>Groups:</b> <b>{len(config['groups'])}</b>\n"
            f"• <b>Saved Username IDs:</b> <b>{len(saved_username_ids)}</b>",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )
        return

    if text == "/status":
        await send_status_msg(event)
        return

    if text.startswith("/addgroup"):
        parts = text.split(maxsplit=1)
        if len(parts) >= 2:
            grp = parse_chat_target(parts[1])
            if grp not in config["groups"]:
                config["groups"].append(grp)
                save_json(CONFIG_FILE, config)
            await event.reply(f"✅ Group added: <code>{grp}</code>", parse_mode="html", buttons=main_menu_buttons())
        return

    if text == "/pause":
        config["paused"] = True
        save_json(CONFIG_FILE, config)
        await event.reply("⏸️ Paused.", parse_mode="html", buttons=main_menu_buttons())
        return

    if text == "/resume":
        config["paused"] = False
        config["flood_until"] = 0
        save_json(CONFIG_FILE, config)
        await event.reply("▶️ Resumed.", parse_mode="html", buttons=main_menu_buttons())
        return

    wiz = wizard_states.get(chat_id)
    if not wiz:
        return

    mode = wiz.get("mode")

    if mode == "awaiting_add_phone":
        try:
            await start_number_otp(chat_id, text)
            wizard_states[chat_id] = {"mode": "awaiting_add_code"}
            await event.reply(
                f"📲 <code>{text}</code> par OTP bhej diya gaya hai.\n"
                f"Ab Telegram mein aaya hua code/OTP bhejein:",
                parse_mode="html",
            )
        except Exception as e:
            wizard_states.pop(chat_id, None)
            await event.reply(f"❌ OTP send error: <code>{e}</code>", parse_mode="html", buttons=main_menu_buttons())

    elif mode == "awaiting_add_code":
        sess = pending_logins.get(chat_id)
        if not sess:
            wizard_states.pop(chat_id, None)
            await event.reply("⚠️ Login session expire ho gaya. Dubara <b>Add Number</b> dabayein.", parse_mode="html", buttons=main_menu_buttons())
            return
        try:
            await sess["client"].sign_in(
                phone=sess["phone"],
                code=text.replace(" ", ""),
                phone_code_hash=sess["phone_code_hash"],
            )
            label = await finalize_account_login(chat_id, sess["client"], sess["phone"])
            wizard_states.pop(chat_id, None)
            await event.reply(
                f"✅ <b>Number Add & Verify Ho Gaya!</b>\n• Account: <code>{label}</code>\n• Total Accounts: <b>{len(added_accounts)}</b>",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )
        except SessionPasswordNeededError:
            wizard_states[chat_id] = {"mode": "awaiting_add_2fa"}
            await event.reply("🔑 Is account par 2FA laga hai. 2FA password bhejein:", parse_mode="html")
        except Exception as e:
            await event.reply(f"❌ Code error: <code>{e}</code>\nSahi code bhejein:", parse_mode="html")

    elif mode == "awaiting_add_2fa":
        sess = pending_logins.get(chat_id)
        if not sess:
            wizard_states.pop(chat_id, None)
            return
        try:
            await sess["client"].sign_in(password=text)
            label = await finalize_account_login(chat_id, sess["client"], sess["phone"])
            wizard_states.pop(chat_id, None)
            await event.reply(
                f"✅ <b>2FA Verified & Number Add Ho Gaya!</b>\n• Account: <code>{label}</code>",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )
        except Exception as e:
            await event.reply(f"❌ 2FA error: <code>{e}</code>", parse_mode="html")

    elif mode == "awaiting_add_group":
        grp = parse_chat_target(text)
        if grp and grp not in config["groups"]:
            config["groups"].append(grp)
            save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"✅ <b>Group Add Ho Gaya!</b>\n• Group: <code>{grp}</code>\n• Total Groups: <b>{len(config['groups'])}</b>",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif mode == "awaiting_daily_limit":
        digits = text.strip()
        if not digits.isdigit() or int(digits) < 1:
            await event.reply("⚠️ Sahi number bhejein (jaise <code>20</code>, <code>30</code>, ya <code>50</code>):", parse_mode="html")
            return
        new_lim = int(digits)
        config["daily_limit"] = new_lim
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"✅ <b>DM Daily Limit Updated!</b>\n• New DM Limit: <b>{new_lim}</b>/day",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif mode == "awaiting_group_reply_limit":
        digits = text.strip()
        if not digits.isdigit() or int(digits) < 1:
            await event.reply("⚠️ Sahi number bhejein (jaise <code>20</code>, <code>30</code>, ya <code>50</code>):", parse_mode="html")
            return
        new_glim = int(digits)
        config["group_reply_daily_limit"] = new_glim
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"✅ <b>Group Reply Daily Limit Updated!</b>\n• New Group Reply Limit: <b>{new_glim}</b>/day",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif mode == "awaiting_timer_values":
        parts = text.split()
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            await event.reply("⚠️ Do numbers bhejein, jaise: <code>75 150</code>", parse_mode="html")
            return
        min_d, max_d = int(parts[0]), int(parts[1])
        if min_d < 30 or max_d < min_d:
            await event.reply("⚠️ Min kam se kam 30s rakhein aur max >= min ho.", parse_mode="html")
            return
        config["min_delay"] = min_d
        config["max_delay"] = max_d
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"✅ <b>Timer Updated!</b>\n• New Timer: <b>{min_d}s – {max_d}s</b>",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif mode == "awaiting_custom_dm_text":
        config["custom_dm_message"] = text
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"✅ <b>DM Message Update Ho Gaya!</b>\n(Ab DM aur Incoming DM Auto-Reply dono mein yahi message jayega)\n<code>{text}</code>",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif mode == "awaiting_group_reply_text":
        config["group_reply_message"] = text
        save_json(CONFIG_FILE, config)
        wizard_states.pop(chat_id, None)
        await event.reply(
            f"✅ <b>Group Reply Message Update Ho Gaya!</b>\n<code>{text}</code>",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif mode == "awaiting_admin_broadcast":
        wizard_states.pop(chat_id, None)
        count_sent = await broadcast_admin_message(text)
        await event.reply(
            f"📣 <b>Admin Message Bhej Diya Gaya!</b>\n• Groups Reached: <b>{count_sent}</b> / {len(config['groups'])}",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif mode == "awaiting_dm_reply_toggle":
        phone_sel = wiz.get("phone") or config.get("dm_reply_account_phone", "")
        wizard_states.pop(chat_id, None)
        up = text.upper()
        if up == "OFF":
            config["dm_reply_enabled"] = False
            save_json(CONFIG_FILE, config)
            await event.reply(f"🤖 DM Auto-Reply <b>OFF</b> ({phone_sel}).", parse_mode="html", buttons=main_menu_buttons())
        else:
            config["dm_reply_enabled"] = True
            save_json(CONFIG_FILE, config)
            await event.reply(f"🤖 DM Auto-Reply <b>ON</b> ({phone_sel}).", parse_mode="html", buttons=main_menu_buttons())


async def send_status_msg(event):
    refresh_daily_counter()
    now_ts = datetime.now().timestamp()
    eta_ts = float(live_task_state.get("eta_timestamp") or 0.0)
    next_dm_rem = max(0, int(eta_ts - now_ts)) if eta_ts > now_ts else 0
    dm_lim = int(config.get("daily_limit", 20))
    grp_lim = int(config.get("group_reply_daily_limit", 20))

    if config.get("paused"):
        state = "⏸️ Paused"
    elif not active_clients:
        state = "🔐 Add Number Pending"
    else:
        state = "🟢 Running"

    if live_task_state.get("active_user") and next_dm_rem > 0:
        acc_txt = f" via <code>{live_task_state['active_account']}</code>" if live_task_state.get("active_account") else ""
        timer_line = f"<b>{next_dm_rem}s</b> mein <code>{live_task_state['active_user']}</code>{acc_txt} ko DM + Group Reply hoga"
    elif dm_queue.qsize() > 0:
        timer_line = f"Queue mein {dm_queue.qsize()} user, agla timer shuru ho raha hai"
    else:
        timer_line = "Abhi queue khaali hai (group message ka intezar)"

    dm_today = int(config.get("today_dm_sent", 0))
    grp_today = int(config.get("today_group_sent", 0))
    ar_phone = config.get("dm_reply_account_phone") or "None"
    ar_state = "ON" if config.get("dm_reply_enabled", True) else "OFF"
    grp_tags = ", ".join([f"<code>{g}</code>" for g in config.get("groups", [])])

    await event.reply(
        f"📊 <b>Bot Status</b>\n\n"
        f"• <b>State:</b> {state}\n"
        f"• <b>Active Accounts:</b> <b>{len(active_clients)}</b> / {len(added_accounts)}\n"
        f"• <b>Groups ({len(config['groups'])}):</b> {grp_tags}\n"
        f"• <b>Saved Username IDs:</b> <b>{len(saved_username_ids)}</b>\n"
        f"• <b>Timer Range:</b> {config['min_delay']}s – {config['max_delay']}s\n"
        f"• <b>Next Task Timer:</b> {timer_line}\n"
        f"• <b>DM Aaj:</b> <b>{dm_today}</b> / <b>{dm_lim}</b>\n"
        f"• <b>Group Reply Aaj:</b> <b>{grp_today}</b> / <b>{grp_lim}</b>\n"
        f"• <b>DM Auto-Reply:</b> <b>{ar_state}</b> on <code>{ar_phone}</code> (sends DM Message)\n"
        f"• <b>Group-wise Aaj:</b>\n{format_group_breakdown()}\n"
        f"• <b>Queue Waiting:</b> {dm_queue.qsize()}\n"
        f"• <b>Live Activity ({live_task_state['last_updated']}):</b> {live_task_state['last_event']}",
        parse_mode="html",
        buttons=main_menu_buttons(),
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
        return False, "Group Reply Limit Full"

    group_target = user_info.get("chat_id") or parse_chat_target(user_info.get("group", ""))
    reply_msg_id = user_info.get("message_id")
    if not group_target:
        return False, "Invalid Group"

    reply_text = get_group_reply_text()

    candidates = []
    if preferred_client:
        candidates.append((preferred_client, preferred_label))
    for acc in active_clients.values():
        if acc["client"] is not preferred_client:
            candidates.append((acc["client"], acc["label"]))

    for client_obj, lbl in candidates:
        try:
            # Direct reply to that user's message inside the group (not an unlinked message)
            await client_obj.send_message(group_target, reply_text, reply_to=reply_msg_id)
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
                set_live_status("Paused")
                await asyncio.sleep(5)

            dm_available = int(config.get("today_dm_sent", 0)) < dm_lim
            grp_available = int(config.get("today_group_sent", 0)) < grp_lim

            if not dm_available and not grp_available:
                set_live_status(f"DM ({dm_lim}) aur Group Reply ({grp_lim}) dono limits poori ho chuki hain")
                continue

            clients_list = list(active_clients.values())
            if not clients_list:
                set_live_status("Koi active account nahi — Add Number se add karein")
                await asyncio.sleep(5)
                continue

            num_accs = len(clients_list)
            dm_ok = False
            dm_used_client = None
            dm_used_label = ""
            dm_status_note = "DM Limit Full" if not dm_available else "DM Failed"

            # 1) Pehle DM try karo (agar DM limit bachi hai)
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
                        f"{delay}s mein {sender_label} se {user_info['username']} ko DM + Group Reply hoga",
                        active_user=user_info["username"],
                        active_uid=uid,
                        active_group=user_info["group"],
                        active_account=sender_label,
                        eta_timestamp=eta_ts,
                        total_delay=delay,
                    )
                    await send_log(
                        f"⏱️ <b>Timer Start ({delay}s):</b> <b>{user_info['name']}</b> ({user_info['username']} / <code>{uid}</code>)\n"
                        f"• Account: <code>{sender_label}</code> | Group: <code>{user_info['group']}</code>"
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

            # 2) Usi user ke group message par direct REPLY bhejo (reply_to=message_id)
            grp_reply_ok, grp_reply_info = await send_direct_group_reply(user_info, dm_used_client, dm_used_label)

            record_delivery_stats(user_info["group"], dm_ok=dm_ok, group_reply_ok=grp_reply_ok)

            if dm_ok or grp_reply_ok:
                sent_users.add(uid)
                save_json(SENT_FILE, list(sent_users))
            else:
                skipped_users.add(uid)
                save_json(SKIPPED_FILE, list(skipped_users))

            dm_badge = f"✅ Sent via <code>{dm_used_label}</code>" if dm_ok else f"❌ {dm_status_note}"
            grp_badge = f"✅ Direct Reply via <code>{grp_reply_info}</code>" if grp_reply_ok else f"❌ {grp_reply_info}"

            set_live_status(f"Done {user_info['username']} | DM: {'OK' if dm_ok else 'NO'} | Group Reply: {'OK' if grp_reply_ok else 'NO'}")
            await send_log(
                f"📋 <b>Delivery Report:</b> {user_info['name']} ({user_info['username']} / <code>{uid}</code>)\n"
                f"• <b>DM Status:</b> {dm_badge}\n"
                f"• <b>Group Reply Status:</b> {grp_badge}\n"
                f"• <b>Aaj DM:</b> <b>{config['today_dm_sent']}</b> / <b>{dm_lim}</b> | <b>Aaj Group Reply:</b> <b>{config['today_group_sent']}</b> / <b>{grp_lim}</b>"
            )

        finally:
            dm_queue.task_done()


async def run_supervisor():
    start_health_server()

    while True:
        try:
            if not log_bot.is_connected():
                await log_bot.connect()
            await log_bot.start(bot_token=LOG_BOT_TOKEN)
            await resolve_log_target()
            await load_all_stored_accounts()

            worker_task = asyncio.create_task(dm_worker())
            set_live_status("Ready — /start se buttons kholein")

            await send_log(
                f"🚀 <b>Multi-Account Auto-DM & Group Reply Bot Started!</b>\n"
                f"• Active Accounts: <b>{len(active_clients)}</b>\n"
                f"• Watched Groups: <b>{len(config['groups'])}</b>\n"
                f"• DM Limit: <b>{config['daily_limit']}</b>/day | Group Reply Limit: <b>{config['group_reply_daily_limit']}</b>/day\n"
                f"• Timer: {config['min_delay']}s – {config['max_delay']}s",
                also_print=True,
            )

            await log_bot.run_until_disconnected()
            worker_task.cancel()

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
