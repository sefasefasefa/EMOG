# 3D Kesiklerini Tamamlama Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/plans/2026-11-18-3d-remaining-tasks.md` içindeki 4–7. maddeleri (animasyon/poz, pratik fizik+eylem, timeline/scrub UI, async 3D üretim job/provider akışı) mevcut mimariyle uyumlu şekilde tamamlamak ve öğrenen-model akışlarının tutarlılığını güçlendirmek.

**Architecture:** Mevcut Django app (`agent_app`) içinde genişletme: `entity_system.py` pose/aksiyon/fizik-şema ve pratik simülasyon yardımcıları, `agent_app/scene_generation.py` (yeni) async 3D job+provider arayüzü (`procedural` uçtan uca, harici sağlayıcı hook), `scene_views.py` timeline/aksiyon/job API’leri ve tutarlılık (`stable_id`, `locked_fields`) koruması, `studio.html` gerçek zamanlı scrub/track/keyframe ve job UI, `learned_models.py` bottom-up/öğrenme tutarlılığı güçlendirmesi. 3D üretim işleri `SceneGenerationJob` modeliyle izlenir.

**Tech Stack:** Django 4+/Python 3.11, mevcut Three.js (ESM importmap) Studio UI, SQLite/dev DB, mevcut `neural_core.research` ve `LearnedEntityKnowledge`/`LearnedVideoKnowledge`.

**Spec:** `docs/superpowers/specs/2026-11-18-complete-3d-cuts-design.md`

## Global Constraints
- 3D ve video öğrenen modelleri **ayrı** kalır (`LearnedEntityKnowledge` vs `LearnedVideoKnowledge`); ML ağırlık fine-tune eklenmez, uygulama-içi kalıcı öğrenme korunur.
- 3D sınıf/öğrenme akışlarında internet araştırması tetiklendiğinde her zaman yapılır (`neural_core.research.run(..., enabled=True)`), bottom-up hiyerarşi korunur.
- Kimlik tutarlılığı: `components.identity.stable_id` ve `identity.locked_fields` üretim, düzenleme, aksiyon, timeline uygulama ve job akışlarında korunur/silinmez.
- Harici fizik motoru ve tam skinned-rig bone deformation bu ortamda zorunlu değil; pratik (deterministik, basit) simülasyon + veri modeli + preview/scrub tamamlanır. Harici 3D sağlayıcılar anahtarsız gerçek çıktı üretmeyebilir; async job+provider arayüzü ve `procedural` sağlayıcı uçtan uca çalışır.
- Mevcut davranışları kırma: scene/node/asset/entity-class/learned-knowledge/video endpoint’leri geriye dönük uyumlu kalır.
- Değişiklik sonrası `py_compile` (değişen modüller) ve `manage.py check` geçmeli.

## Review Focus
- **Timeline scrub önizlemesi state bozmasın:** scrub/preview anlık (non-destructive) olmalı; yalnızca açık “uygula/kaydet” kalıcı yazmalı ve `stable_id/locked_fields` korunmalı.
- **Boş/eksik timeline verisi:** `scene.settings.timeline` yoksa veya eski formatta ise varsayılan track/keyframe yapısı üretilmeli, API/UI çökmesin.
- **Async job güvenliği:** kullanıcı yalnızca kendi scene/job’larını görebilmeli; provider/mode doğrulanmalı; başarısız job hata mesajı ve `failed` durumuyla sonlanmalı.
- **Öğrenme tetikleri:** sınıf oluşturma/araştırma/instantiate ve başarılı video üretimi öğrenen modellere kayıt yazmalı (ağ yoksa da akış hataya düşmeden degrade etmeli).
- **Eski node/component verisi:** eksik `animation/physics/timeline` alanları normalize edilmeli; UI ham JSON parse hatalarını kullanıcıya göstermeli.

---

### Task 1: Model ve Veri/Simülasyon Temelleri (4,5,6,7 altyapısı)

**Files:**
- Modify: `agent_app/models.py`
- Create: `agent_app/migrations/0017_scenegenerationjob.py` (oluşturulacak)
- Modify: `agent_app/entity_system.py`
- Test/Verify: migration üretimi/uygulanması, `py_compile`, `manage.py check`

**Interfaces:**
- Consumes: mevcut `Scene`, `SceneAsset`, `SceneNode`, `EntityClass`, `User`, `ACTION_TEMPLATES`, `apply_action_to_components`.
- Produces: `SceneGenerationJob` modeli; zengin `ACTION_TEMPLATES`/`POSE_PRESETS`; `normalize_physics`, `normalize_animation`, `default_timeline`, `normalize_timeline`, `timeline_state_at`, `step_physics`, `apply_timeline_state`.

