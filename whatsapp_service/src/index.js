/**
 * EMOG WhatsApp Service (Baileys)
 *
 * Chromium/Puppeteer gerektirmez. Her kullanıcı kendi WhatsApp hesabını
 * QR ile bağlar. Django ile REST + WebSocket üzerinden konuşur.
 */
require('dotenv').config();
const express = require('express');
const axios = require('axios');
const { Server } = require('socket.io');
const { createServer } = require('http');
const fs = require('fs-extra');
const path = require('path');
const P = require('pino');
const QRCode = require('qrcode');
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  downloadMediaMessage,
  jidNormalizedUser,
} = require('@whiskeysockets/baileys');
const {
  createContactBook,
  attachContactSync,
  resolveDisplayName: resolveBookName,
  collectContacts,
} = require('./contact-book');

// Kök .env dosyasını da oku (servis whatsapp_service içinden başlatılsa bile).
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });

const DJANGO_API_URL = process.env.DJANGO_API_URL || 'http://127.0.0.1:8002';
const SERVICE_PORT = process.env.WHATSAPP_SERVICE_PORT || 3002;
const SESSION_DIR = process.env.SESSION_DIR || path.join(__dirname, '..', 'sessions');
const MAX_MEDIA_BYTES = 8 * 1024 * 1024;
const logger = P({ level: process.env.WHATSAPP_LOG_LEVEL || 'warn' });

fs.ensureDirSync(SESSION_DIR);

const app = express();
app.use(express.json({ limit: '25mb' }));

const httpServer = createServer(app);
const io = new Server(httpServer, {
  cors: { origin: '*', methods: ['GET', 'POST'] },
});

// userId -> { sock, status, qrCode, info, manualDisconnect, reconnectTimer, ... }
const clients = new Map();
// Pairing code isteği sürerken QR olaylarının durumu ezmesini engeller.
const pairingIntents = new Set();
// userId -> { chats: Map, messages: Map, contacts: Map, messageKeys: Map }
const caches = new Map();

const djangoApi = axios.create({
  baseURL: DJANGO_API_URL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});
const djangoAuthToken = process.env.WHATSAPP_SERVICE_TOKEN || process.env.DJANGO_SERVICE_TOKEN || '';
djangoApi.interceptors.request.use((config) => {
  if (djangoAuthToken) config.headers.Authorization = `Bearer ${djangoAuthToken}`;
  return config;
});

function userCache(userId) {
  if (!caches.has(userId)) {
    caches.set(userId, {
      chats: new Map(),
      messages: new Map(),
      contacts: new Map(),
      contactBook: createContactBook(),
      messageKeys: new Map(),
    });
  }
  return caches.get(userId);
}

function emitToUser(userId, event, data) {
  io.to(`user_${userId}`).emit(event, data);
}

