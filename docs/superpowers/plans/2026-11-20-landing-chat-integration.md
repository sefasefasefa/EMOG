# Plan: Landing sayfasından Chat'e doğrudan geçiş + Mesajı ile beraber gönderme

Tarih: 2026-11-20
Durum: Uygulama devam ediyor

## Kullanıcı İstekleri
1. Kullanıcı `rodonsoft.me/` (landing / anasayfa) açtığında, zaten logged-in (giriş yapmış) ise **direkt chat ekranına** (`/chat/`) yeni bir sohbet olarak yönlendirilsin.
2. Landing sayfasında bir mesaj yazıp gönderdiğinde, bu mesaj **chat ekranına birlikte gitmeli** (chat sayfası açıldığında otomatik olarak gönderilsin / pending olarak kaydedilsin ve chat'de görünsün).

## Global Constraints
- Yeni veritabanı tablosu yok (mevcut `pending_chat_message` session anahtarı ve `Conversation`/`Node` yapısı kullanılır).
- `landing.html` ve `chat.html` template'leri düzenlenir.
- Giriş yapmamış kullanıcı landing'de kalır, giriş yapılmışsa direkt chat'e geçer.
- İstemci JS değişiklikleri syntax check ile doğrulanacak.

## Görevler

### Task 1: Landing sayfası — authenticated kullanıcıyı direkt `/chat/` ye yönlendir
**Files:**
- Modify: `agent_app/templates/agent_app/landing.html`

**Changes:**
- `landing.html`'deki `<script>` bölümüne (veya `AUTHENTICATED` değişkeniTrue olduğunda) sayfa yüklendiğinde `window.location.href = '/chat/'` yönlendirmesi eklensin.
- Sadece authenticated kullanıcılar için (AUTHENTICATED true).
- Yönlendirme anında mevcut chat session'ı bozulmasın (yeni sohbet olarak açılsın — chat.html'deki mevcut davranış).

**Verify:**
- `python manage.py check` geçsin.
- Tarayıcı/Giriş yapmış kullanıcı `/` çağrığında `/chat/` ye yönlendirilir.

### Task 2: Landing'de mesaj gönderildiğinde pending olarak kaydet + chat'de auto-send
**Files:**
- Modify: `agent_app/templates/agent_app/landing.html` (sendMessage içinde `sessionStorage.setItem('pending_chat_message', txt)` var — bu doğru, ama chat'te okunup uygulanmalı)
- Modify: `agent_app/templates/agent_app/chat.html` (sayfa yüklendiğinde `sessionStorage`'daki `pending_chat_message` varsa onu otomatik olarak gönder / chat'e ekle)

**Changes:**
- `landing.html`'deki `sendMessage` zaten `sessionStorage.setItem('pending_chat_message', txt)` yapıyor (line 606) — authenticate gerektiren akışta `/chat/` ye redirect yapıyor; bu `sessionStorage` survives redirect.
- `chat.html`'de sayfa başlangıcında (`DOMContentLoaded` veya IIFE içinde) `sessionStorage.getItem('pending_chat_message')` kontrol edilir; varsa bu mesaj otomatik olarak chat'e gönderilir (`sendMessage()` eşdeğeri) ve sessionStorage'dan temizlenir.

**Verify:**
- `python manage.py check` geçsin.
- Giriş yapmış kullanıcı landing'de mesaj yazıp gönderdiğinde `/chat/` ye geçtiğinde mesaj chat'te görünür.

## Review Focus
- Giriş yapmamış kullanıcı landing'de kalır (yönlendirilmez).
- Pending mesaj sadece bir kez gönderilir (sessionStorage'dan temizlenir).
- Chat'e gelen pending mesaj, normal kullanıcı mesajı gibi görünür (rol 'me').

---
Plan onaylandı; subagent-driven-development ile görevler sırayla uygulanacak.