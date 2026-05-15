/**
 * ╔══════════════════════════════════════════════════════════╗
 * ║          CRYPTON — Telegram Registration Bot             ║
 * ║          Node.js  |  node-telegram-bot-api               ║
 * ╚══════════════════════════════════════════════════════════╝
 *
 * Setup:
 *   npm init -y
 *   npm install node-telegram-bot-api
 *   node index.js
 */

const TelegramBot = require('node-telegram-bot-api');

// ── Bot Token ────────────────────────────────────────────────
const BOT_TOKEN = '8553132955:AAEfyUzW3KYX3Ey7M9kM3QqP2eN4Ax2hwNI';

// ── App Base URL ─────────────────────────────────────────────
const APP_BASE_URL = 'https://bishal700511.github.io/Crypton-App/';

// ── Initialize Bot (polling mode) ───────────────────────────
const bot = new TelegramBot(BOT_TOKEN, { polling: true });

// ── In-memory session store  { chatId → sessionObject } ─────
// For production, replace with Redis / Firebase / any DB.
const sessions = {};

/**
 * Session states:
 *   'AWAIT_NAME'   — waiting for user's full name
 *   'AWAIT_EMAIL'  — waiting for email address
 *   'DONE'         — registration complete
 */

// ────────────────────────────────────────────────────────────
// /start  — entry point
// ────────────────────────────────────────────────────────────
bot.onText(/\/start/, (msg) => {
  const chatId = msg.chat.id;

  // Reset session on every /start
  sessions[chatId] = { state: 'AWAIT_NAME' };

  bot.sendMessage(
    chatId,
    '👋 Welcome to Official Community of CRYPTON...\n\nPlease enter your Full Name:'
  );
});

// ────────────────────────────────────────────────────────────
// General message handler — drives the registration flow
// ────────────────────────────────────────────────────────────
bot.on('message', async (msg) => {
  const chatId = msg.chat.id;
  const text   = (msg.text || '').trim();

  // Ignore commands (handled by onText above)
  if (text.startsWith('/')) return;

  // Ignore button callbacks / non-text updates
  if (!text) return;

  const session = sessions[chatId];

  // If no session exists, prompt user to start
  if (!session) {
    bot.sendMessage(chatId, '👋 Please send /start to begin registration.');
    return;
  }

  // ── STATE: Waiting for Full Name ─────────────────────────
  if (session.state === 'AWAIT_NAME') {
    session.name  = text;
    session.state = 'AWAIT_EMAIL';

    bot.sendMessage(
      chatId,
      `👍 Got it, ${session.name}! Now please enter your Email Address:`
    );
    return;
  }

  // ── STATE: Waiting for Email ─────────────────────────────
  if (session.state === 'AWAIT_EMAIL') {
    // Basic email validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(text)) {
      bot.sendMessage(
        chatId,
        '❌ That doesn\'t look like a valid email address.\nPlease enter a valid Email Address:'
      );
      return;
    }

    session.email = text;
    session.state = 'AWAIT_LINK';

    // Step 3 — Show "Link 🔗 App" button
    bot.sendMessage(chatId, 'Please Link your Telegram', {
      reply_markup: {
        inline_keyboard: [
          [
            {
              text: 'Link 🔗 App',
              callback_data: 'LINK_TELEGRAM'
            }
          ]
        ]
      }
    });
    return;
  }

  // ── STATE: Done — ignore stray messages ──────────────────
  if (session.state === 'DONE') {
    bot.sendMessage(chatId, '✅ You are already registered! Tap the button below to open the app.', {
      reply_markup: {
        inline_keyboard: [
          [
            {
              text: '🔐 Open CRYPTON App',
              url: buildAppUrl(msg.from, session)
            }
          ]
        ]
      }
    });
    return;
  }
});

// ────────────────────────────────────────────────────────────
// Callback Query Handler — inline button taps
// ────────────────────────────────────────────────────────────
bot.on('callback_query', async (query) => {
  const chatId  = query.message.chat.id;
  const msgId   = query.message.message_id;
  const data    = query.data;
  const tgUser  = query.from;
  const session = sessions[chatId];

  // Always acknowledge the callback to stop the loading spinner
  await bot.answerCallbackQuery(query.id);

  if (data === 'LINK_TELEGRAM') {
    if (!session || (session.state !== 'AWAIT_LINK' && session.state !== 'DONE')) {
      bot.sendMessage(chatId, '⚠️ Session expired. Please send /start to begin again.');
      return;
    }

    // Mark session complete
    session.state      = 'DONE';
    session.tgHandle   = tgUser.username ? `@${tgUser.username}` : tgUser.first_name || 'User';
    session.tgId       = tgUser.id;

    const appUrl = buildAppUrl(tgUser, session);

    // Step 4 — Account created confirmation
    await bot.sendMessage(
      chatId,
      `✅ Account created successfully! Welcome to CRYPTON Official ${session.name}! 🎉`
    );

    // Step 5 — Open App button
    await bot.sendMessage(chatId, '🚀 Your app is ready:', {
      reply_markup: {
        inline_keyboard: [
          [
            {
              text: '🔐 Open CRYPTON App',
              url: appUrl
            }
          ]
        ]
      }
    });

    return;
  }
});

// ────────────────────────────────────────────────────────────
// Helper — Build App URL with URL Parameters
// ────────────────────────────────────────────────────────────
function buildAppUrl(tgUser, session) {
  const userName  = encodeURIComponent(session.name  || '');
  const userEmail = encodeURIComponent(session.email || '');
  const tgHandle  = encodeURIComponent(
    tgUser.username ? `@${tgUser.username}` : tgUser.first_name || 'User'
  );
  const tgID      = encodeURIComponent(tgUser.id || '');

  return `${APP_BASE_URL}?name=${userName}&email=${userEmail}&username=${tgHandle}&id=${tgID}`;
}

// ────────────────────────────────────────────────────────────
// Polling error handler
// ────────────────────────────────────────────────────────────
bot.on('polling_error', (err) => {
  console.error('[Polling Error]', err.message);
});

console.log('🤖 CRYPTON Bot is running...');