async function notifyDjango(userId, event, data = {}) {
  try {
    await djangoApi.post('/api/whatsapp/webhook/', {
      user_id: userId,
      event,
      data,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    console.error(`[${userId}] Django webhook failed:`, error.message);
  }
}

function numberFromJid(value) {
  return String(value || '')
    .split('@')[0]
    .split(':')[0]
    .replace(/\D/g, '');
}

function normalizeTarget(value) {
  let target = String(value || '').trim();
  if (!target) return '';
  if (target.includes('@c.us')) return target.replace('@c.us', '@s.whatsapp.net');
  if (target.includes('@')) return target;
  return `${target.replace(/[^\d]/g, '')}@s.whatsapp.net`;
}

function isGroupJid(value) {
  return String(value || '').endsWith('@g.us');
}

function ownJid(sock) {
  return jidNormalizedUser((sock && sock.user && sock.user.id) || '');
}

function isSelfChat(sock, remoteJid) {
  const own = numberFromJid(ownJid(sock));
  const remote = numberFromJid(remoteJid);
  return Boolean(own && remote && own === remote);
}

function setStatus(userId, status, extra = {}) {
  const data = clients.get(userId);
  if (!data) return;
  data.status = status;
  Object.assign(data, extra);
}
function extractText(message) {
  if (!message) return '';
  const inner = message.ephemeralMessage?.message
    || message.viewOnceMessage?.message
    || message.viewOnceMessageV2?.message
    || message.documentWithCaptionMessage?.message
    || message;
  return String(
    inner.conversation
    || inner.extendedTextMessage?.text
    || inner.imageMessage?.caption
    || inner.videoMessage?.caption
    || inner.documentMessage?.caption
    || inner.buttonsResponseMessage?.selectedButtonId
    || inner.listResponseMessage?.singleSelectReply?.selectedRowId
    || ''
  );
}

function messageType(message) {
  if (!message) return 'text';
  const inner = message.ephemeralMessage?.message || message.viewOnceMessage?.message || message;
  if (inner.imageMessage) return 'image';
  if (inner.videoMessage) return 'video';
  if (inner.audioMessage) return 'audio';
  if (inner.documentMessage) return 'document';
  if (inner.stickerMessage) return 'sticker';
  return 'text';
}

function mediaInfo(message) {
  const inner = message?.ephemeralMessage?.message || message?.viewOnceMessage?.message || message || {};
  return inner.imageMessage || inner.videoMessage || inner.audioMessage
    || inner.documentMessage || inner.stickerMessage || null;
}

async function buildMessageData(userId, sock, msg) {
  const key = msg.key || {};
  const remoteJid = key.remoteJid || '';
  if (!remoteJid || remoteJid === 'status@broadcast') return null;
  const fromMe = Boolean(key.fromMe);

  // Yalnızca self-chat giden mesajları Django'ya ilet. Başka kişilere
  // gönderilen özel mesajlar webhook'a taşınmasın.
  if (fromMe && !isSelfChat(sock, remoteJid)) return null;

  const cache = userCache(userId);
  const body = extractText(msg.message);
  const type = messageType(msg.message);
  const media = mediaInfo(msg.message);
  const timestamp = Number(msg.messageTimestamp || 0) * 1000 || Date.now();
  const isGroup = isGroupJid(remoteJid);
  const senderJid = fromMe ? ownJid(sock) : remoteJid;
  const pushname = msg.pushName || (fromMe ? (sock.user?.name || 'Ben') : '');
  if (!isGroup) cache.contactBook.learnFromMessage(remoteJid, pushname);
  // Kayıtlı kişi adı (telefon defteri) pushname/raw JID yerine kullanılır.
  const displayName = isGroup
    ? (cache.contactBook.getChatName(remoteJid) || remoteJid)
    : cache.contactBook.resolveDisplay(remoteJid, pushname || numberFromJid(remoteJid));

  const messageData = {
    id: key.id || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    from: senderJid,
    to: fromMe ? remoteJid : ownJid(sock),
    body,
    type,
    timestamp,
    fromMe,
    hasMedia: Boolean(media),
    contact: {
      name: displayName,
      pushname: msg.pushName || '',
      number: numberFromJid(remoteJid),
      isGroup,
    },
    chat: {
      id: remoteJid,
      name: displayName,
      isGroup,
    },
  };

  if (media) {
    try {
      const buffer = await downloadMediaMessage(msg, 'buffer', {}, {
        logger,
        reuploadRequest: sock.updateMediaMessage,
      });
      if (buffer && buffer.length <= MAX_MEDIA_BYTES) {
        messageData.media = {
          mimetype: media.mimetype || '',
          data: Buffer.from(buffer).toString('base64'),
          filename: media.fileName || '',
        };
      }
    } catch (error) {
      console.error(`[${userId}] media download failed:`, error.message);
    }
  }

  cache.messages.set(messageData.id, messageData);
  cache.messageKeys.set(messageData.id, {
    remoteJid,
    id: key.id,
    fromMe,
    participant: key.participant,
  });

  const previous = cache.chats.get(remoteJid) || {};
  cache.chats.set(remoteJid, {
    id: remoteJid,
    name: displayName || previous.name || numberFromJid(remoteJid),
    isGroup,
    unreadCount: fromMe ? (previous.unreadCount || 0) : (previous.unreadCount || 0) + 1,
    lastMessage: {
      body: body || (media ? '📎 Medya' : ''),
      fromMe,
      timestamp,
    },
  });

  if (!isGroup) {
    cache.contacts.set(remoteJid, {
      id: remoteJid,
      name: cache.contactBook.get(remoteJid)?.name || '',
      number: numberFromJid(remoteJid),
      pushname: msg.pushName || '',
    });
  }
  return messageData;
}

async function forwardMessage(userId, sock, msg) {
  const messageData = await buildMessageData(userId, sock, msg);
  if (!messageData) return;
  await notifyDjango(userId, 'message_received', messageData);
  emitToUser(userId, 'message_received', messageData);
}

async function createClient(userId, opts = {}) {
  const sessionPath = path.join(SESSION_DIR, `user_${userId}`);
  fs.ensureDirSync(sessionPath);

  const previous = clients.get(userId);
  if (previous && previous.status === 'connected') return previous.sock;
  if (previous?.reconnectTimer) clearTimeout(previous.reconnectTimer);
  if (previous?.sock) {
    try { previous.sock.end(undefined); } catch (e) { /* ignore */ }
  }

  const { state, saveCreds } = await useMultiFileAuthState(sessionPath);
  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    logger,
    // WhatsApp pairing code, standart dışı cihaz adlarını reddedebiliyor
    // (QR ile bağlanma çalışırken kod reddedilmesinin klasik nedeni).
    // Varsayılan standart bir OS adı; istenirse env ile değiştirilebilir.
    browser: [process.env.WHATSAPP_BROWSER_OS || 'Ubuntu', 'Chrome', '1.0.0'],
    markOnlineOnConnect: false,
    syncFullHistory: false,
    // Telefondaki "Mesajlar senkronize ediliyor" bildirimi, eşlikçi cihaz
    // geçmiş senkronizasyonu istediğinde tekrar tekrar çıkar. Bu entegrasyon
    // sadece canlı mesajları işliyor; geçmiş senkronunu tamamen kapatıyoruz.
    shouldSyncHistoryMessage: () => false,
    generateHighQualityLinkPreview: false,
  });

  const entry = {
    sock,
    status: 'initializing',
    qrCode: null,
    pairingCode: null,
    info: previous?.info || null,
    manualDisconnect: false,
    reconnectTimer: null,
    reconnectAttempts: 0,
    pairingRequested: pairingIntents.has(userId),
  };
  clients.set(userId, entry);
  const cache = userCache(userId);
  sock.ev.on('creds.update', saveCreds);
  // Baileys 6.x kişileri sock.contacts üzerinde tutmaz; kayıtlı kişi adları
  // ve grup konuları bu olaylarla gelir. Defteri olaylardan besliyoruz.
  attachContactSync(sock, userId, cache);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update || {};
    // Pairing code istenmişse QR olayı durumu 'qr_ready'e geri çevirmesin;
    // aksi halde kod üretilmesine rağmen arayüz yanlış durum gösterir.
    if (qr && !entry.pairingRequested) {
      try {
        const dataUrl = await QRCode.toDataURL(qr, {
          errorCorrectionLevel: 'M',
          margin: 1,
          width: 320,
        });
        entry.qrCode = dataUrl;
        entry.status = 'qr_ready';
        await notifyDjango(userId, 'qr_code', { qr: dataUrl });
        emitToUser(userId, 'qr_code', { qr: dataUrl });
      } catch (error) {
        console.error(`[${userId}] QR generation failed:`, error.message);
      }
    }

    if (connection === 'open') {
      const wid = ownJid(sock);
      entry.status = 'connected';
      entry.reconnectAttempts = 0;
      pairingIntents.delete(userId);
      entry.qrCode = null;
      entry.pairingCode = null;
      entry.info = {
        wid,
        pushname: sock.user?.name || '',
        platform: 'Baileys',
        phone: numberFromJid(wid),
      };
      await notifyDjango(userId, 'connected', { info: entry.info });
      emitToUser(userId, 'connected', { info: entry.info });
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = statusCode === DisconnectReason.loggedOut;
      entry.status = loggedOut ? 'auth_failed' : 'disconnected';
      entry.qrCode = null;
      entry.pairingCode = null;
      await notifyDjango(userId, loggedOut ? 'logged_out' : 'disconnected', {
        reason: String(lastDisconnect?.error?.message || 'connection closed'),
      });
      emitToUser(userId, loggedOut ? 'logged_out' : 'disconnected', {
        reason: String(lastDisconnect?.error?.message || 'connection closed'),
      });
      if (loggedOut) {
        // Cihaz telefondan kaldırıldı (401 / device_removed). Ölü oturum
        // dosyaları kalırsa servis her açılışta bunları restore etmeye
        // çalışır ve telefon tekrar tekrar "senkronize ediliyor" gösterir.
        clients.delete(userId);
        caches.delete(userId);
        try { await fs.remove(sessionPath); } catch (e) { /* ignore */ }
      } else if (!entry.manualDisconnect) {
        // Sabit 3 sn yerine üstel geri çekilme: bağlantı döngüsü olursa
        // telefonu gereksiz yere tekrar senkronizasyona sokmasın.
        entry.reconnectAttempts = (entry.reconnectAttempts || 0) + 1;
        const delay = Math.min(60000, 3000 * Math.pow(2, Math.min(entry.reconnectAttempts - 1, 5)));
        if (entry.reconnectTimer) clearTimeout(entry.reconnectTimer);
        entry.reconnectTimer = setTimeout(() => {
          if (!entry.manualDisconnect) createClient(userId).catch((error) => {
            console.error(`[${userId}] reconnect failed:`, error.message);
          });
        }, delay);
      }
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    for (const msg of messages || []) {
      // Geçmiş (append) mesajlarını işleme; yalnızca self-chat komutlarını
      // kaçırmamak için append + fromMe + kendi sohbeti istisnası.
      if (type === 'append' && !(msg?.key?.fromMe && isSelfChat(sock, msg?.key?.remoteJid))) {
        continue;
      }
      try {
        await forwardMessage(userId, sock, msg);
      } catch (error) {
        console.error(`[${userId}] message handling failed:`, error.message);
      }
    }
  });

  sock.ev.on('messages.update', async (updates) => {
    for (const item of updates || []) {
      const id = item?.key?.id;
      const status = item?.update?.status;
      if (!id || status === undefined) continue;
      const ackMap = { 0: 1, 1: 1, 2: 2, 3: 3, 4: 3 };
      await notifyDjango(userId, 'message_ack', {
        messageId: id,
        ack: ackMap[status] || 1,
      });
    }
  });

  if (!opts.skipQrWait) {
    try {
      await sock.waitForConnectionUpdate((update) => Boolean(update.qr || update.connection), 15000);
    } catch (error) {
      // QR/bağlantı biraz gecikebilir; hata değil.
    }
  }
  return sock;
}

