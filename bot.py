"""
CRYPTON Telegram Bot
────────────────────
Library : pyTelegramBotAPI
Database: Firestore REST API  (no firebase-admin)
Deploy  : Render.com (polling mode)

Required environment variables:
  BOT_TOKEN        – Telegram bot token
  FIREBASE_API_KEY – Firebase Web API key

Data model — ONE document per user, Document ID = str(chat_id):
  tgName        – full name entered during bot registration
  tgEmail       – email entered during bot registration  (registration sentinel)
  tgHandle      – Telegram @username (e.g. "@alice")
  tgId          – Telegram chat ID as a STRING  (matches JS String(user.id))
  walletAddress – blank "" on creation; app writes the TON wallet address later
  earnedTon     – float, starts at 0
  ownedSlots    – array, starts empty
  joinedAt      – UTC RFC 3339 timestamp

Registration flow (4-phase):
  Phase 1 (in-memory only) : collect name → email → show Link button.
  Phase 2 (Link button)    : verify session, update message to
                             "Verification Successfully Your Telegram To Link App"
                             and swap button to "Account Created ◌"
                             (callback: final_create_{chat_id}).
                             NO Firestore write yet.
  Phase 3 (Account Created): call create_user() — the ONLY Firestore write
                             in the entire registration flow.
  Phase 4 (welcome)        : clear user_state, send final launch message
                             with True Mini App button.

A user is considered "fully registered" only when their Firestore document
exists AND contains the 'tgEmail' field (sentinel for completion). Incomplete
or missing documents restart registration.
"""

import os
import logging
import threading
import requests
import telebot
from telebot import types
from flask import Flask

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Environment variables ─────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
FIREBASE_API_KEY = os.environ["FIREBASE_API_KEY"]

PROJECT_ID     = "crypton-app-871b1"
FIRESTORE_BASE = (
    f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}"
    "/databases/(default)/documents"
)
APP_URL = "https://bishal700511.github.io/Crypton-App/"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# ── Dummy web server (keeps Render Web Service alive) ────────────────────────
# Render requires a process to bind to a port; polling bots don't do that by
# default and get killed.  This minimal Flask app runs on a daemon thread so
# it never blocks the bot, and responds with 200 OK to Render's health checks.

_flask_app = Flask(__name__)

@_flask_app.get("/")
def _health() -> tuple[str, int]:
    return "CRYPTON bot is running.", 200

def _run_web_server() -> None:
    port = int(os.environ.get("PORT", 8080))
    _flask_app.run(host="0.0.0.0", port=port)

# ── In-memory registration state ─────────────────────────────────────────────
# {
#   chat_id: {
#     "step"    : "name" | "email" | "link" | "final",
#     "name"    : str,
#     "email"   : str,
#     "username": str | None,   # stored at Phase 2, used at Phase 3
#   }
# }
# Nothing is written to Firestore until the user clicks "Account Created ◌".
user_state: dict[int, dict] = {}

# ── Firestore helpers ─────────────────────────────────────────────────────────

def _fs_url(collection: str, doc_id: str) -> str:
    return f"{FIRESTORE_BASE}/{collection}/{doc_id}?key={FIREBASE_API_KEY}"


def is_fully_registered(chat_id: int) -> bool:
    """
    Return True only when the Firestore document exists AND contains the
    'tgEmail' field — the sentinel that marks a completed registration.

    A document that is missing or lacks 'tgEmail' (e.g. left over from a
    previous broken flow) is treated as NOT registered.
    """
    url = _fs_url("users", str(chat_id))
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            return False
        if resp.status_code == 200:
            fields = resp.json().get("fields", {})
            # Sentinel: 'tgEmail' must exist — matches the field name the app reads
            return "tgEmail" in fields
        logger.error(
            "Firestore GET unexpected status %s: %s", resp.status_code, resp.text
        )
        return False
    except requests.RequestException as exc:
        logger.error("Firestore GET error: %s", exc)
        return False


def get_user(chat_id: int) -> dict | None:
    """Fetch the user document fields as a plain dict, or None on failure."""
    url = _fs_url("users", str(chat_id))
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            fields = resp.json().get("fields", {})
            return {k: list(v.values())[0] for k, v in fields.items()}
        return None
    except requests.RequestException as exc:
        logger.error("Firestore GET user error: %s", exc)
        return None