- [ ] **Step 1: `SceneGenerationJob` modelini `agent_app/models.py` içine ekle**
  - `id` UUID PK, `user` FK(User,null/blank,related_name="scene_generation_jobs"), `scene` FK(Scene,null/blank), `entity_class` FK(EntityClass,null/blank)
  - `provider` CharField(32, default="procedural"), `mode` CharField(32, choices procedural/text_to_3d/image_to_3d, default="procedural")
  - `prompt` TextField(blank), `input_path` CharField(500, blank), `status` CharField(16, choices pending/queued/running/completed/failed, default="pending"), `progress` PositiveSmallIntegerField(default=0)
  - `output_asset` FK(SceneAsset,null/blank), `output_node` FK(SceneNode,null/blank), `parameters` JSONField(default=dict), `result` JSONField(default=dict), `error` TextField(blank)
  - `consistency` JSONField(default=dict), `created_at/started_at/finished_at/updated_at`, indexes (user,-created_at),(status,-created_at)

- [ ] **Step 2: Migration oluştur ve uygula**
  Run: `.\.venv\Scripts\python.exe manage.py makemigrations agent_app -n scenegenerationjob`
  Run: `.\.venv\Scripts\python.exe manage.py migrate`
  Expected: migration oluşturulur ve uygulanır.

- [ ] **Step 3: Animasyon/fizik/timeline yardımcılarını `entity_system.py`’ye ekle**
  - `ACTION_TEMPLATES` genişlet (mevcutları koru): `idle, walk, run, raise_arm, wave, jump, swing_sword, crouch, turn_left, turn_right` (label,type,targets,pose_delta,reset_pose?,physics_impulse?,duration?)
  - `POSE_PRESETS` ve `get_pose_preset(name)` ekle.
  - `normalize_physics(phys)` (enabled,body,mass,collider,constraints,gravity,damping,velocity,angular_velocity,last_impulse)
  - `normalize_animation(anim)` (clips,active,active_action,pose,keyframes)
  - `default_timeline(fps=30,duration=10.0)`, `normalize_timeline(tl)`, `timeline_state_at(timeline,t)` (basit interpolasyon/nearest), `step_physics(transform,physics,dt)` (yerçekimi+damping+impulse, y>=0 clamp), `apply_timeline_state(components,state,preview=False)`.

- [ ] **Step 4: `default_components_for_class` ve `apply_action_to_components` güncelle**
  - Varsayılan animation/physics normalize edilsin; aksiyon uygulaması pose/impulse/duration ve active_action alanlarını tutarlı yazsın, identity’yi bozmasın.

- [ ] **Step 5: Doğrula**
  Run: `.\.venv\Scripts\python.exe -m py_compile agent_app/models.py agent_app/entity_system.py`
  Run: `.\.venv\Scripts\python.exe manage.py check`

---

### Task 2: Async 3D Üretim Job + Provider Akışı (7)

**Files:**
- Create: `agent_app/scene_generation.py`
- Modify: `agent_app/entity_system.py` (yalnızca gerekirse)
- Test/Verify: `py_compile`, `manage.py check`

**Interfaces:**
- Consumes: `SceneGenerationJob`, `Scene`, `EntityClass`, `User`, `instantiate_entity_class`.
- Produces: `get_provider_info()`, `create_generation_job(...)`, `run_generation_job(job_id, background=False)`, `run_generation_job_background(job_id)`.

- [ ] **Step 1: `agent_app/scene_generation.py` oluştur**
  - Procedural provider her zaman available (entity_class/parametrelerle instantiate veya basit üretim); harici `external_text_to_3d`/`external_image_to_3d` hook’lar available False ve yapılandırma yoksa açıklayıcı failed.
  - Job lifecycle: pending/queued->running->completed|failed, progress/result/error/output_asset/output_node, consistency snapshot korunur.
  - `run_generation_job_background` daemon thread ile `_run_impl` çağırır.

- [ ] **Step 2: Doğrula**
  Run: `.\.venv\Scripts\python.exe -m py_compile agent_app/scene_generation.py agent_app/entity_system.py`

---

### Task 3: API/Uç Noktalar (4,5,6,7)

**Files:**
- Modify: `agent_app/scene_views.py`
- Modify: `agent_app/urls.py`
- Test/Verify: `py_compile`, `manage.py check`

**Interfaces:**
- Consumes: `entity_system` normalize/timeline/step yardımcıları, `scene_generation` ve `SceneGenerationJob`.
- Produces: normalize timeline GET/PUT, timeline state (preview) endpoint, zengin aksiyon yanıtı, 3D provider+job CRUD/run endpoint’leri.