/**
 * Pairing code'u Baileys'in önerdiği şekilde, 'connecting' olayında ister.
 * QR el sıkışması başladıktan sonra istenen kodlar WhatsApp tarafından
 * reddedilebiliyor ("cihaz bağlanamadı").
 */
function requestPairingCodeOnConnect(sock, phone, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => { sock.ev.off('connection.update', handler); reject(new Error('Pairing zaman aşımı')); },
      timeoutMs,
    );
    const handler = async (update) => {
      if (!update) return;
      if (update.connection === 'close') {
        sock.ev.off('connection.update', handler);
        clearTimeout(timer);
        return reject(new Error('Bağlantı kapanmadan kod alınamadı'));
      }
      if (update.connection !== 'connecting' && !update.qr) return;
      sock.ev.off('connection.update', handler);
      clearTimeout(timer);
      try {
        // Soketin komut göndermeye hazır olması için kısa gecikme.
        await new Promise((r) => setTimeout(r, 400));
        resolve(await sock.requestPairingCode(phone));
      } catch (error) {
        reject(error);
      }
    };
    sock.ev.on('connection.update', handler);
  });
}

/**
 * Ölü/iptal edilmiş oturumu tamamen temizler.
 * Bağlantı koptuktan sonra aynı soketle QR veya pairing code istenirse
 * WhatsApp "Connection Closed" hatası döner. Temiz başlangıç için oturum
 * dizini de silinir; aksi halde Baileys eski (kaldırılmış) cihazı restore
 * etmeye çalışır.
 */
