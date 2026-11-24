# Studio'da Üretim Merkezi + Chat'te Sonuç Gösterimi: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Üretilen video/görüntüler tek bir yerde (Studio) üretilsin, sonuçlar hem Studio medya listelerinde hem de normal chat ekranında görünür olsun; chat'ten gelen video üretme talep studio ile üretilsin ve studio başka bir uygulama olarak ele alınabilir.

**Architecture:** Üretim zaten studio iş tablosu (VideoJob/ImageJob) üzerinden yapılıyor; chat bir tetikleyici/çerçeve kısmı olsun. Değişiklikler: studio medya tabı aktifken canlı yenileme, chat bubble kartlarına studio linki, chat dosya yükleme accept niteliği genişletme.

**Tech Stack:** Django (views), HTML/JS template (agent_app/templates/agent_app/studio.html, chat.html), arka uç (agent_app/chat_media.py).

**Spec:** docs/superpowers/specs/2026-11-19-studio-chat-media-visibility-design.md

## Global Constraints

- Yeni veritabanı tablosu veya çift üretim sistemi kurgulanmayacak — mevcut `VideoJob`/`ImageJob` (studio liste kaynağı) tek kaynak; chat içinde ayrı üretim alanı yaratılmayacak.

## Review Focus

- Chat video/görsel üretme talep: üretim fonksiyonları studio'nun (`video_generation._generate_video_impl` / `image_generation._generate_image_impl`) aynısı korunmalı (chat_media `_run_video_job`/`_run_image_job_sync` üzerinden).
- Chat'te çoklu format + 3D yükleme: 3D yerleştirme akışı mevcut pending-action sistemiyle çalışmaya devam etmeli.
- Studio tab geçişleri: media aktifken poller başlatılır, 3d aktifken durdurulur (Task 1).
- Chat bubble: video kartında studio linki mevcut, image kartına eklenecek (Task 3).
- Studio medya listesi: chat ile üretilen işler, studio medya tabı aktifken görünür olmalı (Task 1).

---

### Task 1: Studio medya tabı: tab aktifken işleri canlı yenile (poller)

**Files:**
- Modify: `agent_app/templates/agent_app/studio.html`

**Interfaces:**
- Produces: `mediaState.mediaPollTimer`; `startMediaJobsPollLoop()` (interval: `loadVideoJobs()` + `loadImageJobs()`, 4000ms); `stopMediaJobsPollLoop()`; `activate()` media aktifken başlatır, 3d aktifken durdurur.

- [ ] **Step 1:** `mediaState` tanımına `mediaPollTimer: null` alanını ekle (studio.html ~1366-1374, yanıt alanları kısmı).
- [ ] **Step 2:** `activate()`'ta — `isMedia` aktifken `startMediaJobsPollLoop()`; değilse `stopMediaJobsPollLoop()` çağır.
- [ ] **Step 3:** `loadMediaJobsOnce()` altına `startMediaJobsPollLoop` / `stopMediaJobsPollLoop` fonksiyonlarını ekle.

- [ ] **Step 4 (doğrulama):** Tarayıcı: studio açık, media tabı aktifken network: `/api/video/` ve `/api/image/` tekrarlanır (~4s aralık); 3D tabına geçince tekrar dura.

---

### Task 2: chat_media: video/image attachment'larına studio_url + unit test

**Files:**
- Modify: `agent_app/chat_media.py`
- Modify: `agent_app/tests_chat_media.py`

**Interfaces:**
- Produces: `_job_attachment(...)` video/image attachment'larında `studio_url: "/studio/?tab=media"`.
- Produces: `tests_chat_media.JobAttachmentStudioUrlTests`.

- [ ] **Step 1:** `_job_attachment` fonksiyonuna (chat_media.py) sonuna, `job_kind in ("video", "image")` için `att["studio_url"] = "/studio/?tab=media"` ekle.
- [ ] **Step 2:** `tests_chat_media.py`'ye `JobAttachmentStudioUrlTests` test sınıfını ekle: `_job_attachment("video", VideoJob...)` ve `_job_attachment("image", ImageJob...)` sonuçlarının `studio_url == "/studio/?tab=media"` olduğunu assert et.
- [ ] **Step 3 (doğrulama):** `python manage.py test agent_app.tests_chat_media.JobAttachmentStudioUrlTests` — PASS.

---

### Task 3: chat.html — dosya yükleme input'u (çoklu format + 3D) + image kartına studio linki

**Files:**
- Modify: `agent_app/templates/agent_app/chat.html`

**Interfaces:**
- Produces: `#chat-attach-input` accept niteliği; `renderBubbleAttachmentsHtml` image branch'ine `${studioLink}` eklentisi.

- [ ] **Step 1:** `<input type="file" id="chat-attach-input" multiple>`'a `accept="image/*,video/*,audio/*,.txt,.md,.csv,.json,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.glb,.gltf,.obj,.fbx,.stl,.zip"` niteliğini ekle (ALLOWED_EXTENSIONS, chat_media.py:33-40 ile eşleşir).
- [ ] **Step 2:** `renderBubbleAttachmentsHtml` image branch'ine (satır ~1489) `${studioLink}`'i ekle (video kartında zaten var, 1492).
- [ ] **Step 3 (doğrulama):** Tarayıcı: chat'te video üretimi sonucunda kartın "Studio" linki olur; image sonucunda kartta studio linki görünür; 3D yükleme (🧊 kartı, `inferAttachmentKind` 1454) akışı bozulmaz.

---

### Task 4: Test çalıştırma + commit

**Files:** (Task 2)

- [ ] **Step 1 (doğrulama):** `python manage.py test agent_app.tests_chat_media agent_app.tests_video_job agent_app.tests_unified_routes` — PASS.
- [ ] **Step 2:** `git add agent_app/templates/agent_app/studio.html agent_app/templates/agent_app/chat.html agent_app/chat_media.py agent_app/tests_chat_media.py`
- [ ] **Step 3:** `git commit -m "feat: studio'da üretilen medyayı chat'te göster + studio poller + 3D dosya upload accept"`
