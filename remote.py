import os
import asyncio
import traceback
from datetime import datetime, timedelta
from telegram import Update
from telegram.constants import ParseMode
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)

# === CONFIGURATION ===
TOKEN = "7531294790:AAE-gEbMW4AhsRRKCHLGT1-QqfDdEAwTi4c"
ADMIN_ID = 6821755959

UPLOAD_DIR = os.path.abspath(os.path.dirname(__file__))
HISTORY_FILE = os.path.expanduser("~/.vpsbot_history.log")

# Command rate limiting (seconds)
RATE_LIMIT_SECONDS = 5

# For /fetch, limit walk to these dirs to avoid slow traversal
FETCH_SEARCH_PATHS = ["/tmp", "/var/log", "/home", "/root"]

# === GLOBAL STATE ===
last_exception = {}
last_sent_file = {}
confirm_action = {}

# Per-user last command time for rate limiting
user_last_command_time = {}

# === UTILITIES ===
def log_history(entry: str):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {entry}\n")

def safe_handler(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await func(update, context)
        except Exception as e:
            chat_id = update.effective_chat.id
            last_exception[chat_id] = traceback.format_exc()
            await update.message.reply_text("❌ *An error occurred.*", parse_mode="MarkdownV2")
            if is_admin(update.effective_user.id):
                await update.message.reply_text(
                    f"🧨 *Traceback:*\n```{traceback.format_exc()}```",
                    parse_mode="MarkdownV2"
                )
    return wrapper

async def run_command(cmd: str, timeout=20) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "❌ Error: Command timed out."
        output = output_bytes.decode('utf-8', errors='ignore')
        return output[:4000]
    except Exception as e:
        return f"❌ Exception:\n{str(e)}"

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def can_execute_command(user_id: int) -> bool:
    now = datetime.now()
    last_time = user_last_command_time.get(user_id)
    if last_time and (now - last_time) < timedelta(seconds=RATE_LIMIT_SECONDS):
        return False
    user_last_command_time[user_id] = now
    return True

# === HANDLERS ===

@safe_handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        await update.message.reply_text("✅ VPS Control Bot ready.")
    else:
        await update.message.reply_text("❌ Unauthorized.")

@safe_handler
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "*🧾 Available Commands:*\n\n"
        "🔹 `/start` — Start the bot\n"
        "🔹 `/help` — Show this help message\n"
        "📄 `/logs` — Show recent system logs\n"
        "💥 `/exception` — Show last exception trace\n"
        "📜 `/history` — Show last 20 command history entries\n"
        "📤 `/upload` — Upload a file to VPS\n"
        "📥 `/div /path/to/file` — Download file from VPS\n"
        "🔍 `/fetch filename` — Search & send file (limited dirs)\n"
        "🗜 `/zip /path/to/file_or_dir` — Zip and send file/directory\n"
        "🧾 `/override` — Resend last sent file\n"
        "💻 `/cmd <command>` — Run shell command *(admin only, rate-limited)*\n"
        "⚠️ *Other text* will be run as shell command *(admin only, rate-limited)*\n"
        "⛔ `/shutdown` — Shutdown VPS *(admin only)*\n"
        "🔁 `/reboot` — Reboot VPS *(admin only)*"
    )

    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