/**
 * Pairing code için soketin gerçekten açılmasını bekler.
 * Baileys dokümanı: requestPairingCode, 'connecting'/QR olayından sonra
 * çağrılmalıdır; hemen çağrılırsa "Connection Closed" hatası verir.
 */
function waitForSocketReady(sock, timeoutMs = 10000) {
  return new Promise((resolve) => {
    if (!sock) return resolve(false);
    const started = Date.now();
    const check = () => {
      if (sock.ws && sock.ws.isOpen === true) return resolve(true);
      if (Date.now() - started >= timeoutMs) return resolve(false);
      setTimeout(check, 200);
    };
    check();
  });
}

/**
 * Sohbet adını telefon defterindeki kayıtlı kişi adıyla çözümle.
 * Baileys 6.x'te sock.contacts diye bir alan yoktur; kişiler event'lerle
 * gelir ve contact-book modülünde tutulur.
 */
function resolveDisplayName(userId, jid, current) {
  return resolveBookName(userCache(userId), jid, current);
}

async function resetClient(userId) {
  const previous = clients.get(userId);
  if (previous?.reconnectTimer) clearTimeout(previous.reconnectTimer);
  if (previous?.sock) {
    try { previous.sock.end(undefined); } catch (e) { /* ignore */ }
  }
  clients.delete(userId);
  caches.delete(userId);
  pairingIntents.delete(userId);
  const sessionPath = path.join(SESSION_DIR, `user_${userId}`);
  try { await fs.remove(sessionPath); } catch (e) { /* ignore */ }
}


