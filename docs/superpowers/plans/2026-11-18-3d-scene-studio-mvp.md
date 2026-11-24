# 3D Sahne Stüdyosu MVP — Uygulama Planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Obje/sahne/iskelet veri modeli ve Three.js tabanlı 3D glTF görüntüleyici (MVP).

**Architecture:** Django modelleri (Scene, SceneAsset, SceneNode) + JSON bileşen şeması; REST API; `/studio/` sayfası ve Three.js (CDN) ile glTF görüntüleyici. Medya: `media/scenes/`.

**Tech Stack:** Django 5.2, SQLite, Three.js (CDN), vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-18-3d-scene-studio-mvp-design.md`

## Global Constraints

- Ayrı proje/mikroservis yok; `agent_app` içinde.
- Tüm API'ler auth ister; kullanıcı izolasyonu.
- Dosya yolları `media/scenes/` altında; traversal reddi.
- glTF/glb yükleme boyut sınırı.

## Tasks

### Task 1: Modeller ve migration
**Files:** `agent_app/models.py`, migration
- [ ] Scene, SceneAsset, SceneNode modelleri + migration + admin kaydı (opsiyonel)
- [ ] Testler, migrate, check

### Task 2: Scene/Asset/Node API ve URL'ler
**Files:** `agent_app/scene_views.py` (veya views), `agent_app/urls.py`
- [ ] CRUD/list/upload endpoints, auth + ownership
- [ ] Feature flag `scene_studio` (opsiyonel)

### Task 3: /studio/ sayfası ve Three.js görüntüleyici
**Files:** `agent_app/templates/agent_app/studio.html`, `agent_app/views.py`/page view, `agent_app/urls.py`, `base.html` (erişim linki)
- [ ] Sahne listesi/oluştur, glTF yükle, Three.js + OrbitControls + GLTFLoader ile görüntüleme
- [ ] Mobil uyumlu temel düzen

### Task 4: Testler ve doğrulama
- [ ] Model/API testleri, `manage.py test` geçer

---