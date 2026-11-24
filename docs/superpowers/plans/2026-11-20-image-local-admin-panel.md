# Plan: Görsel Anlama (Local/PIL, API'sız) + Admin Paneli (Aç/Kapa, Bakım, Dashboard)

Tarih: 2026-11-20
Durum: Uygulama devam ediyor

## Kullanıcı İstekleri
1. Görsel anlama: **dış vision API olmadan** (NVIDIA vision modeli etkin değil / sunucuya yük bağlamamak için) Pillow (PIL) ile yerel analiz; normal API akışı devam etsin, sunucuya yük bindirmesin.
2. Admin paneli: uygulama içindeki tüm özellikleri aç/kapa, bakım moduna al, detaylı dashboard (istatistikler, kullanıcılar, son görevler, sistem sağlığı).

## Global Constraints
- Yeni veritabanı tablosu: izin verilir (FeatureToggle, SystemSetting, AdminLog) — mevcut User modeli kullanılır.
- Görsel anlama: dış vision API çağrısı **iştigele / pasifleştirilir**, PIL fallback tek kaynak kalır.
- Admin paneli: `/admin/` veya `/emog-admin/` rotası, sadece superuser/yetkili erişebilir.
- Bakım modu: aktifse sadece yetkililer erişebilir, diğerleri "bakımda" sayfası görebilir.
- İstemci JS değişiklikleri syntax check ile doğrulanacak.
- Mevcut dosya yapısı bozulmayacak.

## Görevler

### Task 1: Görsel anlama — local-only, dış vision API pasif
- `agent_app/chat_media.py`: `maybe_handle_chat_vision_analysis` içinde vision LLM çağrısı bir **feature flag** (`VISION_LLM_ENABLED`) ile kapatılsın; dış API çağrısı yapılmasın; PIL analiz tek kaynak kalır.
- `emog_project/settings.py`: `VISION_LLM_ENABLED = False` default (env ile açılebilir).
- PIL analiz çıktısı zaten mevcut (Task 1 önceki plan) — şimdi flag'e bağla.

### Task 2: Admin modelleri
- `agent_app/models.py` (veya yeni `agent_app/admin_models.py`): `FeatureToggle` (name, is_active, description), `SystemSetting` (key, value, description), `AdminAuditLog` (admin_user, action, target, timestamp).
- Migration yoksa modelleri sadece ekle (makemigrations gerekirse not al).

### Task 3: Admin görünümleri (views)
- `agent_app/admin_views.py` (veya mevcut): `admin_dashboard` (istatistikler: toplam kullanıcı, sohbet, video/görsel görevi, aktif toggle'lar), `admin_feature_toggles` (liste + aç/kapa POST), `admin_toggle_maintenance` (bakım modu aç/kapa), `admin_system_logs`.
- Yetkilendirme: sadece `is_staff` / superuser.

### Task 4: Admin şablonları (HTML)
- `agent_app/templates/agent_app/admin_dashboard.html`: kartlar (istatistikler), toggle listesi, bakım durumu, son loglar.
- Base template'e admin menü bağlantısı (sadece yetkili görünür).

### Task 5: Middleware — bakım modu + feature flag koruma
- `agent_app/middleware.py` (veya mevcut middleware): istek geldiğinde bakım modu aktifse (ve kullanıcı yetkili değilse) `/admin/` hariç tüm yere "Sistem bakımda" sayfası.
- Feature toggle kapalıysa ilgili endpoint (ör. video üretimi) 403/404 dönsün.

### Task 6: Test + commit
- `manage.py check`, mevcut testler (tests_chat_media vb.) geçsin.
- Yeni modeller/views/shablonlar için temel test (admin erişimi, toggle aç/kapa).
- Commit: `feat: admin paneli + local-only görsel analizi`.

## Review Focus
- Görsel anlama dış API'ye çağrı atıyor muy? (olmamalı)
- Admin paneli yetkisiz erişime açık mı? (olmamalı)
- Bakım modu yetkiliyi engellemiyor mu? (engellemeli)
- Tüm değişiklikler tek commit mi? (plan'a uygun)

---
Plan onaylandı; subagent-driven-development ile görevler sırayla uygulanacak.