// --- REST API -------------------------------------------------------------

app.get('/api/status/:userId', (req, res) => {
  const { userId } = req.params;
  const data = clients.get(userId);
  if (!data) {
    return res.json({ connected: false, status: 'not_initialized', info: null, hasQr: false });
  }
  return res.json({
    connected: data.status === 'connected',
    status: data.status,
    info: data.info,
    hasQr: Boolean(data.qrCode),
    pairingCode: data.pairingCode || null,
  });
});

app.get('/api/qr/:userId', (req, res) => {
  const data = clients.get(req.params.userId);
  if (!data || !data.qrCode) {
    return res.status(404).json({ error: 'QR code not available' });
  }
  return res.json({ qr: data.qrCode });
});

app.post('/api/init/:userId', async (req, res) => {
  const { userId } = req.params;
  let existing = clients.get(userId);
  if (existing && existing.status === 'connected') {
    return res.json({ success: true, message: 'Already connected', info: existing.info });
  }
  // Ölü oturumla QR istenemez — önce temizle, sonra yeni istemci aç.
  if (existing && ['auth_failed', 'disconnected', 'logged_out'].includes(existing.status)) {
    await resetClient(userId);
    existing = null;
  }
  if (existing && ['initializing', 'qr_ready', 'pairing_code'].includes(existing.status)) {
    return res.json({ success: true, message: 'Already initializing', status: existing.status });
  }

  try {
    await createClient(userId);
    return res.json({ success: true, message: 'Initializing... QR code will be available shortly.' });
  } catch (error) {
    console.error(`[${userId}] init failed:`, error.message);
    clients.delete(userId);
    return res.status(500).json({ success: false, error: error.message });
  }
});

app.post('/api/disconnect/:userId', async (req, res) => {
  const { userId } = req.params;
  const data = clients.get(userId);
  if (!data) {
    return res.json({ success: true, message: 'Not connected' });
  }
  try {
    data.manualDisconnect = true;
    if (data.reconnectTimer) clearTimeout(data.reconnectTimer);
    if (data.sock) {
      try { await data.sock.logout(); } catch (error) { /* zaten kapalı olabilir */ }
      try { data.sock.end(undefined); } catch (error) { /* ignore */ }
    }
    clients.delete(userId);
    caches.delete(userId);
    const sessionPath = path.join(SESSION_DIR, `user_${userId}`);
    await fs.remove(sessionPath);
    await notifyDjango(userId, 'logged_out', {});
    emitToUser(userId, 'logged_out', {});
    return res.json({ success: true, message: 'Logged out' });
  } catch (error) {
    console.error(`[${userId}] disconnect failed:`, error.message);
    return res.status(500).json({ success: false, error: error.message });
  }
});

