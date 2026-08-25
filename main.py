#!/usr/bin/env python3
try:
    import speedtest
    HAS_SPEEDTEST = True
except ImportError:
    HAS_SPEEDTEST = False
import zipfile
import time
import asyncio
import os
import subprocess
import sys
import psutil
import logging
import shutil
import platform
import socket
import pty
import urllib.request
import datetime
import re
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    # Load environment variables
    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "7904426866:AAEQVE_mcJcZMCJjD8hkBhEKqLQjou9LJBs")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7660990923"))

# Global variable to store the currently running process (for /kill)
current_process = None
# Global variable to store the active master file descriptor for inputs
current_master_fd = None
# Global variable for interactive mode
interactive_mode = False
# Dictionary to store custom aliases
aliases = {}
# Dictionary to store active scheduled tasks
scheduled_tasks = {}
task_counter = 1
# MongoDB Setup
MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb+srv://raj:krishna@cluster0.eq8xrjs.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
try:
    # 5 second timeout to prevent the bot from hanging forever if IP is not
    # whitelisted
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    # Test connection quickly to trigger error if auth or IP is blocked
    mongo_client.admin.command('ping')
    db = mongo_client["terminal_bot"]
    admins_collection = db["admins"]
except Exception as e:
    logger.error(f"Error connecting to MongoDB: {e}")
    admins_collection = None

# Set to store extra authorized admin IDs
extra_admins = set()


def load_extra_admins():
    """Load extra admin IDs from MongoDB."""
    if admins_collection is None:
        return
    try:
        docs = admins_collection.find({})
        for doc in docs:
            if "user_id" in doc:
                extra_admins.add(int(doc["user_id"]))
        logger.info(f"Loaded {len(extra_admins)} extra admins from MongoDB.")
    except Exception as e:
        logger.error(f"Error loading extra admins from MongoDB: {e}")


load_extra_admins()

# Store current working directory
# Change initial directory to the user's home directory
try:
    os.chdir(os.path.expanduser("~"))
except Exception:
    pass
current_dir = os.getcwd()


def is_admin(update: Update) -> bool:
    """Check if the user is an authorized admin."""
    user_id = update.effective_user.id
    return user_id == ADMIN_ID or user_id in extra_admins


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message when /start is issued."""
    if not is_admin(update):
        return
    await update.message.reply_text(
        "Welcome to the Terminal Bot! Send any shell command to execute it.\n"
        "Use '/cd <path>' to change directories. Current directory: " + current_dir
    )


async def cd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cd command to change directories."""
    if not is_admin(update):
        return
    global current_dir
    path = " ".join(context.args).strip()
    if not path:
        await update.message.reply_text("Please specify a directory. Usage: /cd <path>")
        return

    # Expand ~ to user's home directory
    path = os.path.expanduser(path)

    try:
        # Attempt to change directory
        os.chdir(path)
        current_dir = os.getcwd()
        await update.message.reply_text(f"Changed directory to: {current_dir}")
    except FileNotFoundError:
        await update.message.reply_text(f"Directory not found: {path}")
    except PermissionError:
        await update.message.reply_text(f"Permission denied: {path}")
    except Exception as e:
        await update.message.reply_text(f"Error changing directory: {str(e)}")


async def home_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /home command to instantly jump to the home directory."""
    if not is_admin(update):
        return
    global current_dir
    path = os.path.expanduser("~")
    try:
        os.chdir(path)
        current_dir = os.getcwd()
        await update.message.reply_text(f"Changed directory to Home: {current_dir}")
    except Exception as e:
        await update.message.reply_text(f"Error changing to Home directory: {str(e)}")


async def sysinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sysinfo command to get OS and Network information."""
    if not is_admin(update):
        return
    try:
        uname = platform.uname()
        sys_os = f"{uname.system} {uname.release} ({uname.machine})"

        # Calculate uptime
        boot_time_timestamp = psutil.boot_time()
        bt = datetime.datetime.fromtimestamp(boot_time_timestamp)
        now = datetime.datetime.now()
        uptime = now - bt

        # Get IPs
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        try:
            public_ip = urllib.request.urlopen(
                'https://api.ipify.org', timeout=5).read().decode('utf8')
        except Exception:
            public_ip = "Unknown (Timeout)"

        msg = (
            "🖥️ **System Information**\n\n"
            f"**OS:** `{sys_os}`\n"
            f"**Hostname:** `{uname.node}`\n"
            f"**Python:** `{platform.python_version()}`\n"
            f"**Uptime:** `{str(uptime).split('.')[0]}` (Since {bt.strftime('%Y-%m-%d %H:%M:%S')})\n"
            f"**Local IP:** `{local_ip}`\n"
            f"**Public IP:** `{public_ip}`\n"
        )
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Error getting sysinfo: {str(e)}")


