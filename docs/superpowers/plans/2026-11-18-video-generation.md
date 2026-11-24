# Video Üretimi ve AI Düzenleme — Uygulama Planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EMOG'a prompt'tan video üretme ve yüklenen dosyaları AI ile düzenleme yeteneği eklemek.

**Architecture:** `agent_app/video_generation.py` servis modülü; `VideoJob` modeli; API uçları; `/video/` sayfası. Sağlayıcı soyutlaması (OpenAI Sora, Stability, Replicate, NVIDIA) + yerel slideshow fallback (PIL + imageio-ffmpeg). Dosya yükleme + AI düzenleme (ffmpeg). Uzun işler arka plan thread'inde.

**Tech Stack:** Django 5.2, SQLite, imageio-ffmpeg, PIL, ffmpeg (imageio-ffmpeg bundle), gTTS/pyttsx3 (opsiyonel), OpenAI client.

**Spec:** `docs/superpowers/specs/2026-11-18-video-generation-design.md`

## Global Constraints

- Ayrı proje/mikroservis yok; her şey `agent_app` içinde.
- Tüm API'ler kimlik doğrulaması ister; kullanıcı yalnızca kendi işlerini görür.
- Çıktı yolu `media/videos/` ve `media/uploads/` altında; traversal reddedilir.
- LLM/sağlayıcı yoksa yerel üretici çalışır.
- Testler ağ çağrısı yapmadan çalışır (OFFLINE benzeri ayar).
- Maksimum süre 300 sn; maksimum dosya boyutu 100MB.

## Review Focus

- Traversal yolu → reddedilmeli.
- Onaysız dosya yazma → yan etki yok.
- Sağlayıcı yok → yerel fallback.
- Uzun iş → arka plan, HTTP timeout yok.
- Kullanıcı izolasyonu → başkasının işi 404.

---

### Task 1: Bağımlılıklar ve settings

**Files:**
- Modify: `requirements.txt`
- Modify: `emog_project/settings.py` (VIDEO_GENERATION bloğu, MEDIA ayarları)

- [ ] imageio, imageio-ffmpeg, gtts, pyttsx3 ekle (opsiyonel).
- [ ] VIDEO_GENERATION ayar bloğu (max duration, max upload, providers, resolution).
- [ ] `manage.py check` temiz.

### Task 2: VideoJob modeli + migration

**Files:**
- Modify: `agent_app/neural_core/models.py`? No — `agent_app/models.py` (VideoJob)
- Actually create in `agent_app/models.py` (VideoJob)
- Migration

**Interfaces:**
- `VideoJob(id, user, prompt, mode, status, progress, output_path, error, created_at, ...)`

- [ ] Model + migration.
- [ ] Test.

### Task 3: video_generation servis modülü

**Files:**
- Create: `agent_app/video_generation.py`

**Interfaces:**
- `generate_video(prompt, user, duration=10, resolution=(1920,1080), fmt="mp4") -> VideoJob` (async, returns job)
- `run_generation(job_id)` — arka plan
- `edit_video(job_id, instruction)` — AI düzenleme
- Providers: openai, stability, replicate, nvidia, local

- [ ] Sağlayıcı soyutlaması.
- [ ] Yerel slideshow fallback (PIL + imageio).
- [ ] TTS mux (opsiyonel).
- [ ] Subtitle render.
- [ ] AI düzenleme (ffmpeg komutları LLM'den).
- [ ] Testler (offline).

### Task 4: API + URL + feature flag

**Files:**
- Modify: `agent_app/urls.py`
- Modify: `audit/features.py`
- Create: views in `agent_app/views.py` or `agent_app/video_views.py`

- [ ] Endpoints.
- [ ] Feature flag `video_generation`.
- [ ] Testler.

### Task 5: /video/ sayfası

**Files:**
- Create: `agent_app/templates/agent_app/video.html`
- Modify: `base.html` (menü)

- [ ] Prompt formu, dosya yükleme, durum poll, önizleme/indirme, düzenleme isteği.

### Task 6: Testler ve doğrulama

- [ ] Unit + API testleri.
- [ ] `manage.py test` geçer.

---

Plan tamamlandı. Uygulama yöntemi seçilsin.