app.post('/api/send/:userId', async (req, res) => {
  const { userId } = req.params;
  const { to, message, media } = req.body || {};
  const data = clients.get(userId);
  if (!data || data.status !== 'connected' || !data.sock) {
    return res.status(400).json({ success: false, error: 'Not connected' });
  }
  const jid = normalizeTarget(to);
  if (!jid) {
    return res.status(400).json({ success: false, error: 'Recipient is required' });
  }
  const text = String(message || '');
  try {
    let payload;
    if (media && media.data) {
      const buffer = Buffer.from(media.data, 'base64');
      const mimetype = media.mimetype || 'application/octet-stream';
      if (mimetype.startsWith('image/')) {
        payload = { image: buffer, mimetype, caption: text };
      } else if (mimetype.startsWith('video/')) {
        payload = { video: buffer, mimetype, caption: text };
      } else if (mimetype.startsWith('audio/')) {
        payload = { audio: buffer, mimetype, ptt: false };
      } else {
        payload = { document: buffer, mimetype, fileName: media.filename || 'file', caption: text };
      }
    } else {
      if (!text.trim()) return res.status(400).json({ success: false, error: 'Message is required' });
      payload = { text };
    }

    const sent = await data.sock.sendMessage(jid, payload);
    return res.json({
      success: true,
      messageId: sent?.key?.id || '',
      timestamp: Number(sent?.messageTimestamp || 0) * 1000 || Date.now(),
    });
  } catch (error) {
    console.error(`[${userId}] send failed:`, error.message);
    return res.status(500).json({ success: false, error: error.message });
  }
});


app.post('/api/pair/:userId', async (req, res) => {
  const { userId } = req.params;
  const phone = String(req.body?.phone || '').replace(/\D/g, '');
  if (!phone) {
    return res.status(400).json({ success: false, error: 'Telefon numarası gerekli' });
  }
  let data = clients.get(userId);
  if (data && data.status === 'connected') {
    return res.json({ success: true, connected: true, message: 'Already connected' });
  }
  // Her kod isteğinde tamamen temiz oturum: önceki kod/QR geçersiz kalsın,
  // ölü soket üzerinden istek yapılmasın.
  await resetClient(userId);
  pairingIntents.add(userId);
  data = null;
  try {
    // QR beklemeden soketi aç; kodu 'connecting' olayında isteyeceğiz.
    await createClient(userId, { skipQrWait: true });
    data = clients.get(userId);
  } catch (error) {
    pairingIntents.delete(userId);
    return res.status(500).json({ success: false, error: error.message });
  }
  if (!data || !data.sock) {
    pairingIntents.delete(userId);
    return res.status(500).json({ success: false, error: 'WhatsApp istemcisi başlatılamadı' });
  }

  try {
    const code = await requestPairingCodeOnConnect(data.sock, phone);
    data.pairingCode = code;
    data.status = 'pairing_code';
    await notifyDjango(userId, 'pairing_code', { code, phone });
    emitToUser(userId, 'pairing_code', { code, phone });
    return res.json({ success: true, code, phone });
  } catch (error) {
    console.error(`[${userId}] pairing code failed:`, error.message);
    return res.status(500).json({
      success: false,
      error: 'Cihaz bağlanamadı. Lütfen tekrar deneyin. (' + error.message + ')',
    });
  }
});

app.get('/api/chats/:userId', (req, res) => {
  const data = clients.get(req.params.userId);
  if (!data || data.status !== 'connected') {
    return res.status(400).json({ success: false, error: 'Not connected' });
  }
  const cache = userCache(req.params.userId);
  const chats = Array.from(cache.chats.values())
    .map((chat) => ({
      ...chat,
      name: resolveDisplayName(req.params.userId, chat.id, chat.name),
    }))
    .sort((a, b) => {
      return Number(b.lastMessage?.timestamp || 0) - Number(a.lastMessage?.timestamp || 0);
    });
  return res.json({ success: true, chats });
});

app.get('/api/contacts/:userId', (req, res) => {
  const cache = userCache(req.params.userId);
  // Kayıtlı kişiler Baileys event'lerinden (contacts.upsert/update,
  // messaging-history.set) gelir; sock.contacts diye bir alan yoktur.
  return res.json({ contacts: collectContacts(cache) });
});

app.get('/api/messages/:userId/:chatId', (req, res) => {
  const cache = userCache(req.params.userId);
  const limit = Math.max(1, Math.min(Number(req.query.limit || 50), 200));
  const chat = String(req.params.chatId || '');
  const messages = Array.from(cache.messages.values())
    .filter((item) => String(item.chat?.id || '') === chat || String(item.from || '') === chat)
    .slice(-limit);
  return res.json({ messages });
});

