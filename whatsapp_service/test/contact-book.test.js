'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  createContactBook,
  attachContactSync,
  resolveDisplayName,
  collectContacts,
} = require('../src/contact-book');

function makeCache() {
  return { contacts: new Map(), contactBook: createContactBook() };
}

function fakeSocket() {
  const handlers = {};
  return {
    handlers,
    ev: {
      on(event, handler) {
        handlers[event] = handler;
      },
    },
  };
}

test('Baileys contacts.upsert saved name wins over pushname', () => {
  const cache = makeCache();
  const sock = fakeSocket();
  attachContactSync(sock, '1', cache);

  sock.handlers['contacts.upsert']([
    { id: '120363346672474295@lid', jid: '905551234567@s.whatsapp.net', name: 'Kayıtlı İsim', notify: 'Pushname' },
  ]);

  assert.equal(
    cache.contactBook.resolveDisplay('120363346672474295@lid', 'Pushname'),
    'Kayıtlı İsim',
  );
  // Aynı kişi telefon numarasıyla (JID) göründüğünde de çözülmeli.
  assert.equal(
    cache.contactBook.resolveDisplay('905551234567@s.whatsapp.net', 'Pushname'),
    'Kayıtlı İsim',
  );
});

test('contacts.update keeps aliases and updates the saved name', () => {
  const cache = makeCache();
  const book = cache.contactBook;
  book.upsertAll([
    { id: '111@lid', jid: '905550000001@s.whatsapp.net', name: 'Eski Ad', notify: 'Nick' },
  ]);
  book.updateAll([{ id: '111@lid', name: 'Yeni Ad' }]);

  assert.equal(book.resolveDisplay('905550000001@s.whatsapp.net', 'Nick'), 'Yeni Ad');
  assert.equal(book.list().length, 1);
});

test('messaging-history.set populates saved contacts and group subjects', () => {
  const cache = makeCache();
  const sock = fakeSocket();
  attachContactSync(sock, '1', cache);

  sock.handlers['messaging-history.set']({
    contacts: [{ id: '222@s.whatsapp.net', name: 'Geçmiş Kişi' }],
    chats: [{ id: '333-444@g.us', name: 'Aile Grubu' }],
  });

  assert.equal(cache.contactBook.resolveDisplay('222@s.whatsapp.net', '222'), 'Geçmiş Kişi');
  assert.equal(cache.contactBook.getChatName('333-444@g.us'), 'Aile Grubu');
});

test('group subject arrives via groups.update', () => {
  const cache = makeCache();
  const book = cache.contactBook;
  book.upsertChats([{ id: '999@g.us', subject: 'Önce' }]);
  book.updateChats([{ id: '999@g.us', subject: 'Sonra' }]);

  assert.equal(book.getChatName('999@g.us'), 'Sonra');
});

test('message-learned pushname never overwrites a saved name', () => {
  const cache = makeCache();
  const book = cache.contactBook;
  book.upsertAll([{ id: '444@s.whatsapp.net', name: 'Defter Adı' }]);
  book.learnFromMessage('444@s.whatsapp.net', 'Pushname');

  assert.equal(book.resolveDisplay('444@s.whatsapp.net', 'Pushname'), 'Defter Adı');
  assert.equal(book.get('444@s.whatsapp.net').pushname, 'Pushname');
});

test('resolveDisplayName /api chats helper prefers saved contact name', () => {
  const cache = makeCache();
  cache.contactBook.upsertAll([{ id: '555@s.whatsapp.net', name: 'Gerçek Ad' }]);

  assert.equal(resolveDisplayName(cache, '555@s.whatsapp.net', 'Pushname'), 'Gerçek Ad');
  assert.equal(resolveDisplayName(cache, '555@s.whatsapp.net', '555'), 'Gerçek Ad');
  assert.equal(resolveDisplayName(cache, '666@s.whatsapp.net', '666'), '666');
});

test('collectContacts exposes saved name, pushname and number', () => {
  const cache = makeCache();
  cache.contactBook.upsertAll([
    { id: '777@lid', jid: '905550000007@s.whatsapp.net', name: 'Defter', notify: 'Takma' },
  ]);

  const contacts = collectContacts(cache);
  assert.equal(contacts.length, 1);
  assert.equal(contacts[0].name, 'Defter');
  assert.equal(contacts[0].pushname, 'Takma');
  assert.equal(contacts[0].number, '905550000007');
});
