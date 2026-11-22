import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import random
import threading
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, PeerFloodError, UserPrivacyRestrictedError
from telethon.sessions import StringSession

# 1. StringSession pehle se set kar diya gaya hai (env mein na bhi ho toh chalega)
HARDCODED_STRING_SESSION = "1BVtsOIEBu2ybVNoRmLLk82sbjsbBQhEvB7ezu_dnV15Hvfp0QSwzPMVNHST1N77lN_Bs14B_vi7TcIPepOlOMXYpnIgVlGP6fL-BV5RyRK-ngvy25u3oSrhqPUJ1WRuNiwn3g1U3qq2TSEWcBeZIn1ptR_ViTjNEpgf4XInvLeUG1qJ01fH-BJYUnnnNHmW1f4VkDQbaD6z9BymhiUlb5zr-9-3Vz1lU0kT-hWhqbaEqB5vCYtpWjieqiCz1QtpLZQdpBgQnIGdKCb_rC5_LlyAP78eBkmDgSe-pgiAzlNNfR5Ff5hPyE6uFy2PhNumWxDTlFkSjw3alOPMQAEFvLSsqWJt89w8="

api_id = int(os.getenv("TG_API_ID", "32946015"))
api_hash = os.getenv("TG_API_HASH", "318e71a31d57a9d0fa0b9c055ba16ede").strip()

LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN", "9238275421:AAGfzrIaiRR4vADpdnRWB4tVDUtKZhz39b4").strip()
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID", "8731314485"))

STRING_SESSION = (os.getenv("TG_STRING_SESSION", "") or HARDCODED_STRING_SESSION).strip()

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
    "min_delay": 45,
    "max_delay": 90,
    "daily_limit": 25,
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

user_session = StringSession(STRING_SESSION) if STRING_SESSION else os.getenv("USER_SESSION_NAME", "ff_auto_dm_session")
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


# ---------------- BOT CONTROL COMMANDS ----------------