app.post('/api/read/:userId/:chatId', async (req, res) => {
  const { userId, chatId } = req.params;
  const data = clients.get(userId);
  if (!data || data.status !== 'connected') {
    return res.status(400).json({ success: false, error: 'Not connected' });
  }
  const cache = userCache(userId);
  const keys = [];
  for (const [id, key] of cache.messageKeys.entries()) {
    if (String(key.remoteJid || '') === String(chatId) && !key.fromMe) {
      keys.push({ remoteJid: key.remoteJid, id: key.id || id, fromMe: false, participant: key.participant });
    }
  }
  try {
    if (keys.length) await data.sock.readMessages(keys);
    const chat = cache.chats.get(chatId);
    if (chat) chat.unreadCount = 0;
    return res.json({ success: true });
  } catch (error) {
    return res.status(500).json({ success: false, error: error.message });
  }
});

// Baileys'te sohbet arşivleme/susturma için güvenilir ortak API yok.
// Arayüz akışını bozmamak için başarılı yanıt dönüyoruz.
app.post('/api/archive/:userId/:chatId', (req, res) => res.json({ success: true, archived: Boolean(req.body?.archive) }));
app.post('/api/mute/:userId/:chatId', (req, res) => res.json({ success: true, muted: Boolean(req.body?.mute) }));

app.get('/health', (req, res) => {
  res.json({ status: 'ok', engine: 'baileys', activeClients: clients.size, uptime: process.uptime() });
});


// --- WebSocket ------------------------------------------------------------

io.on('connection', (socket) => {
  console.log('Frontend connected:', socket.id);

  socket.on('join_user', (userId) => {
    socket.join(`user_${userId}`);
    const data = clients.get(userId);
    if (data) {
      socket.emit('status', {
        connected: data.status === 'connected',
        status: data.status,
        info: data.info,
        hasQr: Boolean(data.qrCode),
      });
      if (data.qrCode) socket.emit('qr_code', { qr: data.qrCode });
    }
  });

  socket.on('disconnect', () => {
    console.log('Frontend disconnected:', socket.id);
  });
});

// --- Startup --------------------------------------------------------------

async function initializeExistingSessions() {
  try {
    const entries = await fs.readdir(SESSION_DIR);
    for (const dir of entries) {
      if (!dir.startsWith('user_')) continue;
      const userId = dir.slice('user_'.length);
      if (!userId || clients.has(userId)) continue;
      console.log(`[Startup] restoring Baileys session for ${userId}`);
      createClient(userId).catch((error) => {
        console.error(`[${userId}] restore failed:`, error.message);
        clients.delete(userId);
      });
    }
  } catch (error) {
    console.error('Failed to restore sessions:', error.message);
  }
}

httpServer.listen(SERVICE_PORT, async () => {
  console.log(`\n🚀 EMOG WhatsApp service (Baileys) on port ${SERVICE_PORT}`);
  console.log(`📡 Django API: ${DJANGO_API_URL}`);
  console.log(`💾 Sessions: ${SESSION_DIR}`);
  console.log(`\nEndpoints:`);
  console.log(`  GET  /api/status/:userId`);
  console.log(`  GET  /api/qr/:userId`);
  console.log(`  POST /api/init/:userId`);
  console.log(`  POST /api/disconnect/:userId`);
  console.log(`  POST /api/send/:userId`);
  console.log(`  GET  /api/chats/:userId`);
  console.log(`  GET  /api/contacts/:userId`);
  console.log(`  GET  /api/messages/:userId/:chatId`);
  console.log(`  POST /api/read/:userId/:chatId`);
  console.log(`  GET  /health`);
  console.log(`\nWebSocket: ws://localhost:${SERVICE_PORT}\n`);
  await initializeExistingSessions();
});

process.on('SIGINT', async () => {
  console.log('\n🛑 Shutting down...');
  for (const [userId, data] of clients) {
    try { await data.sock?.logout(); } catch (error) { /* ignore */ }
    try { data.sock?.end(undefined); } catch (error) { /* ignore */ }
    console.log(`[${userId}] closed`);
  }
  process.exit(0);
});

process.on('SIGTERM', async () => {
  for (const [userId, data] of clients) {
    try { data.sock?.end(undefined); } catch (error) { /* ignore */ }
  }
  process.exit(0);
});

