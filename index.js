/**
 * ╔══════════════════════════════════════════════════════════╗
 * ║          CRYPTON — Telegram Registration Bot             ║
 * ║          Node.js  |  node-telegram-bot-api + Firebase   ║
 * ╚══════════════════════════════════════════════════════════╝
 *
 * Setup:
 *   npm install
 *   node index.js
 *
 * Required Environment Variables (set on Render.com):
 *   BOT_TOKEN              — Telegram Bot Token
 *   FIREBASE_PROJECT_ID    — e.g. crypton-app-871b1
 *   FIREBASE_CLIENT_EMAIL  — from Firebase Service Account JSON
 *   FIREBASE_PRIVATE_KEY   — from Firebase Service Account JSON (include \n)
 */

const TelegramBot = require('node-telegram-bot-api');
const admin       = require('firebase-admin');

// ────────────────────────────────────────────────────────────
// Firebase Admin SDK — init using Environment Variables
// ────────────────────────────────────────────────────────────
admin.initializeApp({
  credential: admin.credential.cert({
    projectId:   process.env.FIREBASE_PROJECT_ID,
    clientEmail: process.env.FIREBASE_CLIENT_EMAIL,
    // Render stores \n as literal \\n — replace it back
    privateKey:  (process.env.FIREBASE_PRIVATE_KEY || '').replace(/\\n/g, '\n'),
  }),
});

const db = admin.firestore();
console.log('[Firebase] Firestore Admin initialized ✓');

// ────────────────────────────────────────────────────────────
// Bot Token & App URL
// ────────────────────────────────────────────────────────────
const BOT_TOKEN    = process.env.BOT_TOKEN;
const APP_BASE_URL = 'https://bishal700511.github.io/Crypton-App/';

if (!BOT_TOKEN) {
  console.error('❌ BOT_TOKEN environment variable is not set!');
  process.exit(1);
}

// ────────────────────────────────────────────────────────────
// Initialize Bot (polling mode)
// ────────────────────────────────────────────────────────────
const bot = new TelegramBot(BOT_TOKEN, { polling: true });

// ── In-memory session store  { chatId → sessionObject } ─────
const sessions = {};

