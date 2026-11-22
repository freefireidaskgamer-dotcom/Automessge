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
# 24H 20-DM GUARANTEED-ATTEMPT + USER LOGIN / GROUP BROADCAST / AUTO-REPLY
# =========================================================================
HARDCODED_STRING_SESSION = ""
HARDCODED_PHONE = ""

api_id = int(os.getenv("TG_API_ID", "32946015"))
api_hash = os.getenv("TG_API_HASH", "318e71a31d57a9d0fa0b9c055ba16ede").strip()

LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN", "9238275421:AAGfzrIaiRR4vADpdnRWB4tVDUtKZhz39b4").strip()
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID", "8731314485"))

PHONE_NUMBER = (
    os.getenv("TG_PHONE", "")
    or os.getenv("PHONE_NUMBER", "")
    or HARDCODED_PHONE
).strip()

STRING_SESSION = (
    os.getenv("TG_STRING_SESSION", "")
    or os.getenv("STRING_SESSION", "")
    or HARDCODED_STRING_SESSION
).strip()

CODE_FILE = os.getenv("CODE_FILE", "phone_code.txt")
PASSWORD_2FA_FILE = os.getenv("PASSWORD_2FA_FILE", "password_2fa.txt")
SESSION_SAVE_FILE = os.getenv("SESSION_SAVE_FILE", "verified_session.txt")

ENABLE_HEALTH_SERVER = os.getenv("ENABLE_HEALTH_SERVER", "1").strip() == "1"
PORT = int(os.getenv("PORT", "8080"))

DEFAULT_DM_TEMPLATES = [
    (
        "FREE LIKE KE LIYE YE BOT TRY KARO FF ME FREE LIKE DETA HA 220 MAST HA\n\n"
        "https://t.me/Likeandcc8_bot?start=7161571409"
    ),
    (
        "Bhai FF me free like chahiye toh ye bot try karke dekho, 220 tak like milte hain:\n\n"
        "https://t.me/Likeandcc8_bot?start=7161571409"
    ),
    (
        "Free Fire free likes ke liye ye bot kaam ka hai, ek baar check kar lo:\n\n"
        "https://t.me/Likeandcc8_bot?start=7161571409"
    ),
]

CONFIG_FILE = os.getenv("CONFIG_FILE", "bot_config.json")
SENT_FILE = os.getenv("SENT_FILE", "sent_users.json")
SKIPPED_FILE = os.getenv("SKIPPED_FILE", "skipped_users.json")
USER_SESSIONS_FILE = os.getenv("USER_SESSIONS_FILE", "user_accounts.json")

DEFAULT_CONFIG = {
    "groups": ["@FreeFireLikes69", "@noobnation69"],
    "min_delay": 75,
    "max_delay": 150,
    "daily_limit": 20,
    "paused": False,
    "today_date": datetime.now().strftime("%Y-%m-%d"),
    "today_sent": 0,
    "today_attempts": 0,
    "today_by_group": {},
    "flood_until": 0,
    "custom_dm_message": "",
    "auto_reply_enabled": True,
    "auto_reply_text": "Bhai message mil gaya! Thodi der mein reply karta hoon.",
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


def _read_text_file(path):
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def _write_text_file(path, content):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.strip())


config = load_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
for k, v in DEFAULT_CONFIG.items():
    config.setdefault(k, v)
config["daily_limit"] = 20

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

sent_users = set(load_json(SENT_FILE, []))
save_json(SENT_FILE, list(sent_users))

skipped_users = set(load_json(SKIPPED_FILE, []))
save_json(SKIPPED_FILE, list(skipped_users))

user_accounts = load_json(USER_SESSIONS_FILE, {})
if not isinstance(user_accounts, dict):
    user_accounts = {}

saved_session_from_file = _read_text_file(SESSION_SAVE_FILE)
active_string_session = STRING_SESSION or saved_session_from_file

user_session = StringSession(active_string_session) if active_string_session else StringSession()
log_session_name = os.getenv("LOG_SESSION_NAME", "log_bot_session")

client_kwargs = {
    "connection_retries": 10,
    "retry_delay": 4,
    "timeout": 20,
    "auto_reconnect": True,
}

user_client = TelegramClient(user_session, api_id, api_hash, **client_kwargs)
log_bot = TelegramClient(log_session_name, api_id, api_hash, **client_kwargs)

dm_queue = asyncio.Queue()
queued_user_ids = set()
log_target_entity = None
replied_dm_senders = set()

