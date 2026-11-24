# NEURAL CORE — Minari Motorları Ekleme Planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Minari mimarisindeki eksik motorları (Reasoning, Learning, Binary Control, paylaşımlı hafıza) EMOG içindeki `agent_app/neural_core` paketine eklemek ve bilişsel çekirdek akışına entegre etmek.

**Architecture:** Her motor ayrı bir modül (`reasoning.py`, `learning.py`, `binary_control.py`) olarak `neural_core` paketine eklenecek; paylaşımlı hafıza `NeuralExperience.shared` alanı ile temsil edilecek. `CognitiveKernel.run` akışı sırasıyla: plan → akıl yürütme (reasoning) → keşif → araştırma → değerlendirme → öğrenme (learning) → çıktı. Binary Control Core, `ToolRegistry`'yi izin/onay politikalarıyla saran bir cephe (facade) olacak. LLM yoksa tüm motorlar deterministik şablonla çalışacak.

**Tech Stack:** Django 5.2, SQLite, Django ORM, mevcut `buildkit.search`/`buildkit.llm`, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-15-neural-core-design.md` + kullanıcı minarisi (bu sohbet).

## Global Constraints

- Ayrı proje/mikroservis yok; her şey `agent_app` içinde.
- Dosya araçları yalnızca `projects/` ve `task_runs/` altında; yol kaçışı reddedilir.
- Yazma ve komut çalıştırma onaysız yürütülmez.
- LLM yoksa/çevrimdışıysa tüm motorlar deterministik şablonlarla çalışır.
- Kendini geliştirme yalnızca güvenli anahtarlar üzerinde önerir.
- Tüm API'ler kimlik doğrulaması ister; kullanıcı yalnızca kendi kayıtlarını görür.
- Testler `NEURAL_CORE["OFFLINE"]=True` ile ağ çağrısı yapmadan çalışır.

## Review Focus

- Boş/çok uzun hedef: çekirdek çökmemeli.
- LLM sağlayıcısı yok/hata: çalışma tamamlanmalı (çevrimdışı).
- Paylaşımlı hafıza: başka kullanıcıların verisi sızdırılmamalı; yalnızca `shared=True` kayıtlar görülebilmeli.
- Binary Control: onaysız yazma/komut hiçbir yan etki oluşturmamalı.
- Reasoning: hipotez listesi boşsa akış çökmemeli.

---

### Task 1: Paylaşımlı hafıza alanı (model + migration)

**Files:**
- Modify: `agent_app/neural_core/models.py` (`NeuralExperience`'a `shared` alanı)
- Modify: `agent_app/neural_core/experience.py` (`record_shared`, `shared_recent`, `shared_lessons` fonksiyonları)
- Migration: `makemigrations agent_app`

**Interfaces:**
- Consumes: mevcut `experience.record`, `experience.recent`.
- Produces: `experience.record_shared(user, goal, outcome, score, lesson, context, run=None) -> NeuralExperience`; `experience.shared_recent(limit=20) -> list[NeuralExperience]`; `experience.shared_lessons(limit=10) -> list[str]`.

- [ ] `NeuralExperience`'a `shared = models.BooleanField(default=False, db_index=True)` ekle.
- [ ] `record_shared` → `shared=True` ile kayıt oluşturur.
- [ ] `shared_recent` → `shared=True` kayıtları döner (kullanıcı filtresi yok; tüm paylaşılan).
- [ ] `shared_lessons` → paylaşılan dersleri döner.
- [ ] `makemigrations` + `migrate`; testler geçer.

### Task 2: Reasoning Engine

**Files:**
- Create: `agent_app/neural_core/reasoning.py`
- Test: `agent_app/tests_neural_core.py` (ek testler)

**Interfaces:**
- Produces: `ReasoningEngine(user, gateway=None)`; `ReasoningEngine.reason(goal, mode, context, plan) -> dict` (`hypotheses: list[dict]`, `trace: list[dict]`, `conclusion: str`, `confidence: float`); `reasoning.generate_hypotheses(goal, gateway) -> list[dict]` (`statement`, `evidence`, `confidence`).

- [ ] LLM varsa JSON hipotez listesi iste; başarısızsa deterministik hipotezler.
- [ ] `trace` en az 3 adımdan oluşmalı (analiz → hipotez → sonuç).
- [ ] Hipotez listesi boş olamaz (en az 1 varsayılan).
- [ ] Çevrimdışı modda ağ çağrısı yok.

### Task 3: Learning Engine

**Files:**
- Create: `agent_app/neural_core/learning.py`
- Test: `agent_app/tests_neural_core.py`

**Interfaces:**
- Produces: `LearningEngine(user, gateway=None)`; `LearningEngine.learn(run, score, context=None) -> dict` (`lessons: list[str]`, `preference_updates: dict`, `proposals: list`, `shared_recorded: bool`).

- [ ] Çalıştırmadan en az 1 ders çıkarır.
- [ ] Düşük puan → `planner.depth=deep` önerisi (evolution).
- [ ] Başarılı çalıştırma → paylaşımlı hafızaya kayıt (shared=True).
- [ ] Tercih güncellemeleri `preferences.set_value` ile yazılır.

### Task 4: Binary Control Core

**Files:**
- Create: `agent_app/neural_core/binary_control.py`
- Test: `agent_app/tests_neural_core.py`

**Interfaces:**
- Produces: `BinaryControlCore(user)`; `list_tools()`, `read_file(path)`, `request_write(path, content)`, `request_command(command, cwd=".")`, `decide(approval_id, approve)` (ToolRegistry'ye delegasyon); `permission_summary() -> dict`.

- [ ] `ToolRegistry`'yi saran cephe; aynı güvenlik sınırları.
- [ ] `permission_summary` onay zorunluluğu ve allowlist'i döner.
- [ ] Onaysız yazma hiçbir yan etki oluşturmaz.

### Task 5: Discovery genişletme (hipotez, teknoloji birleştirme, deney)

**Files:**
- Modify: `agent_app/neural_core/discovery.py`

**Interfaces:**
- `discovery.generate` mevcut imzası korunur.
- Ekle: `discovery.hypotheses(goal, gateway, max_items=3) -> list[dict]` (`statement`, `confidence`); `discovery.merge_technologies(candidates) -> list[dict]` (`name`, `combined`); `discovery.experiment_plan(candidate) -> dict` (`steps`, `success_criteria`).

- [ ] Hipotez listesi boş olamaz.
- [ ] Teknoloji birleştirme en az 1 aday döner.
- [ ] Deney planı `steps` ve `success_criteria` içerir.

### Task 6: Kernel entegrasyonu

**Files:**
- Modify: `agent_app/neural_core/kernel.py`

- [ ] `run` akışı: plan → reasoning → discovery → hypotheses → research → evaluation → learning → çıktı.
- [ ] `NeuralRun.result`'a `reasoning`, `hypotheses`, `learning` alanları eklenir.
- [ ] Hata durumunda `failed` + error; çekirdek exception sızdırmaz.
- [ ] Paylaşımlı hafıza akışa dahil edilir (context'e shared lessons).

### Task 7: Testler ve doğrulama

**Files:**
- Modify: `agent_app/tests_neural_core.py`

- [ ] Reasoning çevrimdışı testi (hipotez ≥1, trace ≥3).
- [ ] Learning testi (ders ≥1, shared kayıt).
- [ ] Binary control traversal/onaysız yazma testi.
- [ ] Paylaşımlı hafıza izolasyon testi.
- [ ] `manage.py test agent_app` tamamı geçer; `check` temiz.

## Review Focus (görevlere eklenen testler)

- Boş hedef → ValueError.
- LLM yok → çevrimdışı tamamlanma.
- Traversal yolu → ValueError.
- Onaysız yazma → hiçbir yan etki.
- Başka kullanıcının shared kaydı → sadece shared=True görülebilir.

---

Plan tamamlandı. Uygulama yöntemi seçilsin.
```

Wait, the writing-plans skill says ask execution method. I'll do that now. But I should also consider whether to run brainstorming first. The user gave architecture; plan is fine.

Let me also verify I should create the plan file (done). Now ask user execution method.<|end|><|start|> rootReducer <|constrain|>xml<|message|><rfl:parameter name="questions">
[{"question": "Plan docs/superpowers/plans/2026-11-16-neural-core-minari-engines.md dosyasına kaydedildi. Uygulama yöntemi seçin.", "header": "Yöntem", "options": [{"label": "Subagent-driven (Önerilen)", "description": "Her görev için taze subagent + reviewer; en kapsamlı"}, {"label": "Native", "description": "Bu oturumda kendim uygularım; tek final review"}, {"label": "Planı önce incele", "description": "Planı okuyup düzeltmeler isteyeceğim"}]}]