- [ ] **Step 1: Timeline ve bileşen normalizasyonu**
  - `scene_timeline_api` GET/PUT `normalize_timeline` kullanır, geriye dönük uyumlu.
  - Yeni timeline state endpoint’i (örn. `POST/GET /api/scenes/<id>/timeline/state/`) `t` için preview state döndürür, DB yazmaz.
  - Node serileştirme/aksiyon yanıtlarında physics/animation normalize edilir.

- [ ] **Step 2: 3D generation provider ve job endpoint’leri**
  - `generation_providers_api` -> `scene_generation.get_provider_info()`.
  - Job endpoint’leri: oluştur/liste/detay/çalıştır; scene/job sahiplik kontrolü; procedural job uçtan uca çalışır.
  - URL’ler: `/api/3d-jobs/`, `/api/3d-jobs/<job_id>/`, `/api/3d-jobs/<job_id>/run/`.

- [ ] **Step 3: Doğrula**
  Run: `.\.venv\Scripts\python.exe -m py_compile agent_app/scene_views.py agent_app/urls.py agent_app/scene_generation.py`
  Run: `.\.venv\Scripts\python.exe manage.py check`

---

### Task 4: Studio UI — Scrub/Track, Animasyon-Fizik, Job (4,5,6,7)

**Files:**
- Modify: `agent_app/templates/agent_app/studio.html`

- [ ] **Step 1: Timeline UI’ını scrub/track tabanlı yap**
  - Range slider (scrub), zaman/fps/duration, play/pause/step, keyframe/track listesi ve düzenleme.
  - Scrub/oynatma preview’ı non-destructive (state endpoint veya client-side hesap) uygulasın; “Uygula & Kaydet” kalıcı yazsın. Mevcut `playTimelinePreview` davranışını preview tabanlıya dönüştür.

- [ ] **Step 2: Animasyon/pose ve fizik kontrollerini geliştir**
  - Genişletilmiş aksiyon/preset listesi ve pose uygulama; animation/physics alanlarıyla uyumlu kaydet/uygula.

- [ ] **Step 3: 3D üretim job UI**
  - Provider/job paneli: provider seçimi, mode/prompt/class/scene, job oluştur/çalıştır, durum/progress ve çıktı gösterimi.

- [ ] **Step 4: Dayanıklılık ve UX**
  - JSON parse ve boş veri hatalarında çökme yok, kullanıcıya mesaj.

---

### Task 5: Öğrenen Model Tutarlılığı ve Güçlendirme

**Files:**
- Modify: `agent_app/learned_models.py`
- Modify: `agent_app/scene_views.py` (gerekirse)

- [ ] **Step 1: Bottom-up fundamental araştırmayı güçlendir**
  - Katman sorguları/prompt’ları kavrama uygun “en temel” alt bileşenleri (biyolojik: kan/hücre/doku temeli; cansız: malzeme/element/alt parça) daha açık isteyecek şekilde güncelle; her adım öğrenen modele yazılmaya devam etsin.

- [ ] **Step 2: Tetikleyici tutarlılığı**
  - Sınıf create/research/instantiate akışlarında eksik spec veya istek halinde araştırma+öğrenme ve uygulama garanti olsun.

- [ ] **Step 3: Doğrula**
  Run: `.\.venv\Scripts\python.exe -m py_compile agent_app/learned_models.py agent_app/scene_views.py agent_app/video_generation.py`

---

### Task 6: Doğrulama, Tasklist ve Rapor

**Files:**
- Modify: `docs/superpowers/plans/2026-11-18-3d-remaining-tasks.md`

- [ ] **Step 1: Tam doğrulama**
  Run: `.\.venv\Scripts\python.exe -m py_compile agent_app/models.py agent_app/entity_system.py agent_app/scene_generation.py agent_app/scene_views.py agent_app/urls.py agent_app/learned_models.py agent_app/video_generation.py`
  Run: `.\.venv\Scripts\python.exe manage.py check`
  Expected: 0 issue.

- [ ] **Step 2: Smoke kontrol (mümkünse)**
  Django test client/shell ile timeline normalize/state, procedural 3D job create-run, consistency korunumu.

- [ ] **Step 3: Tasklist güncelle**
  4–7’yi `[x]` yap; notlara pratik simülasyon/scrub/async job (procedural) tamamlandığını, harici fizik motoru/skinned-rig ve harici 3D API anahtarlarının ortam kısıtı olarak kaldığını yaz.

- [ ] **Step 4: Kullanıcıya raporla**
  Tamamlananlar, doğrulama sonucu ve kalan ortam kısıtlarını netleştir.