auth_state = {
    "verified": False,
    "phone": PHONE_NUMBER,
    "phone_code_hash": None,
    "step": "idle",
    "me_label": None,
}

live_task_state = {
    "active_user": None,
    "active_uid": None,
    "active_group": None,
    "eta_timestamp": 0.0,
    "total_delay": 0,
    "last_event": "Bot initialize ho raha hai...",
    "last_updated": datetime.now().strftime("%H:%M:%S"),
}

# Interactive user wizard states keyed by chat_id
user_wizard_state = {}
pending_user_logins = {}


def set_live_status(
    last_event: str,
    active_user: str = None,
    active_uid=None,
    active_group: str = None,
    eta_timestamp: float = 0.0,
    total_delay: int = 0,
):
    live_task_state["last_event"] = last_event
    live_task_state["active_user"] = active_user
    live_task_state["active_uid"] = active_uid
    live_task_state["active_group"] = active_group
    live_task_state["eta_timestamp"] = eta_timestamp
    live_task_state["total_delay"] = total_delay
    live_task_state["last_updated"] = datetime.now().strftime("%H:%M:%S")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - Auto-DM 24/7 running")

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
        config["today_attempts"] = 0
        config["today_by_group"] = {}
        save_json(CONFIG_FILE, config)
    elif not isinstance(config.get("today_by_group"), dict):
        config["today_by_group"] = {}
        save_json(CONFIG_FILE, config)


def increment_attempt_and_sent(group_label: str, sent_ok: bool):
    refresh_daily_counter()
    config["today_attempts"] = int(config.get("today_attempts", 0)) + 1
    if sent_ok:
        config["today_sent"] = int(config.get("today_sent", 0)) + 1
        by_grp = config.setdefault("today_by_group", {})
        by_grp[group_label] = int(by_grp.get(group_label, 0)) + 1
    save_json(CONFIG_FILE, config)


def format_group_breakdown() -> str:
    by_grp = config.get("today_by_group", {})
    if not by_grp:
        return "Aaj abhi kisi group se DM nahi gaya."
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
    if custom:
        return custom
    return random.choice(DEFAULT_DM_TEMPLATES)


def main_menu_buttons():
    return [
        [
            Button.inline("🔐 User Account Login", data=b"menu_login"),
            Button.inline("✉️ Set DM Message", data=b"menu_setmsg"),
        ],
        [
            Button.inline("📢 Send to Group", data=b"menu_sendgroup"),
            Button.inline("💬 Auto-Reply Setup", data=b"menu_autoreply"),
        ],
        [
            Button.inline("📊 Status & 24h Quota", data=b"menu_status"),
        ],
    ]


# ---------------- MAIN ACCOUNT VERIFICATION FLOW ----------------

async def _mark_verified_and_announce():
    me = await user_client.get_me()
    name = getattr(me, "first_name", "") or ""
    uname = f"@{me.username}" if getattr(me, "username", None) else ""
    phone_str = auth_state.get("phone") or getattr(me, "phone", "") or "Verified Account"
    label = f"{name} {uname} ({phone_str})".strip()
    auth_state["verified"] = True
    auth_state["step"] = "verified"
    auth_state["me_label"] = label
    set_live_status(f"Verified ({label}) — 24h 20-DM slot active")

    exported = user_client.session.save() if isinstance(user_client.session, StringSession) else ""
    if exported:
        _write_text_file(SESSION_SAVE_FILE, exported)
        print(f"[Auth] Verified StringSession: {exported}")

    await send_log(
        f"✅ <b>Number Verify Ho Gaya!</b>\n"
        f"• <b>Account:</b> <code>{label}</code>\n"
        f"• <b>24h Quota:</b> 20 slots (fail hone par next slot automatically try karega)"
    )


async def send_login_code_for_phone(phone_str: str):
    phone_clean = phone_str.strip()
    auth_state["phone"] = phone_clean
    sent_code = await user_client.send_code_request(phone_clean)
    auth_state["phone_code_hash"] = sent_code.phone_code_hash
    auth_state["step"] = "waiting_code"
    set_live_status(f"OTP bhej diya gaya ({phone_clean}) — /code ka intezar")
    await send_log(
        f"📲 <b>OTP / Code Bhej Diya Gaya!</b>\n"
        f"• <b>Number:</b> <code>{phone_clean}</code>\n"
        f"• Code bhejein: <code>/code 12345</code>"
    )