@log_bot.on(events.NewMessage(pattern=r"^/start$"))
async def cmd_start(event):
    if not is_admin(event):
        return
    await event.reply(
        "🤖 <b>Auto-DM Control Bot Ready</b>\n\n"
        "<b>Commands:</b>\n"
        "• <code>/addgroup @group_ya_link</code> — Group add karein\n"
        "• <code>/delgroup @group_ya_link</code> — Group hatayein\n"
        "• <code>/groups</code> — Active groups dekhein\n"
        "• <code>/settimer 45 90</code> — Min & Max delay (sec)\n"
        "• <code>/setlimit 25</code> — Daily max DMs\n"
        "• <code>/pause</code> — DM bhejna rokein\n"
        "• <code>/resume</code> — DM bhejna chalu karein (backoff reset)\n"
        "• <code>/status</code> — Aaj ke DMs & full status",
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
        await event.reply("Usage: <code>/settimer 45 90</code>", parse_mode="html")
        return

    min_d, max_d = int(m1), int(m2)
    if min_d < 15 or max_d < min_d:
        await event.reply("⚠️ Min delay kam se kam <b>15s</b> rakhein aur max delay min se bada/barabar ho.", parse_mode="html")
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
        await event.reply("Usage: <code>/setlimit 25</code>", parse_mode="html")
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
    await event.reply("⏸️ Auto-DM <b>PAUSED</b> kar diya gaya hai.", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/resume$"))
async def cmd_resume(event):
    if not is_admin(event):
        return
    config["paused"] = False
    config["auto_backoff_until"] = 0
    config["flood_until"] = 0
    save_json(CONFIG_FILE, config)
    await event.reply("▶️ Auto-DM <b>RESUMED</b> kar diya gaya hai.", parse_mode="html")


@log_bot.on(events.NewMessage(pattern=r"^/status$"))
async def cmd_status(event):
    if not is_admin(event):
        return
    refresh_daily_counter()
    now_ts = datetime.now().timestamp()
    flood_rem = max(0, int(config.get("flood_until", 0) - now_ts))
    backoff_rem = max(0, int(config.get("auto_backoff_until", 0) - now_ts))

    if config.get("paused"):
        state = "⏸️ Paused"
    elif flood_rem > 0:
        state = "⏳ Flood Cooldown"
    elif backoff_rem > 0:
        state = f"💤 Smart Backoff ({backoff_rem}s left)"
    else:
        state = "🟢 Running"

    groups_str = ", ".join([str(g) for g in config["groups"]]) or "None"
    await event.reply(
        f"📊 <b>Bot Status</b>\n\n"
        f"• <b>State:</b> {state}\n"
        f"• <b>Groups:</b> <code>{groups_str}</code>\n"
        f"• <b>Timer:</b> {config['min_delay']}s – {config['max_delay']}s\n"
        f"• <b>Aaj Bheje Gaye DMs:</b> <b>{config['today_sent']}</b> / {config['daily_limit']}\n"
        f"• <b>Group-wise Aaj:</b>\n{format_group_breakdown()}\n"
        f"• <b>Queue Waiting:</b> {dm_queue.qsize()}\n"
        f"• <b>Total Sent:</b> {len(sent_users)} | <b>Skipped:</b> {len(skipped_users)}\n"
        f"• <b>Flood Left:</b> {flood_rem}s | <b>Backoff Left:</b> {backoff_rem}s",
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
    msg = f"[Queued] {user_info['name']} ({user_info['username']} / {user_info['id']}) from {group_label} | Queue: {dm_queue.qsize()}"
    print(msg)


async def dm_worker():
    consecutive_limit_hits = 0

    while True:
        user_info = await dm_queue.get()
        uid = user_info["id"]
        queued_user_ids.discard(uid)

        try:
            refresh_daily_counter()

            if uid in sent_users or uid in skipped_users:
                continue

            while config.get("paused", False):
                await asyncio.sleep(5)

            now_ts = datetime.now().timestamp()
            flood_until = config.get("flood_until", 0)
            if flood_until > now_ts:
                wait_sec = int(flood_until - now_ts) + 2
                await send_log(f"⏳ <b>FloodWait Cooldown:</b> {wait_sec}s baaki hain...")
                await asyncio.sleep(wait_sec)

            backoff_until = config.get("auto_backoff_until", 0)
            if backoff_until > datetime.now().timestamp():
                wait_sec = int(backoff_until - datetime.now().timestamp()) + 2
                await send_log(f"💤 <b>Cool-Down Active:</b> {wait_sec}s mein agla DM try hoga...")
                await asyncio.sleep(wait_sec)

            if config["today_sent"] >= config["daily_limit"]:
                await send_log(f"🛑 <b>Daily Limit Complete:</b> Aaj ke {config['daily_limit']} DMs poore ho gaye.")
                continue

            delay = random.randint(int(config["min_delay"]), int(config["max_delay"]))
            for rem in range(delay, 0, -1):
                if rem == delay or rem in (30, 15, 5, 1):
                    print(f"[Timer] {rem}s mein {user_info['name']} ({uid}) ko message jayega...")
                await asyncio.sleep(1)

            text_to_send = random.choice(DM_TEMPLATES)
            target_peer = user_info["username"] if user_info["username"].startswith("@") else uid

            try:
                await user_client.send_message(target_peer, text_to_send)
                consecutive_limit_hits = 0

                sent_users.add(uid)
                save_json(SENT_FILE, list(sent_users))

                group_label = user_info["group"]
                increment_group_sent(group_label)

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
                await send_log(
                    f"⏳ <b>FloodWait!</b> {wait_time}s ke liye wait kar rahe hain, phir {user_info['name']} ({uid}) ko retry karenge."
                )

            except UserPrivacyRestrictedError:
                skipped_users.add(uid)
                save_json(SKIPPED_FILE, list(skipped_users))
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

                    await send_log(
                        f"⚠️ <b>Non-Contact Limit / Account Warning!</b>\n"
                        f"• <b>User:</b> {user_info['name']} ({user_info['username']} / <code>{uid}</code>)\n"
                        f"• <b>Cool-Down:</b> {cool_sec // 60} min ({cool_sec}s)\n"
                        f"• <b>Streak:</b> {consecutive_limit_hits}\n"
                        f"• <b>Error:</b> <code>{str(e)}</code>\n"
                        f"• Cool-down ke baad aage ke users par apne aap chalega."
                    )
                else:
                    skipped_users.add(uid)
                    save_json(SKIPPED_FILE, list(skipped_users))
                    await send_log(
                        f"❌ <b>DM Failed (Skipped):</b> {user_info['name']} ({user_info['username']} / <code>{uid}</code>)\n"
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

            if not user_client.is_connected():
                await user_client.connect()
            await user_client.start()

            await resolve_log_target()
            worker_task = asyncio.create_task(dm_worker())

            await send_log(
                f"🚀 <b>Auto-DM 24/7 Bot Started!</b>\n"
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
            await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(run_supervisor())
    except KeyboardInterrupt:
        print("Stopped by user.")
