'use strict';

/**
 * Baileys 6.x does not expose a `sock.contacts` map. Saved contacts arrive via
 * `contacts.upsert`, `contacts.update` and `messaging-history.set` events, and
 * group subjects via `chats.*` / `groups.*` events. This module keeps a
 * per-user book built from those events so display names come from the phone's
 * address book instead of a sender's pushname or a raw JID.
 */

function digits(value) {
  return String(value || '').replace(/\D/g, '');
}

function isRawJid(value) {
  return /@(?:s\.whatsapp\.net|c\.us|lid|g\.us|broadcast)$/.test(String(value || ''));
}

function isPlaceholder(value, jid) {
  const raw = String(value || '').trim();
  if (!raw) return true;
  if (raw === jid) return true;
  if (digits(raw) && digits(raw) === digits(jid)) return true;
  if (/^\d+$/.test(raw)) return true;
  return isRawJid(raw);
}

function aliasKeys(record) {
  const keys = new Set();
  for (const value of [record.id, record.jid, record.lid]) {
    if (value) keys.add(String(value));
  }
  const number = digits(record.jid || record.id);
  if (number) keys.add(number);
  return Array.from(keys);
}

function mergeRecord(target, source) {
  for (const field of ['id', 'jid', 'lid', 'name', 'pushname', 'number', 'imgUrl', 'status', 'isBusiness', 'isEnterprise']) {
    const value = source[field];
    if (value === undefined || value === null || value === '') continue;
    // A saved name is authoritative; never replace it with a pushname.
    if (field === 'name' && target.name && source.__fromMessage) continue;
    if (field === 'pushname' && target.pushname && !source.__fromMessage) continue;
    target[field] = value;
  }
  return target;
}

class ContactBook {
  constructor() {
    this.byAlias = new Map();
    this.chatNames = new Map();
  }

  get(jid) {
    if (!jid) return null;
    return this.byAlias.get(String(jid)) || null;
  }

  _register(record) {
    for (const alias of aliasKeys(record)) {
      const existing = this.byAlias.get(alias);
      // When two records collide on an alias, prefer the one with a saved name.
      if (existing && existing !== record && existing.name && !record.name) continue;
      this.byAlias.set(alias, record);
    }
  }

  _findOrCreate(contact) {
    for (const alias of aliasKeys(contact)) {
      const existing = this.byAlias.get(alias);
      if (existing) return existing;
    }
    const record = {};
    this._register(record);
    return record;
  }

  upsertAll(contacts) {
    for (const contact of contacts || []) {
      if (!contact || !contact.id) continue;
      const record = this._findOrCreate(contact);
      mergeRecord(record, {
        id: contact.id,
        jid: contact.jid,
        lid: contact.lid,
        name: contact.name,
        pushname: contact.notify,
        number: digits(contact.jid || contact.id),
        imgUrl: contact.imgUrl,
        status: contact.status,
      });
      this._register(record);
    }
  }

  updateAll(updates) {
    for (const update of updates || []) {
      if (!update || !update.id) continue;
      const record = this._findOrCreate(update);
      mergeRecord(record, {
        id: update.id,
        jid: update.jid,
        lid: update.lid,
        name: update.name,
        pushname: update.notify,
        number: digits(update.jid || update.id),
        imgUrl: update.imgUrl,
        status: update.status,
      });
      this._register(record);
    }
  }

  learnFromMessage(jid, pushname) {
    if (!jid) return;
    const record = this._findOrCreate({ id: jid });
    if (pushname) mergeRecord(record, { pushname, __fromMessage: true });
    this._register(record);
  }

  resolveDisplay(jid, current) {
    const record = this.get(jid);
    const saved = String((record && record.name) || '').trim();
    if (saved) return saved;
    if (!isPlaceholder(current, jid)) return String(current).trim();
    if (record && record.pushname) return record.pushname;
    return digits(jid) || String(jid || '');
  }

  list() {
    return Array.from(new Set(this.byAlias.values()));
  }

  // --- group / chat subjects ------------------------------------------------

  upsertChats(chats) {
    for (const chat of chats || []) {
      if (!chat || !chat.id) continue;
      const name = chat.name || chat.subject;
      if (name) this.chatNames.set(String(chat.id), String(name));
    }
  }

  updateChats(updates) {
    for (const update of updates || []) {
      if (!update || !update.id) continue;
      const name = update.name || update.subject;
      if (name) this.chatNames.set(String(update.id), String(name));
    }
  }

  getChatName(jid) {
    return jid ? (this.chatNames.get(String(jid)) || '') : '';
  }
}

function createContactBook() {
  return new ContactBook();
}

/** Register Baileys listeners that keep the contact book up to date. */
function attachContactSync(sock, userId, cache) {
  const book = cache.contactBook;
  if (!sock || !sock.ev || !book) return;
  sock.ev.on('contacts.upsert', (contacts) => book.upsertAll(contacts));
  sock.ev.on('contacts.update', (updates) => book.updateAll(updates));
  sock.ev.on('messaging-history.set', (payload) => {
    book.upsertAll(payload && payload.contacts);
    book.upsertChats(payload && payload.chats);
  });
  sock.ev.on('chats.upsert', (chats) => book.upsertChats(chats));
  sock.ev.on('chats.update', (updates) => book.updateChats(updates));
  sock.ev.on('groups.upsert', (groups) => book.upsertChats(groups));
  sock.ev.on('groups.update', (updates) => book.updateChats(updates));
}

/** Chat display name helper used by GET /api/chats. */
function resolveDisplayName(cache, jid, current) {
  return cache.contactBook.resolveDisplay(jid, current);
}

/** Contact list used by GET /api/contacts. */
function collectContacts(cache) {
  const seen = new Set();
  const contacts = [];

  const add = (record) => {
    const id = record.id || record.jid || record.lid;
    if (!id || seen.has(String(id))) return;
    seen.add(String(id));
    for (const alias of aliasKeys(record)) seen.add(alias);
    contacts.push({
      id: String(id),
      jid: record.jid || '',
      lid: record.lid || '',
      number: record.number || digits(record.jid || record.id),
      name: record.name || record.pushname || digits(record.id) || String(id),
      pushname: record.pushname || '',
      isBusiness: Boolean(record.isBusiness),
      isEnterprise: Boolean(record.isEnterprise),
      profilePicUrl: record.imgUrl || '',
      status: record.status || '',
    });
  };

  for (const record of cache.contactBook.list()) add(record);
  // Mesajlardan öğrenilmiş, henüz kişi defterine girmemiş kayıtlar.
  for (const [jid, contact] of cache.contacts || []) {
    if (seen.has(String(jid))) continue;
    add({
      id: contact.id || jid,
      name: contact.name,
      pushname: contact.pushname,
      number: contact.number || digits(jid),
    });
  }
  return contacts;
}

module.exports = {
  ContactBook,
  createContactBook,
  attachContactSync,
  resolveDisplayName,
  collectContacts,
  isPlaceholder,
  digits,
};
