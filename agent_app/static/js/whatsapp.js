/**
 * EMOG WhatsApp Frontend Module
 * Handles QR code display, chat list, message sending/receiving
 * Connects to WhatsApp service via WebSocket for real-time updates
 */
(function() {
    'use strict';

    class WhatsAppManager {
        constructor() {
            this.socket = null;
            this.userId = null;
            this.currentChatId = null;
            this.chats = [];
            this.messages = [];
            this.status = 'not_initialized';
            this.qrCode = null;
            this.reconnectAttempts = 0;
            this.maxReconnectAttempts = 5;
        }

        init(userId) {
            this.userId = userId;
            // socket.io sunucusu ile ham WebSocket uyumsuz; QR/durum için
            // güvenilir HTTP polling kullanıyoruz.
            this.startPolling();
            this.loadStatus();
            // Durum polling'i gecikse bile sohbetleri yükle.
            this.loadChats();
        }

        startPolling() {
            if (this.pollTimer) clearInterval(this.pollTimer);
            this.pollTimer = setInterval(() => this.pollStatus(), 2500);
        }

        async pollStatus() {
            try {
                const response = await fetch('/api/whatsapp/status/', { credentials: 'include' });
                const data = await response.json();
                if (!response.ok) return;
                const previous = this.status;
                this.updateStatus(data);
                if (data.hasQr) {
                    this.loadQRCode();
                }
                if (data.connected && previous !== 'connected') {
                    this.onConnected(data.info || {});
                } else if (data.connected && this.chats.length === 0) {
                    // Bağlı ama liste boşsa (ilk yükleme yarışması) tekrar dene.
                    this.loadChats();
                } else if (!data.connected && data.status === 'disconnected' && previous === 'connected') {
                    this.onDisconnected('Bağlantı kesildi');
                } else if (data.status === 'auth_failed' && previous !== 'auth_failed') {
                    this.onAuthFailed('QR/oturum doğrulanamadı');
                }
            } catch (e) {
                // Polling geçici olarak başarısız olabilir; sessiz geç.
            }
        }

        connectWebSocket() {
            const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${wsProtocol}//${window.location.hostname}:3002`;
            
            try {
                this.socket = new WebSocket(wsUrl);
                
                this.socket.onopen = () => {
                    console.log('WhatsApp WebSocket connected');
                    this.reconnectAttempts = 0;
                    this.socket.send(JSON.stringify({
                        type: 'join_user',
                        userId: this.userId
                    }));
                };

                this.socket.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                };

                this.socket.onclose = () => {
                    console.log('WhatsApp WebSocket disconnected');
                    this.attemptReconnect();
                };

                this.socket.onerror = (error) => {
                    console.error('WhatsApp WebSocket error:', error);
                };
            } catch (e) {
                console.error('Failed to create WebSocket:', e);
                this.attemptReconnect();
            }
        }

        attemptReconnect() {
            if (this.reconnectAttempts < this.maxReconnectAttempts) {
                this.reconnectAttempts++;
                const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
                console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
                setTimeout(() => this.connectWebSocket(), delay);
            }
        }

        handleWebSocketMessage(data) {
            switch (data.type) {
                case 'status':
                    this.updateStatus(data);
                    break;
                case 'qr_code':
                    this.showQRCode(data.qr);
                    break;
                case 'connected':
                    this.onConnected(data.info);
                    break;
                case 'authenticated':
                    this.onAuthenticated();
                    break;
                case 'disconnected':
                    this.onDisconnected(data.reason);
                    break;
                case 'auth_failed':
                    this.onAuthFailed(data.message);
                    break;
                case 'message_received':
                    this.onMessageReceived(data);
                    break;
                case 'message_ack':
                    this.onMessageAck(data);
                    break;
                default:
                    console.log('Unknown WhatsApp event:', data.type);
            }
        }

        updateStatus(data) {
            this.status = data.status;
            this.updateStatusUI(data);
        }

        updateStatusUI(data) {
            const indicator = document.getElementById('whatsapp-status-indicator')
                || document.querySelector('#whatsapp-status-indicator-container .status-indicator');
            const text = document.getElementById('whatsapp-status-text');
            
            if (indicator) {
                indicator.className = 'status-indicator ' + (data.connected ? 'connected' : 'disconnected');
            }
            if (text) {
                const statusText = {
                    'connected': 'Bağlı',
                    'connecting': 'Bağlanıyor...',
                    'initializing': 'Başlatılıyor...',
                    'qr_ready': 'QR Kod Hazır',
                    'authenticated': 'Doğrulanıyor...',
                    'disconnected': 'Bağlantı Kesildi',
                    'auth_failed': 'Doğrulama Başarısız',
                    'not_initialized': 'Başlatılmadı'
                };
                text.textContent = statusText[data.status] || data.status;
            }
        }

        showQRCode(qr) {
            this.qrCode = qr;
            const container = document.getElementById('whatsapp-qr-container');
            const img = document.getElementById('whatsapp-qr-image');
            
            if (container && img) {
                // Baileys QR'ı data:image/png;base64 olarak gönderir.
                img.src = String(qr).startsWith('data:')
                    ? qr
                    : `https://api.qrserver.com/v1/create-qr-code/?size=256x256&data=${encodeURIComponent(qr)}`;
                container.style.display = 'block';
                
                const statusText = document.getElementById('whatsapp-status-text');
                if (statusText) statusText.textContent = 'Telefonunuzla QR kodu tarayın';
            }
        }

        hideQRCode() {
            const container = document.getElementById('whatsapp-qr-container');
            if (container) container.style.display = 'none';
        }

        onConnected(info) {
            this.status = 'connected';
            this.hideQRCode();
            this.updateStatusUI({ connected: true, status: 'connected' });
            this.loadChats();
            this.showToast('WhatsApp bağlandı!', 'success');
        }

        onAuthenticated() {
            this.status = 'authenticated';
            this.updateStatusUI({ connected: false, status: 'authenticated' });
        }

        onDisconnected(reason) {
            this.status = 'disconnected';
            this.hideQRCode();
            this.updateStatusUI({ connected: false, status: 'disconnected' });
            this.showToast(`WhatsApp bağlantısı kesildi: ${reason}`, 'warning');
        }

        onAuthFailed(message) {
            this.status = 'auth_failed';
            this.hideQRCode();
            this.updateStatusUI({ connected: false, status: 'auth_failed' });
            this.showToast(`Doğrulama başarısız: ${message}`, 'error');
        }

        onMessageReceived(message) {
            // Add to current chat messages if viewing this chat
            if (this.currentChatId === message.from || this.currentChatId === message.chat?.id) {
                this.appendMessage(message, false);
            }
            
            // Update chat list
            this.updateChatInList(message);
            
            // Show notification if not in current chat
            if (this.currentChatId !== message.from && this.currentChatId !== message.chat?.id) {
                this.showNotification(message);
            }
        }

        onMessageAck(data) {
            // Update message status in UI
            const msgEl = document.querySelector(`[data-message-id="${data.messageId}"]`);
            if (msgEl) {
                const statusEl = msgEl.querySelector('.message-status');
                if (statusEl) {
                    const statusIcons = {
                        1: '✓',      // sent
                        2: '✓✓',     // delivered
                        3: '✓✓'      // read (blue in real app)
                    };
                    statusEl.textContent = statusIcons[data.ack] || '✓';
                    statusEl.className = 'message-status ' + 
                        (data.ack === 3 ? 'read' : data.ack === 2 ? 'delivered' : 'sent');
                }
            }
        }

        updateChatInList(message) {
            // Update chat list with latest message
            const chatList = document.getElementById('whatsapp-chat-list');
            if (!chatList) return;

            let chatEl = chatList.querySelector(`[data-chat-id="${message.chat?.id || message.from}"]`);
            
            if (!chatEl) {
                // Create new chat element
                chatEl = this.createChatElement(message);
                chatList.insertBefore(chatEl, chatList.firstChild);
            } else {
                // Update existing
                chatEl.querySelector('.chat-preview').textContent = message.body || (message.media ? '📎 Medya' : '');
                chatEl.querySelector('.chat-time').textContent = this.formatTime(message.timestamp);
                chatEl.querySelector('.chat-unread').textContent = 
                    (parseInt(chatEl.dataset.unread || 0) + 1).toString();
                chatEl.dataset.unread = chatEl.querySelector('.chat-unread').textContent;
                
                // Move to top
                chatList.insertBefore(chatEl, chatList.firstChild);
            }
        }

        createChatElement(message) {
            const chat = message.chat || {};
            const isGroup = chat.isGroup || false;
            const name = chat.name || message.fromName || message.from;
            
            const el = document.createElement('div');
            el.className = 'chat-item';
            el.dataset.chatId = chat.id || message.from;
            el.dataset.unread = '1';
            el.innerHTML = `
                <div class="chat-avatar">
                    ${isGroup ? '👥' : '👤'}
                </div>
                <div class="chat-info">
                    <div class="chat-name">${this.escapeHtml(name)}</div>
                    <div class="chat-preview">${this.escapeHtml(message.body || (message.media ? '📎 Medya' : ''))}</div>
                </div>
                <div class="chat-meta">
                    <span class="chat-time">${this.formatTime(message.timestamp)}</span>
                    <span class="chat-unread">1</span>
                </div>
            `;
            
            el.addEventListener('click', () => this.openChat(chat.id || message.from, name, isGroup));
            return el;
        }

        async loadStatus() {
            try {
                const response = await fetch('/api/whatsapp/status/', {
                    credentials: 'include'
                });
                const data = await response.json();
                this.updateStatus(data);
                
                if (data.hasQr) {
                    this.loadQRCode();
                } else if (data.connected) {
                    this.loadChats();
                }
            } catch (e) {
                console.error('Failed to load WhatsApp status:', e);
            }
        }

        async loadQRCode() {
            try {
                const response = await fetch('/api/whatsapp/qr/', {
                    credentials: 'include'
                });
                const data = await response.json();
                if (data.qr) {
                    this.showQRCode(data.qr);
                }
            } catch (e) {
                console.error('Failed to load QR code:', e);
            }
        }

        async initialize() {
            try {
                const response = await fetch('/api/whatsapp/init/', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await response.json();
                if (data.success) {
                    this.showToast('WhatsApp başlatılıyor...', 'info');
                    // QR will come via WebSocket or polling
                    setTimeout(() => this.loadQRCode(), 2000);
                } else {
                    this.showToast(data.error || 'Başlatma başarısız', 'error');
                }
            } catch (e) {
                console.error('Failed to initialize WhatsApp:', e);
                this.showToast('Bağlantı hatası', 'error');
            }
        }

        async disconnect() {
            if (!confirm('WhatsApp bağlantısını kesmek istediğinizden emin misiniz?')) return;
            
            try {
                const response = await fetch('/api/whatsapp/disconnect/', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await response.json();
                if (data.success) {
                    this.status = 'not_initialized';
                    this.updateStatusUI({ connected: false, status: 'not_initialized' });
                    this.hideQRCode();
                    this.clearChatList();
                    this.clearMessages();
                    this.showToast('WhatsApp bağlantısı kesildi', 'info');
                }
            } catch (e) {
                console.error('Failed to disconnect:', e);
                this.showToast('Bağlantı hatası', 'error');
            }
        }

        async loadChats() {
            try {
                const response = await fetch('/api/whatsapp/chats/', {
                    credentials: 'include'
                });
                const data = await response.json();
                this.chats = data.chats || [];
                this.renderChatList();
            } catch (e) {
                console.error('Failed to load chats:', e);
            }
        }

        renderChatList() {
            const chatList = document.getElementById('whatsapp-chat-list');
            if (!chatList) return;

            chatList.innerHTML = '';
            
            if (this.chats.length === 0) {
                chatList.innerHTML = '<div class="chat-empty">Henüz sohbet yok</div>';
                return;
            }

            this.chats.forEach(chat => {
                const el = document.createElement('div');
                el.className = 'chat-item' + (chat.unreadCount > 0 ? ' unread' : '');
                el.dataset.chatId = chat.id;
                el.dataset.unread = chat.unreadCount;
                el.innerHTML = `
                    <div class="chat-avatar">
                        ${chat.isGroup ? '👥' : '👤'}
                    </div>
                    <div class="chat-info">
                        <div class="chat-name">${this.escapeHtml(chat.name || 'Bilinmeyen')}</div>
                        <div class="chat-preview">${this.escapeHtml(chat.lastMessage?.body || 'Mesaj yok')}</div>
                    </div>
                    <div class="chat-meta">
                        <span class="chat-time">${chat.lastMessage ? this.formatTime(new Date(chat.lastMessage.timestamp).getTime()) : ''}</span>
                        ${chat.unreadCount > 0 ? `<span class="chat-unread">${chat.unreadCount}</span>` : ''}
                    </div>
                `;
                el.addEventListener('click', () => this.openChat(chat.id, chat.name, chat.isGroup));
                chatList.appendChild(el);
            });
        }

        async openChat(chatId, name, isGroup) {
            this.currentChatId = chatId;
            
            // Update UI
            document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
            const activeEl = document.querySelector(`[data-chat-id="${chatId}"]`);
            if (activeEl) {
                activeEl.classList.add('active');
                activeEl.dataset.unread = '0';
                activeEl.querySelector('.chat-unread')?.remove();
            }

            // Update header
            const headerName = document.getElementById('whatsapp-chat-header-name');
            const headerSubtitle = document.getElementById('whatsapp-chat-header-subtitle');
            if (headerName) headerName.textContent = name;
            if (headerSubtitle) headerSubtitle.textContent = isGroup ? 'Grup' : 'Kişisel';

            // Load messages
            await this.loadMessages(chatId);
        }

        async loadMessages(chatId) {
            try {
                const response = await fetch(`/api/whatsapp/messages/${chatId}/?limit=100`, {
                    credentials: 'include'
                });
                const data = await response.json();
                this.messages = data.messages || [];
                this.renderMessages();
            } catch (e) {
                console.error('Failed to load messages:', e);
            }
        }

        renderMessages() {
            const container = document.getElementById('whatsapp-messages');
            if (!container) return;

            container.innerHTML = '';
            
            this.messages.forEach(msg => this.appendMessage(msg, true));
            
            // Scroll to bottom
            container.scrollTop = container.scrollHeight;
        }

        appendMessage(msg, isHistory) {
            const container = document.getElementById('whatsapp-messages');
            if (!container) return;

            const isOutbound = msg.fromMe === true || msg.direction === 'outbound';
            const time = this.formatTime(msg.timestamp);
            
            const el = document.createElement('div');
            el.className = `message ${isOutbound ? 'outbound' : 'inbound'}`;
            el.dataset.messageId = msg.id;
            
            let content = '';
            if (msg.media && msg.media.data) {
                const mime = msg.media.mimetype || '';
                if (mime.startsWith('image/')) {
                    content = `<img src="data:${mime};base64,${msg.media.data}" alt="${this.escapeHtml(msg.media.filename)}" style="max-width:100%;border-radius:8px;">`;
                } else if (mime.startsWith('video/')) {
                    content = `<video src="data:${mime};base64,${msg.media.data}" controls style="max-width:100%;border-radius:8px;"></video>`;
                } else if (mime.startsWith('audio/')) {
                    content = `<audio src="data:${mime};base64,${msg.media.data}" controls></audio>`;
                } else {
                    content = `<a href="data:${mime};base64,${msg.media.data}" download="${this.escapeHtml(msg.media.filename)}">📎 ${this.escapeHtml(msg.media.filename)}</a>`;
                }
                if (msg.body) content += `<div class="message-caption">${this.escapeHtml(msg.body)}</div>`;
            } else {
                content = `<div class="message-text">${this.escapeHtml(msg.body)}</div>`;
            }
            
            el.innerHTML = `
                ${!isOutbound ? `<div class="message-sender">${this.escapeHtml(msg.fromName || msg.from)}</div>` : ''}
                <div class="message-bubble">
                    ${content}
                </div>
                <div class="message-footer">
                    <span class="message-time">${time}</span>
                    ${isOutbound ? `<span class="message-status" data-status="${msg.status}">${this.getStatusIcon(msg.status)}</span>` : ''}
                </div>
            `;
            
            container.appendChild(el);
            
            if (!isHistory) {
                container.scrollTop = container.scrollHeight;
            }
        }

        getStatusIcon(status) {
            const icons = { pending: '⏳', sent: '✓', delivered: '✓✓', read: '✓✓', failed: '✗' };
            return icons[status] || '';
        }

        async sendMessage() {
            const input = document.getElementById('whatsapp-message-input');
            const mediaInput = document.getElementById('whatsapp-media-input');
            
            if (!input || !this.currentChatId) return;
            
            const message = input.value.trim();
            const mediaFile = mediaInput.files[0];
            
            if (!message && !mediaFile) return;
            
            let media = null;
            if (mediaFile) {
                // Convert to base64
                media = await this.fileToBase64(mediaFile);
                media.mimetype = mediaFile.type;
                media.filename = mediaFile.name;
            }
            
            try {
                const response = await fetch('/api/whatsapp/send/', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        to: this.currentChatId,
                        message: message,
                        media: media
                    })
                });
                
                const data = await response.json();
                if (data.success) {
                    input.value = '';
                    mediaInput.value = '';
                    // Message will appear via WebSocket or we can add optimistically
                } else {
                    this.showToast(data.error || 'Gönderim başarısız', 'error');
                }
            } catch (e) {
                console.error('Failed to send message:', e);
                this.showToast('Gönderim hatası', 'error');
            }
        }

        fileToBase64(file) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve({ data: reader.result.split(',')[1] });
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });
        }

        formatTime(timestamp) {
            const date = new Date(timestamp);
            const now = new Date();
            const diff = now - date;
            
            if (diff < 60000) return 'Az önce';
            if (diff < 3600000) return `${Math.floor(diff / 60000)}dk`;
            if (diff < 86400000) return date.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
            return date.toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit' });
        }

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        showToast(message, type = 'info') {
            if (window.APP && window.APP.toast) {
                window.APP.toast(message, type);
            } else {
                console.log(`[${type}] ${message}`);
            }
        }

        showNotification(message) {
            if (Notification.permission === 'granted') {
                new Notification(message.fromName || message.from, {
                    body: message.body || '📎 Medya',
                    icon: '/static/images/whatsapp-icon.png'
                });
            }
        }

        clearChatList() {
            const chatList = document.getElementById('whatsapp-chat-list');
            if (chatList) chatList.innerHTML = '<div class="chat-empty">WhatsApp bağlı değil</div>';
        }

        clearMessages() {
            const container = document.getElementById('whatsapp-messages');
            if (container) container.innerHTML = '<div class="messages-empty">Sohbet seçin</div>';
        }

        // Chat actions
        async markAsRead(chatId) {
            try {
                await fetch(`/api/whatsapp/chat/${chatId}/read/`, {
                    method: 'POST',
                    credentials: 'include'
                });
            } catch (e) { console.error(e); }
        }

        async archiveChat(chatId, archive = true) {
            try {
                await fetch(`/api/whatsapp/chat/${chatId}/${archive ? 'archive' : 'unarchive'}/`, {
                    method: 'POST',
                    credentials: 'include'
                });
                this.loadChats();
            } catch (e) { console.error(e); }
        }

        async muteChat(chatId, mute = true, duration = -1) {
            try {
                await fetch(`/api/whatsapp/chat/${chatId}/${mute ? 'mute' : 'unmute'}/`, {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ duration })
                });
            } catch (e) { console.error(e); }
        }
    }

    // Export
    window.WhatsAppManager = WhatsAppManager;

    // Auto-initialize on WhatsApp page
    document.addEventListener('DOMContentLoaded', () => {
        const whatsappPage = document.getElementById('whatsapp-page');
        if (whatsappPage) {
            // Get user ID from page data
            const userId = whatsappPage.dataset.userId;
            if (userId) {
                window.whatsapp = new WhatsAppManager();
                window.whatsapp.init(userId);
            }
        }
    });
})();