// ────────────────────────────────────────────────────────────
// Helper — Get today's date in DD-MM-YYYY format
// ────────────────────────────────────────────────────────────
function getTodayDate() {
  const now  = new Date();
  const dd   = String(now.getDate()).padStart(2, '0');
  const mm   = String(now.getMonth() + 1).padStart(2, '0');
  const yyyy = now.getFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

// ────────────────────────────────────────────────────────────
// Helper — Build App URL with URL Parameters
// ────────────────────────────────────────────────────────────
function buildAppUrl(tgUser, session) {
  const userName  = encodeURIComponent(session.name  || '');
  const userEmail = encodeURIComponent(session.email || '');
  const tgHandle  = encodeURIComponent(
    tgUser.username ? `@${tgUser.username}` : tgUser.first_name || 'User'
  );
  const tgID = encodeURIComponent(tgUser.id || '');
  return `${APP_BASE_URL}?name=${userName}&email=${userEmail}&username=${tgHandle}&id=${tgID}`;
}

// ────────────────────────────────────────────────────────────
// Helper — Check if user already registered in Firestore
// Returns user data object or null
// ────────────────────────────────────────────────────────────
async function getRegisteredUser(tgId) {
  try {
    const docRef = db.collection('users').doc(String(tgId));
    const snap   = await docRef.get();
    return snap.exists ? snap.data() : null;
  } catch (err) {
    console.error('[Firestore] getRegisteredUser error:', err.message);
    return null;
  }
}

// ────────────────────────────────────────────────────────────
// Helper — Save new user to Firestore
// ────────────────────────────────────────────────────────────
async function saveUser(tgUser, session) {
  try {
    const tgHandle = tgUser.username ? `@${tgUser.username}` : tgUser.first_name || 'User';
    const docRef   = db.collection('users').doc(String(tgUser.id));
    await docRef.set({
      name:             session.name,
      email:            session.email,
      telegramId:       String(tgUser.id),
      telegramHandle:   tgHandle,
      registrationDate: getTodayDate(),
      createdAt:        admin.firestore.FieldValue.serverTimestamp(),
    });
    console.log(`[Firestore] User saved: ${tgHandle} (${tgUser.id})`);
    return true;
  } catch (err) {
    console.error('[Firestore] saveUser error:', err.message);
    return false;
  }
}

// ────────────────────────────────────────────────────────────
// /start — Entry point
// ────────────────────────────────────────────────────────────
bot.onText(/\/start/, async (msg) => {
  const chatId = msg.chat.id;
  const tgUser = msg.from;

  // Check if already registered in Firestore
  const existingUser = await getRegisteredUser(tgUser.id);

  if (existingUser) {
    // ── Welcome Back flow ──────────────────────────────────
    const appUrl = buildAppUrl(tgUser, existingUser);
    await bot.sendMessage(
      chatId,
      `👋 Welcome back, *${existingUser.name}*! 🎉\n\nYou are already a member of CRYPTON Official.`,
      { parse_mode: 'Markdown' }
    );
    await bot.sendMessage(chatId, '🚀 Open your app below:', {
      reply_markup: {
        inline_keyboard: [[
          { text: '🔐 Open CRYPTON App', web_app: { url: appUrl } }
        ]]
      }
    });
    return;
  }

  // ── New user — start registration ─────────────────────────
  sessions[chatId] = { state: 'AWAIT_NAME' };
  await bot.sendMessage(
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

  if (text.startsWith('/')) return;
  if (!text) return;

  const session = sessions[chatId];

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

    bot.sendMessage(chatId, 'Please Link your Telegram', {
      reply_markup: {
        inline_keyboard: [[
          { text: 'Link 🔗 App', callback_data: 'LINK_TELEGRAM' }
        ]]
      }
    });
    return;
  }

  // ── STATE: Done ───────────────────────────────────────────
  if (session.state === 'DONE') {
    const appUrl = buildAppUrl(msg.from, session);
    bot.sendMessage(chatId, '✅ You are already registered! Tap below to open the app.', {
      reply_markup: {
        inline_keyboard: [[
          { text: '🔐 Open CRYPTON App', web_app: { url: appUrl } }
        ]]
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
  const data    = query.data;
  const tgUser  = query.from;
  const session = sessions[chatId];

  await bot.answerCallbackQuery(query.id);

  if (data === 'LINK_TELEGRAM') {
    if (!session || (session.state !== 'AWAIT_LINK' && session.state !== 'DONE')) {
      bot.sendMessage(chatId, '⚠️ Session expired. Please send /start to begin again.');
      return;
    }

    // Save user to Firestore
    const saved = await saveUser(tgUser, session);

    if (!saved) {
      bot.sendMessage(chatId, '⚠️ Could not save your data. Please try /start again.');
      return;
    }

    // Update session
    session.state    = 'DONE';
    session.tgHandle = tgUser.username ? `@${tgUser.username}` : tgUser.first_name || 'User';
    session.tgId     = tgUser.id;

    const appUrl = buildAppUrl(tgUser, session);

    // Confirmation message
    await bot.sendMessage(
      chatId,
      `✅ Account created successfully! Welcome to CRYPTON Official ${session.name}! 🎉`
    );

    // Open App button
    await bot.sendMessage(chatId, '🚀 Your app is ready:', {
      reply_markup: {
        inline_keyboard: [[
          { text: '🔐 Open CRYPTON App', web_app: { url: appUrl } }
        ]]
      }
    });
  }
});

// ────────────────────────────────────────────────────────────
// Polling error handler
// ────────────────────────────────────────────────────────────
bot.on('polling_error', (err) => {
  console.error('[Polling Error]', err.message);
});

console.log('🤖 CRYPTON Bot is running...');