async def verify_login_code(code_str: str):
    code_clean = code_str.strip().replace(" ", "")
    phone_clean = auth_state.get("phone") or PHONE_NUMBER
    code_hash = auth_state.get("phone_code_hash")
    if not phone_clean or not code_hash:
        await send_log("⚠️ Pehle number bhejein: <code>/login +91XXXXXXXXXX</code>")
        return

    try:
        await user_client.sign_in(phone=phone_clean, code=code_clean, phone_code_hash=code_hash)
        await _mark_verified_and_announce()
    except SessionPasswordNeededError:
        auth_state["step"] = "waiting_2fa"
        await send_log("🔑 <b>2FA Password Required!</b> Bhejein: <code>/pass aapka_password</code>")


async def verify_2fa_password(pwd_str: str):
    await user_client.sign_in(password=pwd_str.strip())
    await _mark_verified_and_announce()


async def run_auth_flow():
    if not user_client.is_connected():
        await user_client.connect()

    if await user_client.is_user_authorized():
        await _mark_verified_and_announce()
        return

    preset_phone = PHONE_NUMBER or _read_text_file("phone_number.txt")
    if preset_phone:
        auth_state["phone"] = preset_phone
        await send_login_code_for_phone(preset_phone)
        preset_code = os.getenv("TG_LOGIN_CODE", "").strip() or _read_text_file(CODE_FILE)
        if preset_code:
            try:
                await verify_login_code(preset_code)
                return
            except Exception as e:
                await send_log(f"⚠️ Preset code se login nahi hua: <code>{e}</code>")
    else:
        auth_state["step"] = "waiting_phone"
        set_live_status("Number verification pending — /login bhejein")
        await send_log(
            "📱 <b>Number Verification Pending</b>\n"
            "• Bhejein: <code>/login +91XXXXXXXXXX</code>\n"
            "• Phir code: <code>/code 12345</code>"
        )

    while not auth_state.get("verified"):
        await asyncio.sleep(3)


# ---------------- USER SELF-LOGIN, GROUP SEND & AUTO-REPLY ----------------

async def get_or_connect_user_account(owner_id: int):
    rec = user_accounts.get(str(owner_id))
    if not rec or not rec.get("session"):
        return None, None
    client = TelegramClient(StringSession(rec["session"]), api_id, api_hash, **client_kwargs)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return None, None
    return client, rec


async def send_user_login_otp(chat_id: int, phone_str: str):
    phone_clean = phone_str.strip()
    temp_client = TelegramClient(StringSession(), api_id, api_hash, **client_kwargs)
    await temp_client.connect()
    sent_code = await temp_client.send_code_request(phone_clean)
    pending_user_logins[chat_id] = {
        "client": temp_client,
        "phone": phone_clean,
        "phone_code_hash": sent_code.phone_code_hash,
    }


async def complete_user_login_code(chat_id: int, code_str: str):
    sess = pending_user_logins.get(chat_id)
    if not sess:
        raise RuntimeError("Pehle apna phone number bhejein.")
    code_clean = code_str.strip().replace(" ", "")
    client = sess["client"]
    try:
        await client.sign_in(phone=sess["phone"], code=code_clean, phone_code_hash=sess["phone_code_hash"])
    except SessionPasswordNeededError:
        sess["awaiting_2fa"] = True
        return "2FA_NEEDED"

    me = await client.get_me()
    label = f"{getattr(me, 'first_name', '') or 'User'} (@{me.username})" if getattr(me, "username", None) else (getattr(me, "first_name", '') or sess["phone"])
    user_accounts[str(chat_id)] = {
        "session": client.session.save(),
        "phone": sess["phone"],
        "label": label,
        "custom_message": user_accounts.get(str(chat_id), {}).get("custom_message", ""),
        "auto_reply_enabled": user_accounts.get(str(chat_id), {}).get("auto_reply_enabled", True),
        "auto_reply_text": user_accounts.get(str(chat_id), {}).get("auto_reply_text", "Bhai message mil gaya! Thodi der mein reply karta hoon."),
    }
    save_json(USER_SESSIONS_FILE, user_accounts)
    await client.disconnect()
    pending_user_logins.pop(chat_id, None)
    return label


