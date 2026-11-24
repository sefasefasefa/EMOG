# Plan: Son 4 İsteğin Uygulanması

Tarih: 2026-11-20
Durum: Tamamlandı

## Görevler

### Task A: Doğrulama maili rodonsoft.me üzerinden gitsin
- Email backend'i (SMTP) rodonsoft.me üzerinden yapılandırılacak.
- Verification email flow'u e-posta üzerinden sağlanacak.
- Localhost/dev ortamında link olarak `https://rodonsoft.me/...` verilsin (localhost değil).
- Dosyalar: `emog_project/settings.py` (EMAIL_BACKEND/EMAIL_HOST/EMAIL_PORT/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD/EMAIL_USE_TLS/DEFAULT_FROM_EMAIL), `agent_app/auth_views.py` / `agent_app/tokens.py` (verification link oluşturma), `agent_app/templates/agent_app/email_verify.html`.

### Task B: Yaratıcılık / şarkı yazma akışı
- "modele bir şarkı yaz" gibi yaratıcılık gerektiren istekler chat'te `build` intentine düşüp bir task olarak koşulsun veya "creative" akışla yanıt versin.
- `agent_app/views.py` `detect_chat_generation_intent` / intent router'ın "build"/`execute` kararına eklenecek.
- Şarkı/şiir/kod gibi yaratıcı çıktıyı modele gönderebilme.

### Task C: 502 Bad Gateway çözümü
- Backend 8002 portunda çalışmıyor (nginx 127.0.0.1:8002'ye proxy yapıyor ama 8002'de listener yok) → 502.
- `agent_app/views.py` veya app başlatma ile backend'in 8002'de kalıcı çalışması sağlanacak.
- Client-side retry zaten eklendi (`fetchAssistantMessage` 502/503/504/408'de 4 deneme).

### Task D: Username bulma / önerme
- "Bana username bul" -> kullanıcıya username önerme (isim, hobiler, favori renk -> username önerisi).
- Chat akışında yanıt olarak dönecek.

## Global Constraints
- Mevcut dosya yapısı bozulmayacak.
- Yeni DB tablosu yok (mevcut User modeli kullanılıyor).
- İstemci tarafı (chat.html/landing.html)JS değişiklikleri syntax check ile doğrulanacak.

## Review Focus
- 502: backend port 8002 listener'ı var mı?
- Email: gerçek SMTP yapılandırması mı yoksa mock/test only mi?
- Şarkı yazma: LLM'e gönderim yapılıyor mu yoksa sadece template mi?

---
Plan onaylandı, subagent-driven-development ile her bir task sırayla uygulanacak.