@safe_handler
async def override(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    chat_id = update.effective_chat.id
    file_path = last_sent_file.get(chat_id)
    if file_path and os.path.isfile(file_path):
        await send_file(update, context, file_path, override=True)
    else:
        await update.message.reply_text("⚠️ No file to override.")

@safe_handler
async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized.")
        return

    log_file = "/var/log/syslog" if os.path.isfile("/var/log/syslog") else "/var/log/messages"
    output = await run_command(f"tail -n 50 {log_file}")
    await update.message.reply_text(f"📜 Logs from `{log_file}`:\n\n{output}")

@safe_handler
async def exception_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    exc = last_exception.get(chat_id)
    if exc:
        await update.message.reply_text(f"🧨 Last exception:\n\n{exc[:4000]}")
    else:
        await update.message.reply_text("✅ No exceptions recorded.")

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.isfile(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()[-20:]
        await update.message.reply_text("📚 Last 20 history entries:\n" + ''.join(lines))
    else:
        await update.message.reply_text("📚 No history recorded yet.")

@safe_handler
async def send_file(update: Update, context: ContextTypes.DEFAULT_TYPE, file_path: str, override=False):
    chat_id = update.effective_chat.id
    if not os.path.isfile(file_path):
        await update.message.reply_text("❌ File not found.")
        return
    if last_sent_file.get(chat_id) == file_path and not override:
        await update.message.reply_text("⚠️ File already sent. Use /override to resend.")
        return
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        with open(file_path, 'rb') as f:
            await update.message.reply_document(document=f, filename=os.path.basename(file_path))
        last_sent_file[chat_id] = file_path
        log_history(f"Sent file: {file_path}")
    except Exception:
        last_exception[chat_id] = traceback.format_exc()
        await update.message.reply_text("❌ Error sending file.")

@safe_handler
async def download_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /div /path/to/file")
        return
    file_path = ' '.join(context.args).strip()
    await send_file(update, context, file_path)

@safe_handler
async def smart_fetch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /fetch filename")
        return
    filename = context.args[0]
    # Search limited paths to avoid slow full disk search
    for base_path in FETCH_SEARCH_PATHS:
        for root, _, files in os.walk(base_path):
            if filename in files:
                full_path = os.path.join(root, filename)
                await send_file(update, context, full_path)
                return
    await update.message.reply_text("❌ File not found.")

@safe_handler
async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📥 Send the file you want to upload.")
    return 1

@safe_handler
async def receive_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)
    path = os.path.join(UPLOAD_DIR, doc.file_name)
    await doc.get_file().download_to_drive(path)
    await update.message.reply_text(f"✅ Uploaded to {path}")
    log_history(f"Uploaded file: {path}")
    return ConversationHandler.END

@safe_handler
async def zip_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /zip /path/to/file_or_dir")
        return
    target = context.args[0].strip()
    if not os.path.exists(target):
        await update.message.reply_text("❌ Path not found.")
        return
    zip_path = f"/tmp/{os.path.basename(target)}.zip"
    cmd = f"zip -r {zip_path} {target}"
    output = await run_command(cmd)
    await send_file(update, context, zip_path)

@safe_handler
async def confirm_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    chat_id = update.effective_chat.id
    confirm_action[chat_id] = "shutdown"
    await update.message.reply_text("⚠️ Confirm shutdown? Send /yes or /no")

@safe_handler
async def confirm_reboot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    chat_id = update.effective_chat.id
    confirm_action[chat_id] = "reboot"
    await update.message.reply_text("⚠️ Confirm reboot? Send /yes or /no")

@safe_handler
async def confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    action = confirm_action.get(chat_id)
    if action == "shutdown":
        await update.message.reply_text("🛑 Shutting down...")
        asyncio.create_task(run_command("shutdown now"))
    elif action == "reboot":
        await update.message.reply_text("🔁 Rebooting...")
        asyncio.create_task(run_command("reboot"))
    else:
        await update.message.reply_text("❌ No action to confirm.")
    confirm_action.pop(chat_id, None)

@safe_handler
async def confirm_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    confirm_action.pop(chat_id, None)
    await update.message.reply_text("❌ Cancelled.")

@safe_handler
async def run_background_command(cmd: str):
    # Run command detached without waiting for output or blocking
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # Don't wait or capture output; process runs in background


@safe_handler
async def cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized.")
        return

    if not can_execute_command(user_id):
        await update.message.reply_text(f"⏳ Rate limit: Please wait {RATE_LIMIT_SECONDS} seconds between commands.")
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: /cmd <command>")
        return

    command = ' '.join(context.args).strip()

    # If the command is intended to run in background (nohup or ends with &), don't await output
    if command.startswith("nohup") or command.endswith("&"):
        # Remove trailing '&' if present for subprocess compatibility
        cmd_to_run = command.rstrip("&").strip()
        asyncio.create_task(run_background_command(cmd_to_run))
        await update.message.reply_text(f"▶️ Started background command:\n`{command}`")
        log_history(f"Background command by {user_id}: {command}")
        return

    # Otherwise, run normally and wait for output
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await update.message.reply_text(f"▶️ Running command:\n`{command}`")
    output = await run_command(command)
    log_history(f"Command by {user_id}: {command}\nOutput: {output[:100]}...")
    await update.message.reply_text(f"🖥 Output:\n{output}")

@safe_handler
async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized.")
        return

    if not can_execute_command(user_id):
        await update.message.reply_text(f"⏳ Rate limit: Please wait {RATE_LIMIT_SECONDS} seconds between commands.")
        return

    command = update.message.text.strip()

    if command.startswith("nohup") or command.endswith("&"):
        cmd_to_run = command.rstrip("&").strip()
        asyncio.create_task(run_background_command(cmd_to_run))
        await update.message.reply_text(f"▶️ Started background command:\n`{command}`")
        log_history(f"Background command by {user_id}: {command}")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await update.message.reply_text(f"▶️ Running command:\n`{command}`")
    output = await run_command(command)
    log_history(f"Command by {user_id}: {command}\nOutput: {output[:100]}...")
    await update.message.reply_text(f"🖥 Output:\n{output}")

# === MAIN ===
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    upload_handler = ConversationHandler(
        entry_points=[CommandHandler("upload", safe_handler(upload))],
        states={1: [MessageHandler(filters.Document.ALL, safe_handler(receive_upload))]},
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", safe_handler(start)))
    app.add_handler(CommandHandler("help", safe_handler(help_command)))
    app.add_handler(CommandHandler("override", safe_handler(override)))
    app.add_handler(CommandHandler("logs", safe_handler(logs)))
    app.add_handler(CommandHandler("exception", safe_handler(exception_log)))
    app.add_handler(CommandHandler("history", safe_handler(history)))
    app.add_handler(CommandHandler("div", safe_handler(download_file)))
    app.add_handler(CommandHandler("fetch", safe_handler(smart_fetch)))
    app.add_handler(CommandHandler("zip", safe_handler(zip_file)))
    app.add_handler(CommandHandler("shutdown", safe_handler(confirm_shutdown)))
    app.add_handler(CommandHandler("reboot", safe_handler(confirm_reboot)))
    app.add_handler(CommandHandler("yes", safe_handler(confirm_yes)))
    app.add_handler(CommandHandler("no", safe_handler(confirm_no)))
    app.add_handler(CommandHandler("cmd", safe_handler(cmd_command)))
    app.add_handler(upload_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, safe_handler(handle_command)))

    print("✅ Ultimate VPS Bot Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