async def complete_user_login_2fa(chat_id: int, pwd_str: str):
    sess = pending_user_logins.get(chat_id)
    if not sess:
        raise RuntimeError("Active login session nahi mila.")
    client = sess["client"]
    await client.sign_in(password=pwd_str.strip())
    me = await client.get_me()
    label = f"{getattr(me, 'first_name', '') or 'User'} (@{me.username})" if getattr(me, "username", None) else (getattr(me, "first_name", '') or sess["phone"])
    user_accounts[str(chat_id)] = {
        "session": client.session.save(),
        "phone": sess["phone"],
        "label": label,
        "custom_message": user_accounts.get(str(chat_id), {}).get("custom_message", ""),
        "auto_reply_enabled": user_accounts.get(str(chat_id), {}).get("auto_reply_enabled", True),
        "auto_reply_text": user_accounts.get(str(chat_id), {}).get("auto_reply_text", "Bhai message mil gaya! Thodi der mein reply karta hoon."),
    }
    save_json(USER_SESSIONS_FILE, user_accounts)
    await client.disconnect()
    pending_user_logins.pop(chat_id, None)
    return label


async def broadcast_to_group_for_owner(owner_id: int, group_raw: str, message_text: str):
    client, rec = await get_or_connect_user_account(owner_id)
    if not client:
        # Fallback to main verified account if admin uses it
        if owner_id == LOG_CHAT_ID and auth_state.get("verified"):
            target = parse_chat_target(group_raw)
            await user_client.send_message(target, message_text)
            return f"Main Account ({auth_state.get('me_label')})"
        raise RuntimeError("Pehle 'User Account Login' button se apna Telegram account login karein.")

    try:
        target = parse_chat_target(group_raw)
        await client.send_message(target, message_text)
        return rec.get("label") or rec.get("phone")
    finally:
        await client.disconnect()


@user_client.on(events.NewMessage(incoming=True))
async def on_incoming_dm_autoreply(event):
    if not auth_state.get("verified"):
        return
    if not event.is_private or event.out:
        return
    if not config.get("auto_reply_enabled", True):
        return

    sender = await event.get_sender()
    if not sender or getattr(sender, "bot", False):
        return
    if sender.id in replied_dm_senders:
        return

    replied_dm_senders.add(sender.id)
    reply_txt = (config.get("auto_reply_text") or "").strip()
    if reply_txt:
        try:
            await event.reply(reply_txt)
        except Exception as e:
            print(f"[Auto-Reply Error] {e}")


# ---------------- BOT BUTTONS & COMMANDS ----------------

@log_bot.on(events.CallbackQuery)
async def on_callback(event):
    data = event.data
    chat_id = event.chat_id

    if data == b"menu_login":
        user_wizard_state[chat_id] = {"mode": "awaiting_user_phone"}
        await event.answer("Phone number bhejein")
        await event.respond(
            "🔐 <b>User Account Login</b>\n"
            "Apna Telegram phone number bhejein (jaise <code>+919876543210</code>):",
            parse_mode="html",
        )

    elif data == b"menu_setmsg":
        user_wizard_state[chat_id] = {"mode": "awaiting_dm_message"}
        await event.answer("Message text bhejein")
        await event.respond(
            "✉️ <b>Set DM / Broadcast Message</b>\n"
            "Jo message aap set karna chahte hain wo yahan bhej dein:",
            parse_mode="html",
        )

    elif data == b"menu_sendgroup":
        user_wizard_state[chat_id] = {"mode": "awaiting_group_target"}
        await event.answer("Group username/link bhejein")
        await event.respond(
            "📢 <b>Send Message to Group</b>\n"
            "Kis group mein message bhejna hai? Group ka <code>@username</code>, link, ya ID bhejein:",
            parse_mode="html",
        )

    elif data == b"menu_autoreply":
        user_wizard_state[chat_id] = {"mode": "awaiting_autoreply_text"}
        current_state = "ON" if config.get("auto_reply_enabled", True) else "OFF"
        await event.answer("Auto-reply text bhejein")
        await event.respond(
            f"💬 <b>Auto-Reply Setup</b> (Current: <b>{current_state}</b>)\n"
            f"Koi message kare toh kya auto-reply jaana chahiye? Wo text bhej dein:\n"
            f"<i>(Band karne ke liye <code>OFF</code> aur chalu karne ke liye <code>ON</code> bhej sakte hain.)</i>",
            parse_mode="html",
        )

    elif data == b"menu_status":
        await event.answer("Status load ho raha hai...")
        await send_status_reply(event)


