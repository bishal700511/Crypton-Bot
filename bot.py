"""
CRYPTON Telegram Bot
────────────────────
Library : pyTelegramBotAPI
Database: Firestore REST API  (no firebase-admin)
Deploy  : Render.com (polling mode)

Required environment variables:
  BOT_TOKEN        – Telegram bot token
  FIREBASE_API_KEY – Firebase Web API key

Fixes applied:
  1. All app-launch buttons now use WebAppInfo so the Mini App opens
     natively inside Telegram instead of an external browser pop-up.
  2. Registration is two-phase:
       • Phase 1 (in-memory only): collect name → email → wait for link.
       • Phase 2 (Firestore write): only on successful "Link" button press.
     A user is considered "fully registered" only when their Firestore
     document exists AND contains the 'email' field (sentinel for
     completion).  Incomplete / missing documents restart registration.
"""

import os
import logging
import requests
import telebot
from telebot import types

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

# ── In-memory registration state ─────────────────────────────────────────────
# { chat_id: { "step": "name"|"email"|"link", "name": str, "email": str } }
# Nothing is written to Firestore until the user clicks the Link button.
user_state: dict[int, dict] = {}

# ── Firestore helpers ─────────────────────────────────────────────────────────

def _fs_url(collection: str, doc_id: str) -> str:
    return f"{FIRESTORE_BASE}/{collection}/{doc_id}?key={FIREBASE_API_KEY}"


def is_fully_registered(chat_id: int) -> bool:
    """
    Return True only when the Firestore document exists AND contains the
    'email' field — the sentinel that marks a completed registration.

    A document that is missing or lacks 'email' (e.g. left over from a
    previous broken flow) is treated as NOT registered.
    """
    url = _fs_url("users", str(chat_id))
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            return False
        if resp.status_code == 200:
            fields = resp.json().get("fields", {})
            # Must have the email sentinel to count as fully registered
            return "email" in fields
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
    Called ONLY after the user clicks the Link button — never during
    the intermediate registration steps.
    """
    url = _fs_url("users", str(chat_id))
    payload = {
        "fields": {
            "tgName":     {"stringValue": name},
            "email":      {"stringValue": email},
            "telegramId": {"integerValue": chat_id},
            "username":   {"stringValue": username or ""},
            "earnedTon":  {"doubleValue": 0},
            "ownedSlots": {"arrayValue": {"values": []}},
            "joinedAt":   {"timestampValue": _server_timestamp()},
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
    """The 'Link ✧' button triggers the final Firestore write via callback."""
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "╭☞ Link ✧",
            callback_data=f"link_{chat_id}",
        )
    )
    return kb


def account_created_markup() -> types.InlineKeyboardMarkup:
    """
    Post-registration launch button — also a True Mini App button.
    """
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "🔐 Open CRYPTON App ❖",
            web_app=types.WebAppInfo(url=APP_URL),
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
        # Not in a registration flow — prompt /start
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
        # Store in memory only — no Firestore write yet.
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
        # Store in memory only — no Firestore write yet.
        state["email"] = email
        state["step"]  = "link"
        bot.send_message(
            chat_id,
            "Now link your Telegram to 🤝 CRYPTON",
            reply_markup=link_markup(chat_id),
        )

    elif step == "link":
        # Waiting for the Link button — nothing to save yet; remind the user.
        bot.send_message(
            chat_id,
            "Please tap the '╭☞ Link ✧' button above to continue.",
        )

    else:
        bot.send_message(chat_id, "Please type /start to begin.")


# ── Callback query handler ────────────────────────────────────────────────────

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

    name     = state.get("name", "")
    email    = state.get("email", "")
    username = call.from_user.username  # may be None

    bot.answer_callback_query(call.id, "Linking your Telegram…")

    # ── Phase 2: Write the complete, final document to Firestore ─────────────
    # This is the ONLY place we touch Firestore during registration.
    success = create_user(chat_id, name, email, username)

    if not success:
        bot.send_message(
            chat_id,
            "⚠️ Something went wrong while saving your data. "
            "Please try /start again.",
        )
        user_state.pop(chat_id, None)
        return

    # Clear in-memory state — user is now fully registered in Firestore.
    user_state.pop(chat_id, None)

    # ── Confirmation message ──────────────────────────────────────────────────
    bot.send_message(
        chat_id,
        "✅ Your Telegram has been successfully linked to CRYPTON",
        reply_markup=types.InlineKeyboardMarkup(rows=[
            [types.InlineKeyboardButton("Account Created ◌", callback_data="noop")]
        ]),
    )

    # ── Welcome / launch message with True Mini App button ───────────────────
    bot.send_message(
        chat_id,
        f"Welcome to CRYPTON Official, {name} ᴥ √\n"
        "Tap below to launch your app and start earning",
        reply_markup=account_created_markup(),
    )


@bot.callback_query_handler(func=lambda call: call.data == "noop")
def handle_noop(call: types.CallbackQuery) -> None:
    bot.answer_callback_query(call.id)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("CRYPTON bot starting (polling)…")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