async def speedtest_command(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE):
    """Handle /speedtest command to test server network speed."""
    if not is_admin(update):
        return
    if not HAS_SPEEDTEST:
        await update.message.reply_text("❌ The `speedtest-cli` module is not installed on this server.\nPlease run: `/run pip install speedtest-cli`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text("🚀 Testing Server Internet Speed (This may take a minute)...\n\n1️⃣ Testing Ping...", parse_mode='Markdown')
    try:
        def run_test():
            st = speedtest.Speedtest()
            st.get_best_server()
            return st

        loop = asyncio.get_running_loop()
        st = await loop.run_in_executor(None, run_test)

        await status_msg.edit_text(f"🚀 Testing Server Internet Speed...\n\n✅ Ping: `{st.results.ping} ms`\n2️⃣ Testing Download...", parse_mode='Markdown')
        download = await loop.run_in_executor(None, st.download)

        await status_msg.edit_text(f"🚀 Testing Server Internet Speed...\n\n✅ Ping: `{st.results.ping} ms`\n✅ Download: `{download / 1024 / 1024:.2f} Mbps`\n3️⃣ Testing Upload...", parse_mode='Markdown')
        upload = await loop.run_in_executor(None, st.upload)

        final_msg = (
            "🚀 **Speedtest Results**\n\n"
            f"**Ping:** `{st.results.ping} ms`\n"
            f"**Download:** `{download / 1024 / 1024:.2f} Mbps`\n"
            f"**Upload:** `{upload / 1024 / 1024:.2f} Mbps`\n"
            f"**Server:** `{st.results.server['sponsor']} ({st.results.server['name']})`"
        )
        await status_msg.edit_text(final_msg, parse_mode='Markdown')
    except Exception as e:
        await status_msg.edit_text(f"❌ Error testing speed: {str(e)}")


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ping command to test network connection to a host."""
    if not is_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/ping <host>`\nExample: `/ping google.com`", parse_mode='Markdown')
        return

    host = args[0]
    # Check OS to use correct ping argument (-c for Linux/Mac, -n for Windows)
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    command = ['ping', param, '4', host]

    status_msg = await update.message.reply_text(f"Pinging `{host}`...", parse_mode='Markdown')
    try:
        # Run the command and capture output
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=15)
        output = result.stdout + result.stderr
        if not output:
            output = "Ping command executed with no output."
        if len(output) > 4000:
            output = output[:4000] + "\n[Output truncated...]"
        await status_msg.edit_text(f"📡 **Ping Results for `{host}`:**\n\n```\n{output}\n```", parse_mode='Markdown')
    except subprocess.TimeoutExpired:
        await status_msg.edit_text("❌ Ping timed out after 15 seconds.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error pinging `{host}`: {str(e)}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command to check system resources."""
    if not is_admin(update):
        return
    try:
        cpu_usage = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')

        stats_message = (
            "📊 *System Stats*\n\n"
            f"💻 *CPU Usage:* {cpu_usage}%\n"
            f"🧠 *RAM Usage:* {memory.percent}% ({memory.used // (1024**2)}MB / {memory.total // (1024**2)}MB)\n"
            f"💾 *Disk Usage:* {disk.percent}% ({disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB)"
        )
        await update.message.reply_text(stats_message, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"Error getting stats: {str(e)}")


async def bg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bg command to execute long-running shell commands with live updates."""
    if not is_admin(update):
        return
    global current_process, current_master_fd

    # Prevent running multiple bg commands at once to avoid confusion
    if current_process is not None and current_process.poll() is None:
        await update.message.reply_text("A process is already running. Please /kill it first or wait for it to finish.")
        return

    command = " ".join(context.args).strip()
    if not command:
        await update.message.reply_text("Please provide a command. Usage: /bg <command>")
        return

    try:
        master_fd, slave_fd = pty.openpty()
        current_master_fd = master_fd
        current_process = subprocess.Popen(
            command,
            shell=True,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=current_dir,
            preexec_fn=os.setsid  # Allow killing process group
        )
        os.close(slave_fd)  # Close child fd in parent

        message = await update.message.reply_text(f"🚀 Started background process (PID: {current_process.pid})\nLoading...")

        async def read_output():
            global current_process, current_master_fd
            output_buffer = ""
            last_update_time = time.time()
            loop = asyncio.get_running_loop()

            def read_pty():
                try:
                    return os.read(
                        master_fd, 1024).decode(
                        'utf-8', errors='replace')
                except OSError:
                    return ""

            while True:
                chunk = await loop.run_in_executor(None, read_pty)
                if not chunk and current_process.poll() is not None:
                    break
                if chunk:
                    output_buffer += chunk
                    current_time = time.time()

                    has_prompt = re.search(
                        r'\[y/n\]', output_buffer[-100:], re.IGNORECASE)

                    if current_time - last_update_time >= 3.0 or has_prompt:
                        last_update_time = current_time
                        display_text = output_buffer
                        if len(display_text) > 4000:
                            display_text = display_text[-4000:]

                        reply_markup = None
                        if has_prompt:
                            keyboard = [
                                [
                                    InlineKeyboardButton(
                                        "Yes", callback_data='reply_y'), InlineKeyboardButton(
                                        "No", callback_data='reply_n')]]
                            reply_markup = InlineKeyboardMarkup(keyboard)

                        try:
                            await message.edit_text(f"⏳ Process Running (PID: {current_process.pid})\n\n```\n{display_text}\n```", parse_mode='Markdown', reply_markup=reply_markup)
                        except Exception as e:
                            logger.error(f"Failed to edit live message: {e}")

            os.close(master_fd)
            display_text = output_buffer
            if len(display_text) > 4000:
                display_text = display_text[-4000:]
            if not display_text.strip():
                display_text = "Process finished with no output."

            status = "✅ Process Finished" if current_process.returncode == 0 else "❌ Process Failed/Killed"
            try:
                await message.edit_text(f"{status} (PID: {current_process.pid})\n\n```\n{display_text}\n```", parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Failed to edit final message: {e}")

            current_process = None
            current_master_fd = None

        asyncio.create_task(read_output())

    except Exception as e:
        current_process = None
        current_master_fd = None
        await update.message.reply_text(f"Error executing background command: {str(e)}")


async def kill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /kill command to terminate the running process."""
    if not is_admin(update):
        return
    global current_process
    if current_process is None or current_process.poll() is not None:
        await update.message.reply_text("No active process to kill.")
        current_process = None
        return
    try:
        import signal
        os.killpg(os.getpgid(current_process.pid), signal.SIGTERM)
        # Give it a tiny bit to actually die, the read loop will handle
        # nullifying current_process
        await update.message.reply_text(f"The running process (PID: {current_process.pid}) has been sent the kill signal.")
    except Exception as e:
        await update.message.reply_text(f"Error terminating process: {str(e)}")


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /restart command to restart the bot."""
    if not is_admin(update):
        return
    await update.message.reply_text("Restarting bot...")
    try:
        os.execv(sys.executable, ['python3'] + sys.argv)
    except Exception as e:
        await update.message.reply_text(f"Error restarting bot: {str(e)}")


async def update_bot_command(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE):
    """Handle /update command to pull latest code from Git and restart."""
    if not is_admin(update):
        return
    status_msg = await update.message.reply_text("🔄 Force-pulling latest updates from GitHub...", parse_mode='Markdown')
    try:
        # Fetch the latest updates
        subprocess.run(["git", "fetch", "--all"],
                       capture_output=True, text=True, timeout=15)
        # Force hard reset to overwrite any local file changes
        result = subprocess.run(
            ["git", "reset", "--hard", "@{u}"], capture_output=True, text=True, timeout=15)
        output = result.stdout + result.stderr

        if "is up to date" in output or "Already up to date." in output:
            await status_msg.edit_text("✅ Bot is already up to date.")
        else:
            if len(output) > 3000:
                output = output[:3000] + "\n[Truncated]"
            await status_msg.edit_text(f"✅ Updates Force-Pulled:\n```\n{output}\n```\nRestarting bot to apply changes...", parse_mode='Markdown')
            # Wait a tiny bit so the message sends, then restart
            await asyncio.sleep(1)
            os.execv(sys.executable, ['python3'] + sys.argv)
    except subprocess.TimeoutExpired:
        await status_msg.edit_text("❌ `git pull` timed out.", parse_mode='Markdown')
    except Exception as e:
        await status_msg.edit_text(f"❌ Error updating bot: {str(e)}")


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logs command to send the last 50 lines of the bot.log file."""
    if not is_admin(update):
        return
    try:
        with open("bot.log", "r") as file:
            lines = file.readlines()
            last_lines = lines[-50:]
            logs_output = "".join(last_lines)
            if not logs_output:
                logs_output = "No logs available."
            elif len(logs_output) > 4000:
                logs_output = logs_output[-4000:]
            await update.message.reply_text(f"Last 50 lines of bot.log:\n```\n{logs_output}\n```", parse_mode='Markdown')
    except FileNotFoundError:
        await update.message.reply_text("Log file not found.")
    except Exception as e:
        await update.message.reply_text(f"Error reading logs: {str(e)}")


async def alias_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new shortcut alias."""
    if not is_admin(update):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/alias <name> <command>`\nExample: `/alias up apt-get update`", parse_mode='Markdown')
        return
    alias_name = args[0]
    alias_cmd = " ".join(args[1:])
    aliases[alias_name] = alias_cmd
    await update.message.reply_text(f"✅ Alias saved: `{alias_name}` -> `{alias_cmd}`", parse_mode='Markdown')


async def aliases_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all aliases."""
    if not is_admin(update):
        return
    if not aliases:
        await update.message.reply_text("No aliases saved.")
        return
    msg = "📝 **Saved Aliases:**\n\n"
    for name, cmd in aliases.items():
        msg += f"`{name}` -> `{cmd}`\n"
    await update.message.reply_text(msg, parse_mode='Markdown')


async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new admin ID."""
    if not is_admin(update):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: `/addadmin <user_id>` (Must be numeric)", parse_mode='Markdown')
        return
    new_admin = int(args[0])
    if new_admin == ADMIN_ID or new_admin in extra_admins:
        await update.message.reply_text("User is already an admin.")
        return
    try:
        if admins_collection is not None:
            admins_collection.insert_one({"user_id": new_admin})
        else:
            await update.message.reply_text("⚠️ Warning: MongoDB is not connected. Admin added to memory only (will be lost on restart).")

        extra_admins.add(new_admin)
        await update.message.reply_text(f"✅ User ID `{new_admin}` has been granted admin access.", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error adding admin to database: {e}")


async def deladmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove an admin ID."""
    if not is_admin(update):
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: `/deladmin <user_id>` (Must be numeric)", parse_mode='Markdown')
        return
    del_admin = int(args[0])
    if del_admin == ADMIN_ID:
        await update.message.reply_text("❌ You cannot delete the primary owner (`ADMIN_ID` from `.env`).", parse_mode='Markdown')
        return
    if del_admin not in extra_admins:
        await update.message.reply_text("User is not currently an extra admin.")
        return
    try:
        if admins_collection is not None:
            admins_collection.delete_one({"user_id": del_admin})
        extra_admins.remove(del_admin)
        await update.message.reply_text(f"🔴 User ID `{del_admin}`'s admin access has been revoked.", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error removing admin from database: {e}")


async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Upload a file to the server's current directory."""
    if not is_admin(update):
        return

    message = update.message
    if not message.reply_to_message:
        await message.reply_text("Please reply to a file (document, photo, etc.) with `/upload` to save it.", parse_mode='Markdown')
        return

    reply_msg = message.reply_to_message
    file_id = None
    file_name = "uploaded_file"

    # Check all possible attachments
    if reply_msg.document:
        file_id = reply_msg.document.file_id
        file_name = reply_msg.document.file_name or "uploaded_file.bin"
    elif reply_msg.audio:
        file_id = reply_msg.audio.file_id
        file_name = reply_msg.audio.file_name or f"uploaded_audio_{int(time.time())}.mp3"
    elif reply_msg.video:
        file_id = reply_msg.video.file_id
        file_name = reply_msg.video.file_name or f"uploaded_video_{int(time.time())}.mp4"
    elif reply_msg.animation:
        file_id = reply_msg.animation.file_id
        file_name = reply_msg.animation.file_name or f"uploaded_animation_{int(time.time())}.mp4"
    elif reply_msg.voice:
        file_id = reply_msg.voice.file_id
        file_name = f"uploaded_voice_{int(time.time())}.ogg"
    elif reply_msg.photo:
        file_id = reply_msg.photo[-1].file_id
        file_name = f"uploaded_photo_{int(time.time())}.jpg"
    else:
        await message.reply_text("Unsupported file type. Please reply to a document (.zip, .txt, etc), photo, video, or audio file.")
        return

    # Check if the user provided a custom name in the command
    args = context.args
    if args:
        # Use custom name provided by user
        file_name = " ".join(args)
    else:
        # User just typed /upload. If the original filename ends with .zip,
        # strip it.
        if file_name.lower().endswith(".zip"):
            file_name = file_name[:-4]

    status_msg = await message.reply_text(f"Downloading `{file_name}` from Telegram...", parse_mode='Markdown')
    try:
        new_file = await context.bot.get_file(file_id)
        save_path = os.path.join(current_dir, file_name)
        await new_file.download_to_drive(save_path)
        await status_msg.edit_text(f"✅ File successfully saved to:\n`{save_path}`", parse_mode='Markdown')
    except Exception as e:
        await status_msg.edit_text(f"❌ Error uploading file: {str(e)}")


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download a file from the server."""
    if not is_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/download <file_name>`", parse_mode='Markdown')
        return
    file_name = " ".join(args)
    file_path = os.path.join(current_dir, file_name)
    if not os.path.exists(file_path):
        await update.message.reply_text(f"❌ Target not found: `{file_path}`", parse_mode='Markdown')
        return

    # If the user targets a directory, zip it automatically
    is_dir = os.path.isdir(file_path)
    send_path = file_path

    if is_dir:
        status_msg = await update.message.reply_text(f"🗜️ Target is a directory. Zipping `{file_name}`...", parse_mode='Markdown')
        try:
            zip_path = file_path + ".zip"
            shutil.make_archive(file_path, 'zip', file_path)
            send_path = zip_path
            file_name += ".zip"
        except Exception as e:
            await status_msg.edit_text(f"❌ Error zipping directory: {str(e)}")
            return
    else:
        status_msg = await update.message.reply_text(f"Uploading `{file_name}` to Telegram...", parse_mode='Markdown')

    try:
        with open(send_path, "rb") as f:
            # Tell telegram to treat it as a document (works for zip, txt, bin,
            # etc)
            await context.bot.send_document(chat_id=update.effective_chat.id, document=f, filename=file_name)
        await status_msg.edit_text(f"✅ Successfully sent: `{file_name}`", parse_mode='Markdown')

        # Cleanup automatically created zip file
        if is_dir and os.path.exists(send_path):
            os.remove(send_path)

    except Exception as e:
        await status_msg.edit_text(f"❌ Error sending file: {str(e)}")


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a file or directory from the server."""
    if not is_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/delete <file_or_dir_name>`", parse_mode='Markdown')
        return
    target_name = " ".join(args)
    target_path = os.path.join(current_dir, target_name)
    if not os.path.exists(target_path):
        await update.message.reply_text(f"❌ Target not found: `{target_path}`", parse_mode='Markdown')
        return
    try:
        if os.path.isfile(target_path):
            os.remove(target_path)
            await update.message.reply_text(f"🗑️ Deleted file: `{target_path}`", parse_mode='Markdown')
        elif os.path.isdir(target_path):
            shutil.rmtree(target_path)
            await update.message.reply_text(f"🗑️ Deleted directory recursively: `{target_path}`", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error deleting `{target_path}`: {str(e)}", parse_mode='Markdown')


async def zip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zip a file or folder on the server."""
    if not is_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/zip <file_or_folder>`", parse_mode='Markdown')
        return
    target_name = " ".join(args)
    target_path = os.path.join(current_dir, target_name)
    if not os.path.exists(target_path):
        await update.message.reply_text(f"❌ Target not found: `{target_path}`", parse_mode='Markdown')
        return

    status_msg = await update.message.reply_text(f"🗜️ Zipping `{target_name}`...", parse_mode='Markdown')
    try:
        if os.path.isdir(target_path):
            shutil.make_archive(target_path, 'zip', target_path)
            await status_msg.edit_text(f"✅ Directory Zipped: `{target_name}.zip`", parse_mode='Markdown')
        else:
            with zipfile.ZipFile(f"{target_path}.zip", 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(target_path, os.path.basename(target_path))
            await status_msg.edit_text(f"✅ File Zipped: `{target_name}.zip`", parse_mode='Markdown')
    except Exception as e:
        await status_msg.edit_text(f"❌ Error zipping: {str(e)}")


async def unzip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unzip a file on the server."""
    if not is_admin(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/unzip <file.zip>`", parse_mode='Markdown')
        return
    target_name = " ".join(args)
    target_path = os.path.join(current_dir, target_name)
    if not os.path.exists(target_path):
        await update.message.reply_text(f"❌ Zip file not found: `{target_path}`", parse_mode='Markdown')
        return
    if not target_name.endswith('.zip'):
        await update.message.reply_text("❌ Target does not appear to be a `.zip` file.", parse_mode='Markdown')
        return

    extract_folder = target_path[:-4]  # Remove .zip
    status_msg = await update.message.reply_text(f"📂 Extracting `{target_name}`...", parse_mode='Markdown')
    try:
        shutil.unpack_archive(target_path, extract_folder)
        await status_msg.edit_text(f"✅ Unzipped successfully into:\n`{extract_folder}`", parse_mode='Markdown')
    except Exception as e:
        await status_msg.edit_text(f"❌ Error extracting zip: {str(e)}")


async def txt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a text file with the specified content on the server."""
    if not is_admin(update):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/txt <filename> <content>`\nExample: `/txt script.py print('Hello')`", parse_mode='Markdown')
        return

    file_name = args[0]
    # The rest of the message after the filename is the content
    # To preserve formatting, we can extract from the original message text
    # command structure: /txt filename content...
    command_text = update.message.text
    # Split by the first two spaces (or use maxsplit=2)
    # /txt filename content goes here...
    parts = command_text.split(None, 2)
    if len(parts) < 3:
        await update.message.reply_text("Please provide content for the file.")
        return

    content = parts[2]
    file_path = os.path.join(current_dir, file_name)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        await update.message.reply_text(f"✅ File successfully saved to server:\n`{file_path}`", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error creating file: {str(e)}")


async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all authorized admin IDs."""
    if not is_admin(update):
        return
    msg = f"👑 **Primary Admin (Owner):**\n`{ADMIN_ID}`\n\n"
    msg += "👥 **Extra Admins:**\n"
    if extra_admins:
        for admin in extra_admins:
            msg += f"`{admin}`\n"
    else:
        msg += "None"
    await update.message.reply_text(msg, parse_mode='Markdown')


async def interactive_command(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE):
    """Toggle interactive shell mode on."""
    if not is_admin(update):
        return
    global interactive_mode
    interactive_mode = True
    await update.message.reply_text("🟢 **Interactive Mode Enabled**\nYou no longer need to use `/run`. Every message you send will be executed as a shell command.\nType `/exit` to turn this off.", parse_mode='Markdown')


async def exit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle interactive shell mode off."""
    if not is_admin(update):
        return
    global interactive_mode
    interactive_mode = False
    await update.message.reply_text("🔴 **Interactive Mode Disabled**\nYou must now use `/run <cmd>` to execute commands.", parse_mode='Markdown')


async def execute_shell_command(update: Update, command: str):
    """Helper function to execute a shell command with live updates and a 60s timeout."""
    global current_process, current_master_fd

    # Prevent running multiple commands at once to avoid confusion
    if current_process is not None and current_process.poll() is None:
        await update.message.reply_text("A process is already running. Please wait or use `/kill`.", parse_mode='Markdown')
        return

    # Check for alias
    first_word = command.split()[0] if command else ""
    if first_word in aliases:
        command = command.replace(first_word, aliases[first_word], 1)
        await update.message.reply_text(f"*(Alias expanded: `{command}`)*", parse_mode='Markdown')

    try:
        master_fd, slave_fd = pty.openpty()
        current_master_fd = master_fd
        current_process = subprocess.Popen(
            command,
            shell=True,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=current_dir,
            preexec_fn=os.setsid
        )
        os.close(slave_fd)

        message = await update.message.reply_text(f"⏳ Executing: `{command}`\nLoading...", parse_mode='Markdown')

        output_buffer = ""
        last_update_time = time.time()
        start_time = time.time()
        loop = asyncio.get_running_loop()

        def read_pty():
            try:
                return os.read(
                    master_fd,
                    1024).decode(
                    'utf-8',
                    errors='replace')
            except OSError:
                return ""

        while True:
            # Check for timeout (60 seconds)
            if time.time() - start_time > 60:
                import signal
                os.killpg(os.getpgid(current_process.pid), signal.SIGTERM)
                os.close(master_fd)
                display_text = output_buffer
                if len(display_text) > 4000:
                    display_text = display_text[-4000:]
                try:
                    await message.edit_text(f"❌ **Timeout (60s)**. Process killed.\nUse `/bg` for longer tasks.\n\n```\n{display_text}\n```", parse_mode='Markdown')
                except Exception:
                    pass
                current_process = None
                current_master_fd = None
                return

            chunk = await loop.run_in_executor(None, read_pty)

            if not chunk and current_process.poll() is not None:
                break

            if chunk:
                output_buffer += chunk
                current_time = time.time()

                has_prompt = re.search(
                    r'\[y/n\]', output_buffer[-100:], re.IGNORECASE)

                # Update every 3 seconds or if there's a prompt
                if current_time - last_update_time >= 3.0 or has_prompt:
                    last_update_time = current_time
                    display_text = output_buffer
                    if len(display_text) > 4000:
                        display_text = display_text[-4000:]

                    reply_markup = None
                    if has_prompt:
                        keyboard = [
                            [
                                InlineKeyboardButton(
                                    "Yes", callback_data='reply_y'), InlineKeyboardButton(
                                    "No", callback_data='reply_n')]]
                        reply_markup = InlineKeyboardMarkup(keyboard)

                    try:
                        await message.edit_text(f"⏳ Executing (PID: {current_process.pid})\n\n```\n{display_text}\n```", parse_mode='Markdown', reply_markup=reply_markup)
                    except Exception as e:
                        logger.error(f"Failed to edit live message: {e}")

        os.close(master_fd)

        # Final update
        display_text = output_buffer
        if len(display_text) > 4000:
            display_text = display_text[-4000:]
        if not display_text.strip():
            display_text = "Command executed (no output)."

        status = "✅ Finished" if current_process.returncode == 0 else "❌ Failed"
        try:
            await message.edit_text(f"{status}\n\n```\n{display_text}\n```", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to edit final message: {e}")

        current_process = None
        current_master_fd = None

    except Exception as e:
        current_process = None
        current_master_fd = None
        await update.message.reply_text(f"Error executing command: {str(e)}")


def parse_time(time_str: str) -> int:
    """Helper to convert time string (e.g., 5m, 1h, 30s) to seconds."""
    time_str = time_str.lower()
    try:
        if time_str.endswith('s'):
            return int(time_str[:-1])
        elif time_str.endswith('m'):
            return int(time_str[:-1]) * 60
        elif time_str.endswith('h'):
            return int(time_str[:-1]) * 3600
        elif time_str.endswith('d'):
            return int(time_str[:-1]) * 86400
        else:
            return int(time_str)
    except ValueError:
        return -1


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Schedule a command to run periodically."""
    if not is_admin(update):
        return
    global task_counter
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/schedule <time> <command>`\nExample: `/schedule 5m apt-get update`", parse_mode='Markdown')
        return

    time_interval = parse_time(args[0])
    if time_interval <= 0:
        await update.message.reply_text("❌ Invalid time format. Use something like `30s`, `5m`, or `1h`.", parse_mode='Markdown')
        return

    command = " ".join(args[1:])
    task_id = task_counter
    task_counter += 1

    async def scheduled_job():
        # Let's wait first or execute immediately? Let's execute immediately
        # then wait.
        while task_id in scheduled_tasks:
            # We will use the execute_shell_command function, but we need to pass a context
            # We just send a message to the user independently.
            try:
                # Check for alias
                expanded_command = command
                first_word = command.split()[0] if command else ""
                if first_word in aliases:
                    expanded_command = command.replace(
                        first_word, aliases[first_word], 1)

                process = subprocess.Popen(
                    expanded_command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=current_dir,
                    preexec_fn=os.setsid
                )
                stdout, stderr = process.communicate(timeout=60)
                output = stdout + stderr
                if not output:
                    output = "(No output)"
                if len(output) > 4000:
                    output = output[:4000] + "\n[Output truncated...]"

                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"⏰ **Scheduled Task #{task_id} Executed** (`{command}`):\n\n```\n{output}\n```",
                    parse_mode='Markdown'
                )
            except Exception as e:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ **Error in Scheduled Task #{task_id}**: {str(e)}",
                    parse_mode='Markdown'
                )

            await asyncio.sleep(time_interval)

    # Start the background asyncio task
    task_loop = asyncio.create_task(scheduled_job())
    scheduled_tasks[task_id] = {
        "command": command,
        "interval": time_interval,
        "task_obj": task_loop
    }

    await update.message.reply_text(f"✅ **Task Scheduled** (ID: `{task_id}`)\nCommand `{command}` will run every `{args[0]}`.", parse_mode='Markdown')


async def unschedule_command(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE):
    """Stop a scheduled task."""
    if not is_admin(update):
        return
    args = context.args
    if not args:
        if not scheduled_tasks:
            await update.message.reply_text("No active scheduled tasks.")
            return
        msg = "⏱️ **Active Scheduled Tasks:**\n\n"
        for t_id, t_info in scheduled_tasks.items():
            msg += f"**ID {t_id}**: `{t_info['command']}` (Interval: {t_info['interval']}s)\n"
        msg += "\nTo stop a task, use `/unschedule <id>`"
        await update.message.reply_text(msg, parse_mode='Markdown')
        return

    try:
        task_id = int(args[0])
        if task_id in scheduled_tasks:
            task_info = scheduled_tasks.pop(task_id)
            task_info["task_obj"].cancel()
            await update.message.reply_text(f"🛑 Cancelled Scheduled Task #{task_id} (`{task_info['command']}`)", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ Task ID `{task_id}` not found.")
    except ValueError:
        await update.message.reply_text("Please provide a valid numeric Task ID.")


async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /r command to send input to the running process."""
    if not is_admin(update):
        return

    if current_master_fd is None:
        await update.message.reply_text("No active interactive process to reply to.")
        return

    # Extract the input text after /r
    input_text = " ".join(context.args).strip()
    if not input_text:
        await update.message.reply_text("Please provide input. Usage: `/r <text>`", parse_mode='Markdown')
        return

    try:
        os.write(current_master_fd, (input_text + "\n").encode('utf-8'))
        await update.message.reply_text(f"✅ Sent input: `{input_text}`", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error sending input: {str(e)}")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses for interactive prompts."""
    query = update.callback_query
    # We use a simple check; update.effective_user.id provides the user's ID
    user_id = query.from_user.id
    if user_id != ADMIN_ID and user_id not in extra_admins:
        await query.answer("You are not authorized.", show_alert=True)
        return

    await query.answer()

    if current_master_fd is None:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    data = query.data
    input_text = ""
    if data == 'reply_y':
        input_text = "y"
    elif data == 'reply_n':
        input_text = "n"

    if input_text:
        try:
            os.write(current_master_fd, (input_text + "\n").encode('utf-8'))
            # Remove the buttons after selection
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.error(f"Error sending button input: {e}")


async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /run command to execute shell commands."""
    if not is_admin(update):
        return
    # Extract the command after /run
    command = " ".join(context.args).strip()
    if not command:
        await update.message.reply_text("Please provide a command to run. Usage: /run <command>")
        return
    await execute_shell_command(update, command)


def main():
    """Start the bot."""
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN must be set in .env file.")
        return

    # Setup bot commands menu
    async def post_init(app: Application):
        from telegram import BotCommand
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("cd", "Change directory (e.g., /cd <path>)"),
            BotCommand("home", "Go to home directory"),
            BotCommand("run", "Run a shell command (e.g., /run ls)"),
            BotCommand("r", "Reply to an interactive prompt (e.g., /r y)"),
            BotCommand("bg", "Run a command in background (e.g., /bg top)"),
            BotCommand("kill", "Kill the currently running process"),
            BotCommand("stats", "Show system CPU, RAM, and Disk usage"),
            BotCommand("logs", "Show last 50 lines of bot logs"),
            BotCommand("restart", "Restart the bot script"),
            BotCommand("interactive", "Toggle interactive shell mode"),
            BotCommand("exit", "Disable interactive mode"),
            BotCommand("alias", "Add a shortcut (e.g., /alias up apt update)"),
            BotCommand("aliases", "List all shortcuts"),
            BotCommand("addadmin", "Add extra admin ID"),
            BotCommand("deladmin", "Remove extra admin ID"),
            BotCommand("admins", "List all admins"),
            BotCommand("upload", "Upload a file (reply to a file)"),
            BotCommand("download", "Download a file to Telegram"),
            BotCommand("delete", "Delete a file or folder"),
            BotCommand("zip", "Zip a file or folder"),
            BotCommand("unzip", "Unzip a .zip file"),
            BotCommand("txt", "Create a new text file"),
            BotCommand("sysinfo", "Show OS, IP, and Uptime"),
            BotCommand("speedtest", "Test server internet speed"),
            BotCommand("ping", "Ping a host"),
            BotCommand("schedule", "Schedule a repeating task"),
            BotCommand("unschedule", "Stop a scheduled task"),
            BotCommand("update", "Git pull and restart bot"),
        ]
        await app.bot.set_my_commands(commands)

    # Create the Application
    application = Application.builder().token(
        BOT_TOKEN).post_init(post_init).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cd", cd_command))
    application.add_handler(CommandHandler("home", home_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("sysinfo", sysinfo_command))
    application.add_handler(CommandHandler("speedtest", speedtest_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("schedule", schedule_command))
    application.add_handler(CommandHandler("unschedule", unschedule_command))
    application.add_handler(CommandHandler("upload", upload_command))
    application.add_handler(CommandHandler("download", download_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("zip", zip_command))
    application.add_handler(CommandHandler("unzip", unzip_command))
    application.add_handler(CommandHandler("txt", txt_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("run", run_command))
    application.add_handler(CommandHandler("r", reply_command))
    application.add_handler(CommandHandler("bg", bg_command))
    application.add_handler(CommandHandler("kill", kill_command))
    application.add_handler(CommandHandler("restart", restart_command))
    application.add_handler(CommandHandler("update", update_bot_command))
    application.add_handler(CommandHandler("interactive", interactive_command))
    application.add_handler(CommandHandler("exit", exit_command))
    application.add_handler(CommandHandler("alias", alias_command))
    application.add_handler(CommandHandler("aliases", aliases_command))
    application.add_handler(
        CallbackQueryHandler(
            button_callback,
            pattern='^reply_'))

    # Message handler for interactive mode or fallback warning
    async def unknown_command(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE):
        if is_admin(update):
            if interactive_mode:
                command = update.message.text.strip()
                await execute_shell_command(update, command)
            else:
                await update.message.reply_text("Please use `/run <command>` to execute shell commands, or type `/interactive` to enable interactive mode.", parse_mode='Markdown')

    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, unknown_command))

    # Start the bot
    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
