import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import random
import threading
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, PeerFloodError, SessionPasswordNeededError, UserPrivacyRestrictedError
from telethon.sessions import StringSession

# =========================================================================
# NUMBER + CODE VERIFICATION & AUTO-DM FLOW
# 1. Phone number (+91...) aap yahan HARDCODED_PHONE mein ya Render/hosting Env
#    mein TG_PHONE daal sakte hain (ya start hone ke baad /login +91... bhi bhej sakte hain).
# 2. Telegram jo login OTP/code bhejta hai, wo aap TG_LOGIN_CODE (env) ya
#    phone_code.txt file mein daal sakte hain (ya /code 12345 command se bhi de sakte hain).
# 3. Verify hote hi session save/print hoga aur usi verify hue number se DM chalega.
# =========================================================================
HARDCODED_STRING_SESSION = ""
HARDCODED_PHONE = ""  # Example: "+919876543210"

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

DM_TEMPLATES = [
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

DEFAULT_CONFIG = {
    "groups": ["@FreeFireLikes69", "@noobnation69"],
    "min_delay": 60,
    "max_delay": 120,
    "daily_limit": 15,
    "paused": False,
    "auto_backoff_until": 0,
    "today_date": datetime.now().strftime("%Y-%m-%d"),
    "today_sent": 0,
    "today_by_group": {},
    "flood_until": 0,
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

normalized_groups = []
for g in config.get("groups", []):
    g_clean = str(g).replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "").strip()
    if g_clean and not g_clean.lstrip("-").isdigit() and not g_clean.startswith("@"):
        g_clean = "@" + g_clean
    if g_clean not in normalized_groups:
        normalized_groups.append(g_clean)

if "@noobnation69" not in [x.lower() for x in normalized_groups]:
    normalized_groups.append("@noobnation69")

config["groups"] = normalized_groups
save_json(CONFIG_FILE, config)

sent_users = set(load_json(SENT_FILE, []))
save_json(SENT_FILE, list(sent_users))

skipped_users = set(load_json(SKIPPED_FILE, []))
save_json(SKIPPED_FILE, list(skipped_users))

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

auth_state = {
    "verified": False,
    "phone": PHONE_NUMBER,
    "phone_code_hash": None,
    "step": "idle",  # idle -> waiting_phone -> waiting_code -> waiting_2fa -> verified
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
        config["today_by_group"] = {}
        config["auto_backoff_until"] = 0
        save_json(CONFIG_FILE, config)
    elif not isinstance(config.get("today_by_group"), dict):
        config["today_by_group"] = {}
        save_json(CONFIG_FILE, config)


def increment_group_sent(group_label: str):
    refresh_daily_counter()
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


def _read_value_from_env_or_file(env_keys, file_path):
    for k in env_keys:
        val = os.getenv(k, "").strip()
        if val:
            return val
    txt = _read_text_file(file_path)
    if txt:
        return txt
    return ""


async def _mark_verified_and_announce():
    me = await user_client.get_me()
    name = getattr(me, "first_name", "") or ""
    uname = f"@{me.username}" if getattr(me, "username", None) else ""
    phone_str = auth_state.get("phone") or getattr(me, "phone", "") or "Verified Account"
    label = f"{name} {uname} ({phone_str})".strip()
    auth_state["verified"] = True
    auth_state["step"] = "verified"
    auth_state["me_label"] = label
    set_live_status(f"Verified ({label}) — groups aur queue ke liye ready")

    exported = user_client.session.save() if isinstance(user_client.session, StringSession) else ""
    if exported:
        _write_text_file(SESSION_SAVE_FILE, exported)
        print(f"[Auth] Verified StringSession: {exported}")

    await send_log(
        f"✅ <b>Number Verify Ho Gaya!</b>\n"
        f"• <b>Account:</b> <code>{label}</code>\n"
        f"• Ab isi verified number se DM queue chalega."
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
        f"• Telegram par aaya hua code yahan bhejein: <code>/code 12345</code>\n"
        f"  (Ya env <code>TG_LOGIN_CODE</code> / <code>phone_code.txt</code> mein daalein)"
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
        set_live_status("2FA password ka intezar (/pass)")
        await send_log(
            "🔑 <b>2FA Password Required!</b>\n"
            "Is account par 2-step verification laga hai. Password bhejein: <code>/pass aapka_password</code>"
        )


async def verify_2fa_password(pwd_str: str):
    pwd_clean = pwd_str.strip()
    await user_client.sign_in(password=pwd_clean)
    await _mark_verified_and_announce()


async def run_auth_flow():
    if not user_client.is_connected():
        await user_client.connect()

    if await user_client.is_user_authorized():
        await _mark_verified_and_announce()
        return

    preset_phone = PHONE_NUMBER or _read_value_from_env_or_file(["TG_PHONE", "PHONE_NUMBER"], "phone_number.txt")
    if preset_phone:
        auth_state["phone"] = preset_phone
        await send_login_code_for_phone(preset_phone)
        preset_code = _read_value_from_env_or_file(["TG_LOGIN_CODE", "TG_PHONE_CODE", "PHONE_CODE"], CODE_FILE)
        if preset_code:
            try:
                await verify_login_code(preset_code)
                return
            except Exception as e:
                await send_log(f"⚠️ Preset code se login nahi hua: <code>{e}</code>")
                preset_2fa = _read_value_from_env_or_file(["TG_2FA_PASSWORD", "TWO_STEP_PASSWORD"], PASSWORD_2FA_FILE)
                if preset_2fa and auth_state.get("step") == "waiting_2fa":
                    await verify_2fa_password(preset_2fa)
                    return
    else:
        auth_state["step"] = "waiting_phone"
        set_live_status("Number verification pending — /login bhejein")
        await send_log(
            "📱 <b>Number Verification Pending</b>\n"
            "• Number verify karne ke liye bhejein: <code>/login +91XXXXXXXXXX</code>\n"
            "• Phir code bhejein: <code>/code 12345</code>\n"
            "• Verify hote hi usi number se DM shuru ho jayega."
        )

    while not auth_state.get("verified"):
        await asyncio.sleep(3)


# ---------------- BOT CONTROL COMMANDS ----------------

@log_bot.on(events.NewMessage(pattern=r"^/login(?:\s+(.+))?$"))
async def cmd_login(event):
    if not is_admin(event):
        return
    arg = (event.pattern_match.group(1) or "").strip()
    if not arg:
        await event.reply("Usage: <code>/login +919876543210</code>", parse_mode="html")
        return
    try:
        if not user_client.is_connected():
            await user_client.connect()
        await send_login_code_for_phone(arg)
    except Exception as e:
        await event.reply(f"❌ OTP send error: <code>{e}</code>", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/code(?:\s+(.+))?$"))
async def cmd_code(event):
    if not is_admin(event):
        return
    arg = (event.pattern_match.group(1) or "").strip()
    if not arg:
        await event.reply("Usage: <code>/code 12345</code>", parse_mode="html")
        return
    try:
        await verify_login_code(arg)
    except Exception as e:
        await event.reply(f"❌ Code verification failed: <code>{e}</code>", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/pass(?:\s+(.+))?$"))
async def cmd_pass(event):
    if not is_admin(event):
        return
    arg = (event.pattern_match.group(1) or "").strip()
    if not arg:
        await event.reply("Usage: <code>/pass your_2fa_password</code>", parse_mode="html")
        return
    try:
        await verify_2fa_password(arg)
    except Exception as e:
        await event.reply(f"❌ 2FA password failed: <code>{e}</code>", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/start$"))
async def cmd_start(event):
    if not is_admin(event):
        return
    await event.reply(
        "🤖 <b>Auto-DM Control Bot Ready</b>\n\n"
        "<b>Number Verification:</b>\n"
        "• <code>/login +91XXXXXXXXXX</code> — Number par code bhejein\n"
        "• <code>/code 12345</code> — Code verify karein\n"
        "• <code>/pass password</code> — 2FA password (agar ho toh)\n\n"
        "<b>DM & Group Commands:</b>\n"
        "• <code>/addgroup @group_ya_link</code> — Group add karein\n"
        "• <code>/delgroup @group_ya_link</code> — Group hatayein\n"
        "• <code>/groups</code> — Active groups dekhein\n"
        "• <code>/settimer 60 120</code> — Min & Max delay (sec)\n"
        "• <code>/setlimit 15</code> — Daily max DMs\n"
        "• <code>/pause</code> — DM bhejna rokein\n"
        "• <code>/resume</code> — DM bhejna chalu karein (backoff reset)\n"
        "• <code>/status</code> — Aaj ke DMs, timer & full status",
        parse_mode="html",
    )


@log_bot.on(events.NewMessage(pattern=r"^/addgroup(?:\s+(.+))?$"))
async def cmd_addgroup(event):
    if not is_admin(event):
        return
    arg = event.pattern_match.group(1)
    if not arg:
        await event.reply("Usage: <code>/addgroup https://t.me/noobnation69</code> ya <code>/addgroup @groupname</code>", parse_mode="html")
        return

    target = parse_chat_target(arg)
    if target in config["groups"]:
        await event.reply(f"⚠️ Group pehle se list mein hai: <code>{target}</code>", parse_mode="html")
        return

    try:
        entity = await user_client.get_entity(target)
        title = getattr(entity, "title", str(target))
    except Exception as e:
        await event.reply(
            f"⚠️ Group resolve nahi hua: <code>{target}</code>\nError: <code>{e}</code>",
            parse_mode="html",
        )
        return

    config["groups"].append(target)
    save_json(CONFIG_FILE, config)
    await event.reply(f"✅ Group add ho gaya: <b>{title}</b> (<code>{target}</code>)", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/delgroup(?:\s+(.+))?$"))
async def cmd_delgroup(event):
    if not is_admin(event):
        return
    arg = event.pattern_match.group(1)
    if not arg:
        await event.reply("Usage: <code>/delgroup @groupname</code> ya ID", parse_mode="html")
        return

    target = parse_chat_target(arg)
    matches = [g for g in config["groups"] if str(g).lower() == str(target).lower()]
    if not matches:
        await event.reply(f"❌ Group list mein nahi mila: <code>{target}</code>", parse_mode="html")
        return

    for m in matches:
        config["groups"].remove(m)
    save_json(CONFIG_FILE, config)
    await event.reply(f"🗑️ Group hata diya gaya: <code>{target}</code>", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/groups$"))
async def cmd_groups(event):
    if not is_admin(event):
        return
    if not config["groups"]:
        await event.reply("📭 Koi active group set nahi hai.", parse_mode="html")
        return
    lines = "\n".join([f"• <code>{g}</code>" for g in config["groups"]])
    await event.reply(f"📢 <b>Active Groups ({len(config['groups'])}):</b>\n{lines}", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/settimer(?:\s+(\d+)\s+(\d+))?$"))
async def cmd_settimer(event):
    if not is_admin(event):
        return
    m1 = event.pattern_match.group(1)
    m2 = event.pattern_match.group(2)
    if not m1 or not m2:
        await event.reply("Usage: <code>/settimer 60 120</code>", parse_mode="html")
        return

    min_d, max_d = int(m1), int(m2)
    if min_d < 30 or max_d < min_d:
        await event.reply("⚠️ Safe timer ke liye min delay kam se kam <b>30s</b> rakhein aur max delay min se bada/barabar ho.", parse_mode="html")
        return

    config["min_delay"] = min_d
    config["max_delay"] = max_d
    save_json(CONFIG_FILE, config)
    await event.reply(f"⏱️ Timer updated: <b>{min_d}s – {max_d}s</b>", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/setlimit(?:\s+(\d+))?$"))
async def cmd_setlimit(event):
    if not is_admin(event):
        return
    val = event.pattern_match.group(1)
    if not val:
        await event.reply("Usage: <code>/setlimit 15</code>", parse_mode="html")
        return
    limit = int(val)
    if limit < 1:
        await event.reply("⚠️ Limit kam se kam 1 honi chahiye.", parse_mode="html")
        return
    config["daily_limit"] = limit
    save_json(CONFIG_FILE, config)
    await event.reply(f"🛡️ Daily DM limit set to: <b>{limit}</b>/day", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/pause$"))
async def cmd_pause(event):
    if not is_admin(event):
        return
    config["paused"] = True
    save_json(CONFIG_FILE, config)
    set_live_status("Bot PAUSED (/resume se chalu karein)")
    await event.reply("⏸️ Auto-DM <b>PAUSED</b> kar diya gaya hai.", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/resume$"))
async def cmd_resume(event):
    if not is_admin(event):
        return
    config["paused"] = False
    config["auto_backoff_until"] = 0
    config["flood_until"] = 0
    save_json(CONFIG_FILE, config)
    set_live_status("Bot RESUMED — agla DM queue se uthayega")
    await event.reply("▶️ Auto-DM <b>RESUMED</b> kar diya gaya hai.", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/status$"))
async def cmd_status(event):
    if not is_admin(event):
        return
    refresh_daily_counter()
    now_ts = datetime.now().timestamp()
    flood_rem = max(0, int(config.get("flood_until", 0) - now_ts))
    backoff_rem = max(0, int(config.get("auto_backoff_until", 0) - now_ts))
    eta_ts = float(live_task_state.get("eta_timestamp") or 0.0)
    next_dm_rem = max(0, int(eta_ts - now_ts)) if eta_ts > now_ts else 0

    if not auth_state.get("verified"):
        state = f"🔐 Verify Pending ({auth_state.get('step')})"
    elif config.get("paused"):
        state = "⏸️ Paused"
    elif flood_rem > 0:
        state = "⏳ Flood Cooldown"
    elif backoff_rem > 0:
        state = f"💤 Smart Backoff ({backoff_rem}s left)"
    else:
        state = "🟢 Running"

    groups_str = ", ".join([str(g) for g in config["groups"]]) or "None"
    acc_label = auth_state.get("me_label") or auth_state.get("phone") or "Not Verified"

    if live_task_state.get("active_user") and next_dm_rem > 0:
        next_dm_line = (
            f"<b>{next_dm_rem}s</b> mein <code>{live_task_state['active_user']}</code> "
            f"(<code>{live_task_state['active_uid']}</code>) ko jayega"
        )
    elif dm_queue.qsize() > 0:
        next_dm_line = f"Queue mein {dm_queue.qsize()} user tayyar, agla timer shuru ho raha hai"
    else:
        next_dm_line = "Abhi queue khaali hai (group message ka intezar)"

    await event.reply(
        f"📊 <b>Bot Status</b>\n\n"
        f"• <b>State:</b> {state}\n"
        f"• <b>Verified Account:</b> <code>{acc_label}</code>\n"
        f"• <b>Groups:</b> <code>{groups_str}</code>\n"
        f"• <b>Timer:</b> {config['min_delay']}s – {config['max_delay']}s\n"
        f"• <b>Agla DM Timer:</b> {next_dm_line}\n"
        f"• <b>Aaj Bheje Gaye DMs:</b> <b>{config['today_sent']}</b> / {config['daily_limit']}\n"
        f"• <b>Group-wise Aaj:</b>\n{format_group_breakdown()}\n"
        f"• <b>Queue Waiting:</b> {dm_queue.qsize()}\n"
        f"• <b>Total Sent:</b> {len(sent_users)} | <b>Skipped:</b> {len(skipped_users)}\n"
        f"• <b>Flood Left:</b> {flood_rem}s | <b>Backoff Left:</b> {backoff_rem}s\n"
        f"• <b>Live Activity ({live_task_state['last_updated']}):</b> {live_task_state['last_event']}",
        parse_mode="html",
    )


# ---------------- GROUP LISTENER & QUEUE WORKER ----------------

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
    msg = (
        f"[Queued] {user_info['name']} ({user_info['username']} / {user_info['id']}) "
        f"from {group_label} | Queue: {dm_queue.qsize()}"
    )
    print(msg)
    set_live_status(f"Queued {user_info['name']} ({user_info['username']}) from {group_label}")


async def dm_worker():
    consecutive_limit_hits = 0

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

            now_ts = datetime.now().timestamp()
            flood_until = config.get("flood_until", 0)
            if flood_until > now_ts:
                wait_sec = int(flood_until - now_ts) + 2
                set_live_status(f"FloodWait cooldown ({wait_sec}s baaki)")
                await send_log(f"⏳ <b>FloodWait Cooldown:</b> {wait_sec}s baaki hain...")
                await asyncio.sleep(wait_sec)

            backoff_until = config.get("auto_backoff_until", 0)
            if backoff_until > datetime.now().timestamp():
                wait_sec = int(backoff_until - datetime.now().timestamp()) + 2
                set_live_status(f"Cool-Down active ({wait_sec}s baaki)")
                await send_log(f"💤 <b>Cool-Down Active:</b> {wait_sec}s mein agla DM try hoga...")
                await asyncio.sleep(wait_sec)

            if config["today_sent"] >= config["daily_limit"]:
                set_live_status(f"Daily limit ({config['daily_limit']}) poori ho chuki hai")
                await send_log(f"🛑 <b>Daily Limit Complete:</b> Aaj ke {config['daily_limit']} DMs poore ho gaye.")
                continue

            delay = random.randint(int(config["min_delay"]), int(config["max_delay"]))
            eta_ts = datetime.now().timestamp() + delay
            set_live_status(
                f"{delay}s timer chal raha hai — {user_info['name']} ({user_info['username']}) ko DM jayega",
                active_user=user_info["username"],
                active_uid=uid,
                active_group=user_info["group"],
                eta_timestamp=eta_ts,
                total_delay=delay,
            )
            await send_log(
                f"⏱️ <b>Timer Start ({delay}s):</b> <b>{user_info['name']}</b> ({user_info['username']} / <code>{uid}</code>) "
                f"ko <b>{delay}s</b> baad message bheja jayega | Group: <code>{user_info['group']}</code>"
            )

            for rem in range(delay, 0, -1):
                if rem == delay or rem in (30, 15, 5, 1):
                    print(f"[Timer] {rem}s mein {user_info['name']} ({uid}) ko message jayega...")
                await asyncio.sleep(1)

            set_live_status(
                f"Abhi {user_info['name']} ({user_info['username']}) ko DM bhej rahe hain...",
                active_user=user_info["username"],
                active_uid=uid,
                active_group=user_info["group"],
                eta_timestamp=0.0,
                total_delay=delay,
            )

            text_to_send = random.choice(DM_TEMPLATES)
            target_peer = user_info["username"] if user_info["username"].startswith("@") else uid

            try:
                await user_client.send_message(target_peer, text_to_send)
                consecutive_limit_hits = 0

                sent_users.add(uid)
                save_json(SENT_FILE, list(sent_users))

                group_label = user_info["group"]
                increment_group_sent(group_label)

                set_live_status(f"DM sent to {user_info['name']} ({user_info['username']}) in {delay}s")

                log_msg = (
                    f"✅ <b>Message Sent!</b>\n\n"
                    f"👤 <b>Name:</b> {user_info['name']}\n"
                    f"🔗 <b>Username:</b> {user_info['username']}\n"
                    f"🆔 <b>User ID:</b> <code>{uid}</code>\n"
                    f"📢 <b>Group:</b> <code>{group_label}</code>\n"
                    f"⏱️ <b>Delay Used:</b> {delay}s\n"
                    f"📅 <b>Aaj Bheje Gaye (Total):</b> <b>{config['today_sent']}</b> / {config['daily_limit']}\n"
                    f"📌 <b>Aaj Is Group Se:</b> <b>{config['today_by_group'].get(group_label, 0)}</b>\n"
                    f"📊 <b>All-Time Total Sent:</b> {len(sent_users)}"
                )
                await send_log(log_msg)

            except FloodWaitError as fw:
                wait_time = int(fw.seconds) + 20
                config["flood_until"] = datetime.now().timestamp() + wait_time
                save_json(CONFIG_FILE, config)
                queued_user_ids.add(uid)
                await dm_queue.put(user_info)
                set_live_status(f"FloodWait {wait_time}s — {user_info['name']} retry queue mein")
                await send_log(
                    f"⏳ <b>FloodWait!</b> {wait_time}s ke liye wait kar rahe hain, phir {user_info['name']} ({uid}) ko retry karenge."
                )

            except UserPrivacyRestrictedError:
                skipped_users.add(uid)
                save_json(SKIPPED_FILE, list(skipped_users))
                set_live_status(f"Skipped (Privacy): {user_info['name']} ({user_info['username']})")
                await send_log(
                    f"🔒 <b>Privacy Restricted (Skipped):</b> {user_info['name']} ({user_info['username']} / <code>{uid}</code>)"
                )

            except Exception as e:
                if is_non_contact_limit_error(e):
                    consecutive_limit_hits += 1
                    skipped_users.add(uid)
                    save_json(SKIPPED_FILE, list(skipped_users))

                    backoff_steps = [900, 2700, 7200]
                    idx = min(consecutive_limit_hits - 1, len(backoff_steps) - 1)
                    cool_sec = backoff_steps[idx]
                    config["auto_backoff_until"] = datetime.now().timestamp() + cool_sec
                    save_json(CONFIG_FILE, config)

                    set_live_status(
                        f"Non-Contact Limit ({cool_sec // 60}m cool-down) — {user_info['username']} skip hua"
                    )
                    await send_log(
                        f"⚠️ <b>Non-Contact Limit / Account Warning!</b>\n"
                        f"• <b>User:</b> {user_info['name']} ({user_info['username']} / <code>{uid}</code>)\n"
                        f"• <b>Timer laga tha:</b> {delay}s\n"
                        f"• <b>Cool-Down:</b> {cool_sec // 60} min ({cool_sec}s)\n"
                        f"• <b>Streak:</b> {consecutive_limit_hits}\n"
                        f"• <b>Error:</b> <code>{str(e)}</code>\n"
                        f"• Cool-down ke baad aage ke users par apne aap chalega."
                    )
                else:
                    skipped_users.add(uid)
                    save_json(SKIPPED_FILE, list(skipped_users))
                    set_live_status(f"Failed ({user_info['username']}): {str(e)[:60]}")
                    await send_log(
                        f"❌ <b>DM Failed (Skipped):</b> {user_info['name']} ({user_info['username']} / <code>{uid}</code>)\n"
                        f"• <b>Timer laga tha:</b> {delay}s\n"
                        f"⚠️ <b>Reason:</b> <code>{str(e)}</code>"
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
                f"• <b>Groups:</b> <code>{', '.join(map(str, config['groups']))}</code>\n"
                f"• <b>Timer:</b> {config['min_delay']}s – {config['max_delay']}s\n"
                f"• <b>Daily Limit:</b> {config['daily_limit']}\n"
                f"• <b>Aaj Bheje Gaye DMs:</b> <b>{config['today_sent']}</b>"
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