def create_user(chat_id: int, name: str, email: str, username: str | None) -> bool:
    """
    Write the complete, final user document to Firestore via REST PATCH.
    Document ID  : str(chat_id)  — the single permanent user identity.
    Field names  : aligned exactly with what index.html reads/writes.
      tgName        – full name entered by the user
      tgEmail       – email entered by the user  (sentinel: app checks for this)
      tgHandle      – Telegram @username handle
      tgId          – Telegram chat ID saved as a STRING (matches JS String())
      walletAddress – blank on creation; the app writes the real address later
      earnedTon     – initialised to 0
      ownedSlots    – initialised to empty array
      joinedAt      – server-side UTC timestamp
    Called ONLY when the user clicks 'Account Created ◌' (Phase 3).
    """
    url = _fs_url("users", str(chat_id))
    handle = f"@{username}" if username else ""
    payload = {
        "fields": {
            "tgName":        {"stringValue": name},
            "tgEmail":       {"stringValue": email},
            "tgHandle":      {"stringValue": handle},
            "tgId":          {"stringValue": str(chat_id)},
            "walletAddress": {"stringValue": ""},
            "earnedTon":     {"doubleValue": 0},
            "ownedSlots":    {"arrayValue": {"values": []}},
            "joinedAt":      {"timestampValue": _server_timestamp()},
        }
    }
    try:
        resp = requests.patch(url, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            return True
        logger.error(
            "Firestore PATCH error %s: %s", resp.status_code, resp.text
        )
        return False
    except requests.RequestException as exc:
        logger.error("Firestore PATCH exception: %s", exc)
        return False


def _server_timestamp() -> str:
    """Return current UTC time in RFC 3339 / Firestore timestampValue format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ── Keyboard helpers ──────────────────────────────────────────────────────────

def open_app_markup() -> types.InlineKeyboardMarkup:
    """
    Button that opens CRYPTON as a TRUE Telegram Mini App (no external
    browser pop-up, no URL preview window).
    """
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "🔐 Open CRYPTON App ❖",
            web_app=types.WebAppInfo(url=APP_URL),
        )
    )
    return kb


def link_markup(chat_id: int) -> types.InlineKeyboardMarkup:
    """Phase 1 → Phase 2: 'Link ✧' button, triggers handle_link_callback."""
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "╭☞ Link ✧",
            callback_data=f"link_{chat_id}",
        )
    )
    return kb


def account_created_markup(chat_id: int) -> types.InlineKeyboardMarkup:
    """
    Phase 2 → Phase 3: 'Account Created ◌' button shown after the Link
    button is pressed.  Triggers handle_final_create_callback which is the
    ONLY place Firestore is written.
    """
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "Account Created ◌",
            callback_data=f"final_create_{chat_id}",
        )
    )
    return kb


# ── /start handler ────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def handle_start(message: types.Message) -> None:
    chat_id = message.chat.id

    # Always wipe any stale in-memory state first so /start is a clean slate.
    user_state.pop(chat_id, None)

    if is_fully_registered(chat_id):
        # ── Returning, fully-registered user ─────────────────────────────────
        data = get_user(chat_id)
        name = data.get("tgName", "User") if data else "User"
        bot.send_message(
            chat_id,
            f"👋 Welcome back, {name} ᴥ √\n"
            "Your CRYPTON Account is Already Here — "
            "Tap The Button Below To Open The App",
            reply_markup=open_app_markup(),
        )
    else:
        # ── New user OR abandoned half-registration → restart from step 1 ────
        # Any incomplete Firestore document is intentionally left alone;
        # it will be fully overwritten by PATCH when registration completes.
        user_state[chat_id] = {"step": "name"}
        bot.send_message(
            chat_id,
            "👋 Welcome To The Official Community Of CRYPTON Decentralized ❂\n"
            "◆ Let's Set Up Your Account — Please Enter Your Full Name ᴥ",
        )


# ── Text message handler (registration flow) ──────────────────────────────────

@bot.message_handler(func=lambda m: m.content_type == "text" and not m.text.startswith("/"))
def handle_text(message: types.Message) -> None:
    chat_id = message.chat.id
    state   = user_state.get(chat_id)

    if state is None:
        bot.send_message(chat_id, "Please type /start to begin.")
        return

    step = state.get("step")

    # ── Step 1: Collect name ──────────────────────────────────────────────────
    if step == "name":
        name = message.text.strip()
        if not name:
            bot.send_message(
                chat_id, "Name cannot be empty. Please enter your full name."
            )
            return
        state["name"] = name
        state["step"] = "email"
        bot.send_message(
            chat_id,
            f"👍 Got it, {name} ᴥ\nNow please enter your Email Address 📨",
        )

    # ── Step 2: Collect email ─────────────────────────────────────────────────
    elif step == "email":
        email = message.text.strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            bot.send_message(
                chat_id,
                "That doesn't look like a valid email. Please try again.",
            )
            return
        state["email"] = email
        state["step"]  = "link"
        bot.send_message(
            chat_id,
            "Now link your Telegram to 🤝 CRYPTON",
            reply_markup=link_markup(chat_id),
        )

    elif step in ("link", "final"):
        # Waiting for an inline button press — remind the user.
        bot.send_message(
            chat_id,
            "Please tap the button above to continue.",
        )

    else:
        bot.send_message(chat_id, "Please type /start to begin.")


# ── Phase 2: Link button callback ─────────────────────────────────────────────
# Verifies session, stores username, advances step to "final", updates the
# message in-place with new text + "Account Created ◌" button.
# NO Firestore write happens here.

@bot.callback_query_handler(func=lambda call: call.data.startswith("link_"))
def handle_link_callback(call: types.CallbackQuery) -> None:
    chat_id = call.message.chat.id

    try:
        callback_chat_id = int(call.data.split("_", 1)[1])
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid request.")
        return

    # Security: only the owner of this registration may press the button.
    if chat_id != callback_chat_id:
        bot.answer_callback_query(call.id, "This button is not for you.")
        return

    state = user_state.get(chat_id)
    if state is None or state.get("step") != "link":
        bot.answer_callback_query(
            call.id, "Session expired. Please type /start again."
        )
        return

    # Capture username now (available from call.from_user) for use in Phase 3.
    state["username"] = call.from_user.username  # may be None
    state["step"]     = "final"

    bot.answer_callback_query(call.id, "Telegram linked ✓")

    # Edit the existing message in-place: new text + swap button.
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text="Verification Successfully Your Telegram To Link App",
        reply_markup=account_created_markup(chat_id),
    )


# ── Phase 3: Account Created button callback ──────────────────────────────────
# This is the ONLY place create_user() / Firestore is called.

@bot.callback_query_handler(func=lambda call: call.data.startswith("final_create_"))
def handle_final_create_callback(call: types.CallbackQuery) -> None:
    chat_id = call.message.chat.id

    try:
        callback_chat_id = int(call.data.split("_", 2)[2])
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid request.")
        return

    # Security: only the owner of this registration may press the button.
    if chat_id != callback_chat_id:
        bot.answer_callback_query(call.id, "This button is not for you.")
        return

    state = user_state.get(chat_id)
    if state is None or state.get("step") != "final":
        bot.answer_callback_query(
            call.id, "Session expired. Please type /start again."
        )
        return

    name     = state.get("name", "")
    email    = state.get("email", "")
    username = state.get("username")  # captured during Phase 2

    bot.answer_callback_query(call.id, "Creating your account…")

    # ── Phase 3: THE ONLY Firestore write in the registration flow ────────────
    success = create_user(chat_id, name, email, username)

    if not success:
        bot.send_message(
            chat_id,
            "⚠️ Something went wrong while saving your data. "
            "Please try /start again.",
        )
        user_state.pop(chat_id, None)
        return

    # ── Phase 4: Clear memory, send final welcome + launch button ─────────────
    user_state.pop(chat_id, None)

    bot.send_message(
        chat_id,
        f"Welcome to CRYPTON Official, {name} ᴥ √\n"
        "Tap below to launch your app and start earning",
        reply_markup=open_app_markup(),
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Start the dummy web server on a background daemon thread so Render's
    # port-binding check passes without interrupting the bot's polling loop.
    web_thread = threading.Thread(target=_run_web_server, daemon=True)
    web_thread.start()
    logger.info("CRYPTON bot starting (polling)…")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