@log_bot.on(events.NewMessage())
async def on_wizard_or_command(event):
    if event.out:
        return
    text = (event.raw_text or "").strip()
    chat_id = event.chat_id

    # Handle slash commands first
    if text.startswith("/"):
        if text == "/start":
            user_wizard_state.pop(chat_id, None)
            await event.reply(
                "🤖 <b>Auto-DM & User Control Panel</b>\n\n"
                "Neeche diye gaye buttons se aap account login, message set, group message, aur auto-reply kar sakte hain.\n"
                "• <b>24h Quota:</b> 20 message slots (fail hone par next user try karega)\n"
                "• <b>Admin Commands:</b> <code>/login</code>, <code>/code</code>, <code>/pass</code>, <code>/addgroup</code>, <code>/settimer</code>, <code>/pause</code>, <code>/resume</code>, <code>/status</code>",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )
            return

        if not is_admin(event):
            return

        if text.startswith("/login"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await event.reply("Usage: <code>/login +919876543210</code>", parse_mode="html")
                return
            try:
                if not user_client.is_connected():
                    await user_client.connect()
                await send_login_code_for_phone(parts[1].strip())
            except Exception as e:
                await event.reply(f"❌ OTP error: <code>{e}</code>", parse_mode="html")
            return

        if text.startswith("/code"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await event.reply("Usage: <code>/code 12345</code>", parse_mode="html")
                return
            try:
                await verify_login_code(parts[1].strip())
            except Exception as e:
                await event.reply(f"❌ Code error: <code>{e}</code>", parse_mode="html")
            return

        if text.startswith("/pass"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await event.reply("Usage: <code>/pass password</code>", parse_mode="html")
                return
            try:
                await verify_2fa_password(parts[1].strip())
            except Exception as e:
                await event.reply(f"❌ 2FA error: <code>{e}</code>", parse_mode="html")
            return

        if text.startswith("/addgroup"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await event.reply("Usage: <code>/addgroup @groupname</code>", parse_mode="html")
                return
            target = parse_chat_target(parts[1].strip())
            if target not in config["groups"]:
                config["groups"].append(target)
                save_json(CONFIG_FILE, config)
            await event.reply(f"✅ Group active: <code>{target}</code>", parse_mode="html")
            return

        if text.startswith("/settimer"):
            parts = text.split()
            if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
                await event.reply("Usage: <code>/settimer 75 150</code>", parse_mode="html")
                return
            min_d, max_d = int(parts[1]), int(parts[2])
            if min_d < 30 or max_d < min_d:
                await event.reply("⚠️ Min delay kam se kam 30s rakhein aur max >= min ho.", parse_mode="html")
                return
            config["min_delay"] = min_d
            config["max_delay"] = max_d
            save_json(CONFIG_FILE, config)
            await event.reply(f"⏱️ Timer updated: <b>{min_d}s – {max_d}s</b>", parse_mode="html")
            return

        if text == "/pause":
            config["paused"] = True
            save_json(CONFIG_FILE, config)
            await event.reply("⏸️ Auto-DM <b>PAUSED</b>.", parse_mode="html")
            return

        if text == "/resume":
            config["paused"] = False
            config["flood_until"] = 0
            save_json(CONFIG_FILE, config)
            await event.reply("▶️ Auto-DM <b>RESUMED</b>.", parse_mode="html")
            return

        if text == "/status":
            await send_status_reply(event)
            return

    # Handle interactive button wizard steps
    wiz = user_wizard_state.get(chat_id)
    if not wiz:
        return

    mode = wiz.get("mode")

    if mode == "awaiting_user_phone":
        try:
            await send_user_login_otp(chat_id, text)
            user_wizard_state[chat_id] = {"mode": "awaiting_user_code"}
            await event.reply(
                f"📲 OTP bhej diya gaya hai <code>{text}</code> par.\n"
                f"Ab Telegram mein aaya hua OTP/code yahan bhejein:",
                parse_mode="html",
            )
        except Exception as e:
            user_wizard_state.pop(chat_id, None)
            await event.reply(f"❌ OTP bhejne mein error: <code>{e}</code>", parse_mode="html", buttons=main_menu_buttons())

    elif mode == "awaiting_user_code":
        try:
            res = await complete_user_login_code(chat_id, text)
            if res == "2FA_NEEDED":
                user_wizard_state[chat_id] = {"mode": "awaiting_user_2fa"}
                await event.reply("🔑 Is account par 2FA laga hai. Apna 2FA password bhejein:", parse_mode="html")
            else:
                user_wizard_state.pop(chat_id, None)
                await event.reply(
                    f"✅ <b>Account Login Successful!</b>\n• Account: <code>{res}</code>",
                    parse_mode="html",
                    buttons=main_menu_buttons(),
                )
        except Exception as e:
            await event.reply(f"❌ Login code error: <code>{e}</code>\nDobara code bhejein ya <code>/start</code> karein.", parse_mode="html")

    elif mode == "awaiting_user_2fa":
        try:
            label = await complete_user_login_2fa(chat_id, text)
            user_wizard_state.pop(chat_id, None)
            await event.reply(
                f"✅ <b>2FA Verified & Account Login Complete!</b>\n• Account: <code>{label}</code>",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )
        except Exception as e:
            await event.reply(f"❌ 2FA password error: <code>{e}</code>", parse_mode="html")

    elif mode == "awaiting_dm_message":
        user_wizard_state.pop(chat_id, None)
        config["custom_dm_message"] = text
        save_json(CONFIG_FILE, config)
        rec = user_accounts.get(str(chat_id))
        if rec:
            rec["custom_message"] = text
            save_json(USER_SESSIONS_FILE, user_accounts)
        await event.reply(
            f"✅ <b>Message Set Ho Gaya!</b>\n<code>{text}</code>",
            parse_mode="html",
            buttons=main_menu_buttons(),
        )

    elif mode == "awaiting_group_target":
        user_wizard_state[chat_id] = {"mode": "awaiting_group_text", "group": text}
        await event.reply(
            f"📢 Group <code>{text}</code> select ho gaya.\n"
            f"Ab wo message bhejein jo group mein bhejna hai:",
            parse_mode="html",
        )

    elif mode == "awaiting_group_text":
        group_raw = wiz.get("group")
        user_wizard_state.pop(chat_id, None)
        try:
            used_acc = await broadcast_to_group_for_owner(chat_id, group_raw, text)
            await event.reply(
                f"✅ <b>Group Message Sent!</b>\n"
                f"• Account: <code>{used_acc}</code>\n"
                f"• Group: <code>{group_raw}</code>",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )
        except Exception as e:
            await event.reply(
                f"❌ Group message nahi gaya: <code>{e}</code>",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )

    elif mode == "awaiting_autoreply_text":
        user_wizard_state.pop(chat_id, None)
        upper = text.upper()
        if upper == "OFF":
            config["auto_reply_enabled"] = False
            save_json(CONFIG_FILE, config)
            await event.reply("💬 Auto-reply <b>OFF</b> kar diya gaya hai.", parse_mode="html", buttons=main_menu_buttons())
        elif upper == "ON":
            config["auto_reply_enabled"] = True
            save_json(CONFIG_FILE, config)
            await event.reply("💬 Auto-reply <b>ON</b> kar diya gaya hai.", parse_mode="html", buttons=main_menu_buttons())
        else:
            config["auto_reply_enabled"] = True
            config["auto_reply_text"] = text
            save_json(CONFIG_FILE, config)
            rec = user_accounts.get(str(chat_id))
            if rec:
                rec["auto_reply_enabled"] = True
                rec["auto_reply_text"] = text
                save_json(USER_SESSIONS_FILE, user_accounts)
            await event.reply(
                f"✅ <b>Auto-Reply Set & ON!</b>\n<code>{text}</code>",
                parse_mode="html",
                buttons=main_menu_buttons(),
            )


async def send_status_reply(event):
    refresh_daily_counter()
    now_ts = datetime.now().timestamp()
    flood_rem = max(0, int(config.get("flood_until", 0) - now_ts))
    eta_ts = float(live_task_state.get("eta_timestamp") or 0.0)
    next_dm_rem = max(0, int(eta_ts - now_ts)) if eta_ts > now_ts else 0

    if not auth_state.get("verified"):
        state = f"🔐 Verify Pending ({auth_state.get('step')})"
    elif config.get("paused"):
        state = "⏸️ Paused"
    elif flood_rem > 0:
        state = f"⏳ Flood Wait ({flood_rem}s)"
    else:
        state = "🟢 Running (24h 20-Slot Mode)"

    groups_str = ", ".join([str(g) for g in config["groups"]]) or "None"
    acc_label = auth_state.get("me_label") or auth_state.get("phone") or "Not Verified"
    attempts_today = int(config.get("today_attempts", 0))
    sent_today = int(config.get("today_sent", 0))
    slots_left = max(0, 20 - attempts_today)

    if live_task_state.get("active_user") and next_dm_rem > 0:
        next_dm_line = f"<b>{next_dm_rem}s</b> mein <code>{live_task_state['active_user']}</code> ko attempt"
    elif dm_queue.qsize() > 0:
        next_dm_line = f"Queue mein {dm_queue.qsize()} user tayyar"
    else:
        next_dm_line = "Queue khaali hai (group message ka intezar)"

    ar_status = "ON" if config.get("auto_reply_enabled", True) else "OFF"
    await event.reply(
        f"📊 <b>Bot Status</b>\n\n"
        f"• <b>State:</b> {state}\n"
        f"• <b>Main Account:</b> <code>{acc_label}</code>\n"
        f"• <b>Groups:</b> <code>{groups_str}</code>\n"
        f"• <b>Timer:</b> {config['min_delay']}s – {config['max_delay']}s\n"
        f"• <b>Agla DM Timer:</b> {next_dm_line}\n"
        f"• <b>24h Slots Used / Left:</b> <b>{attempts_today}</b> / 20 (<b>{slots_left}</b> slots baaki)\n"
        f"• <b>Aaj Delivered DMs:</b> <b>{sent_today}</b> / 20\n"
        f"• <b>Auto-Reply:</b> <b>{ar_status}</b>\n"
        f"• <b>Group-wise Sent:</b>\n{format_group_breakdown()}\n"
        f"• <b>Queue Waiting:</b> {dm_queue.qsize()}\n"
        f"• <b>Total Sent:</b> {len(sent_users)} | <b>Skipped:</b> {len(skipped_users)}\n"
        f"• <b>Live Activity ({live_task_state['last_updated']}):</b> {live_task_state['last_event']}",
        parse_mode="html",
        buttons=main_menu_buttons(),
    )


# ---------------- GROUP LISTENER & 24H 20-SLOT WORKER ----------------

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


@user_client.on(events.NewMessage())
async def on_any_message(event):
    if not auth_state.get("verified"):
        return
    if event.out or not event.is_group:
        return

    chat = await event.get_chat()
    if not is_watched_group(chat):
        return

    sender = await event.get_sender()
    if not sender or sender.bot:
        return

    if sender.id in sent_users or sender.id in skipped_users or sender.id in queued_user_ids:
        return

    queued_user_ids.add(sender.id)
    group_label = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)
    user_info = {
        "id": sender.id,
        "name": sender.first_name or "No Name",
        "username": f"@{sender.username}" if getattr(sender, "username", None) else "No Username",
        "group": group_label,
    }
    await dm_queue.put(user_info)
    print(f"[Queued] {user_info['name']} ({user_info['username']} / {user_info['id']}) from {group_label} | Queue: {dm_queue.qsize()}")
    set_live_status(f"Queued {user_info['name']} ({user_info['username']}) from {group_label}")


async def dm_worker():
    while True:
        user_info = await dm_queue.get()
        uid = user_info["id"]
        queued_user_ids.discard(uid)

        try:
            if not auth_state.get("verified"):
                await asyncio.sleep(3)
                queued_user_ids.add(uid)
                await dm_queue.put(user_info)
                continue

            refresh_daily_counter()

            if uid in sent_users or uid in skipped_users:
                continue

            while config.get("paused", False):
                set_live_status("Paused — /resume ka intezar")
                await asyncio.sleep(5)

            # 24 ghante mein 20 message slots: fail hone par rukenge nahi, agla user try hoga
            if int(config.get("today_attempts", 0)) >= 20:
                set_live_status("24h 20-slot quota poora ho chuka hai")
                continue

            now_ts = datetime.now().timestamp()
            flood_until = config.get("flood_until", 0)
            if flood_until > now_ts:
                wait_sec = int(flood_until - now_ts) + 2
                set_live_status(f"FloodWait {wait_sec}s baaki")
                await asyncio.sleep(wait_sec)

            delay = random.randint(int(config["min_delay"]), int(config["max_delay"]))
            eta_ts = datetime.now().timestamp() + delay
            slot_num = int(config.get("today_attempts", 0)) + 1
            set_live_status(
                f"Slot {slot_num}/20: {delay}s mein {user_info['name']} ({user_info['username']}) ko attempt",
                active_user=user_info["username"],
                active_uid=uid,
                active_group=user_info["group"],
                eta_timestamp=eta_ts,
                total_delay=delay,
            )
            await send_log(
                f"⏱️ <b>Slot {slot_num}/20 Timer ({delay}s):</b> <b>{user_info['name']}</b> "
                f"({user_info['username']} / <code>{uid}</code>) | Group: <code>{user_info['group']}</code>"
            )

            for rem in range(delay, 0, -1):
                if rem == delay or rem in (30, 15, 5, 1):
                    print(f"[Timer] {rem}s mein {user_info['name']} ({uid}) ko message jayega...")
                await asyncio.sleep(1)

            text_to_send = get_dm_message_text()
            target_peer = user_info["username"] if user_info["username"].startswith("@") else uid

            try:
                await user_client.send_message(target_peer, text_to_send)
                sent_users.add(uid)
                save_json(SENT_FILE, list(sent_users))
                increment_attempt_and_sent(user_info["group"], sent_ok=True)
                set_live_status(f"Slot {slot_num}/20 Sent -> {user_info['username']} ({delay}s)")
                await send_log(
                    f"✅ <b>Message Sent (Slot {slot_num}/20)!</b>\n"
                    f"• User: {user_info['name']} ({user_info['username']} / <code>{uid}</code>)\n"
                    f"• Group: <code>{user_info['group']}</code> | Delay: {delay}s\n"
                    f"• Aaj Delivered: <b>{config['today_sent']}</b>/20 | Slots Used: <b>{config['today_attempts']}</b>/20"
                )

            except FloodWaitError as fw:
                wait_sec = min(int(fw.seconds) + 5, 120)
                config["flood_until"] = datetime.now().timestamp() + wait_sec
                save_json(CONFIG_FILE, config)
                increment_attempt_and_sent(user_info["group"], sent_ok=False)
                skipped_users.add(uid)
                save_json(SKIPPED_FILE, list(skipped_users))
                set_live_status(f"Slot {slot_num}/20 FloodWait ({wait_sec}s) — agla user try hoga")
                await send_log(
                    f"⏳ <b>Slot {slot_num}/20 FloodWait ({wait_sec}s):</b> {user_info['username']} skip karke agla user lenge."
                )

            except UserPrivacyRestrictedError:
                increment_attempt_and_sent(user_info["group"], sent_ok=False)
                skipped_users.add(uid)
                save_json(SKIPPED_FILE, list(skipped_users))
                set_live_status(f"Slot {slot_num}/20 Privacy Skip ({user_info['username']}) — agla user try")
                await send_log(
                    f"🔒 <b>Slot {slot_num}/20 Privacy Skip:</b> {user_info['username']} — agla user try hoga."
                )

            except Exception as e:
                # Fail / spam / non-contact limit par bhi cool-down nahi: slot count karke agla user turant next cycle mein try hoga
                increment_attempt_and_sent(user_info["group"], sent_ok=False)
                skipped_users.add(uid)
                save_json(SKIPPED_FILE, list(skipped_users))
                err_tag = "Spam/Non-Contact Limit" if is_non_contact_limit_error(e) else str(e)[:60]
                set_live_status(f"Slot {slot_num}/20 Failed ({err_tag}) — agla user try hoga")
                await send_log(
                    f"⚠️ <b>Slot {slot_num}/20 Failed ({err_tag}):</b> {user_info['name']} ({user_info['username']}) skip kiya gaya, "
                    f"cool-down nahi lagaya — ab agla user try hoga. (Slots used: <b>{config['today_attempts']}</b>/20)"
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

            await run_auth_flow()
            worker_task = asyncio.create_task(dm_worker())

            await send_log(
                f"🚀 <b>Auto-DM 24/7 Bot Started!</b>\n"
                f"• <b>Account:</b> <code>{auth_state.get('me_label')}</code>\n"
                f"• <b>24h Quota:</b> 20 message slots (auto-next on fail)\n"
                f"• <b>Timer:</b> {config['min_delay']}s – {config['max_delay']}s"
            )

            await asyncio.gather(
                user_client.run_until_disconnected(),
                log_bot.run_until_disconnected(),
            )
            worker_task.cancel()

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Supervisor Restart] Connection/runtime issue: {e}. Restarting in 10s...")
            set_live_status(f"Restarting in 10s: {str(e)[:60]}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(run_supervisor())
    except KeyboardInterrupt:
        print("Stopped by user.")
