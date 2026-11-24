# NEURAL CORE — EMOG Entegrasyonu Uygulama Planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NEURAL CORE'un sekiz yeteneğini EMOG içinde tek bir çalışan bilişsel çekirdek olarak entegre etmek.

**Architecture:** `agent_app/neural_core/` paketi; Django ORM modelleri, sağlayıcı bağımsız model gateway'i, planlama/keşif/araştırma/değerlendirme hattı, deneyim ve tercih deposu, izin/onay kontrollü araçlar ve güvenli politika önerileri. `/neural/` arayüzü ve `/api/neural/*` uçları EMOG oturumu, CSRF, feature flag ve mevcut middleware ile çalışır.

**Tech Stack:** Django 5.2, SQLite, Django ORM, mevcut `buildkit.search` ve `buildkit.llm`, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-15-neural-core-design.md`

## Global Constraints

- Ayrı proje/mikroservis yok; her şey `agent_app` içinde.
- Dosya araçları yalnızca `projects/` ve `task_runs/` altında; yol kaçışı (traversal) reddedilir.
- Yazma ve komut çalıştırma onaysız yürütülmez; komutlar beyaz liste + 30 sn zaman aşımı.
- LLM yoksa/çevrimdışıysa plan, keşif ve araştırma deterministik şablonlarla çalışır.
- Kendini geliştirme kod/ağırlık değiştirmez; yalnızca güvenli anahtarlar (`planner.depth`, `research.enabled`, `gateway.preferred`, `prompt.addendum`, `tools.require_approval`) önerir.
- Tüm API'ler kimlik doğrulaması ister; kullanıcı yalnızca kendi kayıtlarını görür.
- Testler `NEURAL_CORE["OFFLINE"]=True` ile ağ çağrısı yapmadan çalışır.

## Review Focus

- Boş/çok uzun hedef metni: çekirdek çökmemeli, anlaşılır doğrulama hatası dönmeli.
- LLM sağlayıcısı yok veya hata veriyor: çalışma tamamlanmalı (çevrimdışı mod).
- Araç yolunda `..` veya kök dışı mutlak yol: reddedilmeli.
- Onaysız yazma/komut: hiçbir yan etki oluşmamalı.
- Başka kullanıcının çalışması/onayı/tercihi: 404/403, veri sızıntısı yok.

---

### Task 1: Paket iskeleti, ayarlar ve veri modeli

**Files:**
- Create: `agent_app/neural_core/__init__.py`
- Create: `agent_app/neural_core/config.py`
- Create: `agent_app/neural_core/models.py`
- Modify: `agent_app/models.py` (dosya sonunda import)
- Modify: `emog_project/settings.py` (NEURAL_CORE bloğu)
- Create: `agent_app/migrations/0010_neural_core.py` (makemigrations ile)

**Interfaces:**
- `config.get_config() -> dict`; `config.offline() -> bool`.
- Modeller: `NeuralRun`, `NeuralDiscovery`, `NeuralExperience`, `NeuralPreference`, `NeuralProposal`, `NeuralToolApproval`, `NeuralGoal`.

- [ ] `NEURAL_CORE` ayarlarını ekle (OFFLINE, modlar, limitler, sağlayıcılar, araç kökleri, komut allowlist, onay zorunluluğu, otonomi aralığı).
- [ ] Modelleri `app_label="agent_app"` ile yaz; kullanıcı FK'si `settings.AUTH_USER_MODEL` yerine mevcut `agent_app.User` (`user` UUID) olsun.
- [ ] `agent_app/models.py` sonunda `from .neural_core.models import *  # noqa: F401,F403` ekle.
- [ ] `makemigrations agent_app` ve `migrate` çalıştır.
- [ ] `manage.py check` hatasız.

### Task 2: Model gateway ve bilişsel yardımcılar

**Files:**
- Create: `agent_app/neural_core/gateway.py`
- Create: `agent_app/neural_core/evaluation.py`
- Create: `agent_app/neural_core/planner.py`
- Create: `agent_app/neural_core/discovery.py`
- Create: `agent_app/neural_core/research.py`

**Interfaces:**
- `ModelGateway.complete(system, user, purpose="neural", temperature=0.2, max_tokens=None) -> str`
- `ModelGateway.available() -> list[str]`, `ModelGateway.active_provider() -> str`
- `evaluation.score_run(plan, alternatives, findings, result) -> dict` (`score` 0-100, `strengths`, `gaps`)
- `planner.build_plan(goal, mode, context, gateway) -> list[dict]` (her adım: `key`, `title`, `detail`, `status`)
- `discovery.generate(goal, mode, gateway, max_items) -> list[dict]` (`name`, `summary`, `pros`, `cons`, `score`)
- `research.run(goal, queries, enabled, gateway) -> dict` (`queries`, `findings`, `summary`, `offline`)

- [ ] Gateway sağlayıcı sırası: nvidia → openai → deepseek → anthropic → ollama; anahtarsız sağlayıcı atlanır.
- [ ] NVIDIA çağrısı mevcut `buildkit.llm.chat` üzerinden; diğerleri OpenAI-uyumlu HTTP (Anthropic kendi şeması, Ollama `/api/chat`).
- [ ] Sağlayıcı yoksa/başarısızsa `""` döndür; çevrimdışı modda hiç ağ çağrısı yapma.
- [ ] Plan ve keşif LLM çıktısını JSON olarak ayrıştır; başarısızsa deterministik şablona düş.
- [ ] Araştırma `buildkit.search.search` kullanır; hata/boş sonuçta `offline=True` ile şablon özet döner.

### Task 3: Deneyim, kişisel tercih ve kendini geliştirme

**Files:**
- Create: `agent_app/neural_core/experience.py`
- Create: `agent_app/neural_core/preferences.py`
- Create: `agent_app/neural_core/evolution.py`

**Interfaces:**
- `experience.record(user, goal, outcome, score, lesson, context, run=None) -> NeuralExperience`
- `experience.recent(user, limit=20) -> list[NeuralExperience]`
- `experience.lessons(user, limit=10) -> list[str]`
- `preferences.set_value(user, key, value, source="user", confidence=1.0) -> NeuralPreference`
- `preferences.get_value(user, key, default=None)`, `preferences.all_values(user) -> dict`
- `evolution.propose(user) -> list[NeuralProposal]` (yalnızca güvenli anahtarlar)
- `evolution.apply(user, proposal) -> bool` (pending değilse/anahtar güvenli değilse False)

- [ ] Deneyim kaydı başarı/başarısızlık ve ders üretir; en fazla 20 deneyim bağlamda kullanılır.
- [ ] Tercih `(user, key)` benzersiz; JSON değer.
- [ ] Öneri kuralları: düşük ortalama puan → `planner.depth=deep`; araştırma kapalı ve başarısızlık → `research.enabled=true`; sağlayıcı hatası → `gateway.preferred`; eksik dil/ton → `prompt.addendum`.
- [ ] `apply` yalnızca izinli anahtarları yazar ve öneriyi `applied` yapar.

### Task 4: İzin/onay kontrollü bilgisayar araçları

**Files:**
- Create: `agent_app/neural_core/tools.py`

**Interfaces:**
- `ToolRegistry(user)`; `list_tools() -> list[dict]`
- `request_write(user, path, content) -> NeuralToolApproval`
- `request_command(user, command, cwd=".") -> NeuralToolApproval`
- `decide(approval_id, approve, user) -> dict` (onayda yürütür, sonucu kaydeder)
- `read_file(user, path) -> dict`, `list_files(user, path=".") -> dict`

- [ ] `_resolve(path)` kök dışına çıkan yolları `ValueError` ile reddeder.
- [ ] Okuma boyut sınırı; yazma `TOOL_MAX_WRITE_BYTES` ile sınırlı.
- [ ] Komut yalnızca allowlist ilk token + `shell=False` + `timeout`.
- [ ] Onay kaydı `pending` oluşturur; onaylanınca yürütür, reddedilince `rejected`.

### Task 5: Çekirdek ve otonom hedefler

**Files:**
- Create: `agent_app/neural_core/kernel.py`
- Create: `agent_app/management/commands/neural_cycle.py`

**Interfaces:**
- `CognitiveKernel(user).run(goal, mode="full", run=None) -> NeuralRun`
- `run_goal_cycle(limit=None) -> dict` (due `NeuralGoal` kayıtlarını çalıştırır)
- `neural_cycle` komutu: `--limit`, `--dry-run`.

- [ ] `run` akışı: doğrula → plan → keşif (moda göre) → araştırma (moda göre) → değerlendirme → deneyim → çıktı.
- [ ] Modlar: `full`, `solve`, `explore`, `research`, `learn`.
- [ ] Hata durumunda `NeuralRun.status="failed"` + `error`; çekirdek exception sızdırmaz.
- [ ] Otonom hedef aralığı en az `MIN_INTERVAL_MINUTES`; `next_run_at` güncellenir.

### Task 6: API, URL, feature flag ve menü

**Files:**
- Create: `agent_app/neural_core/views.py`
- Modify: `agent_app/urls.py`
- Modify: `audit/features.py`
- Modify: `agent_app/templates/agent_app/base.html`

**Interfaces:**
- Sayfa: `GET /neural/` → `neural.html`
- `GET /api/neural/status/` → `{ok, providers, offline, counts}`
- `POST /api/neural/run/` `{goal, mode}` → `{ok, run}` (thread başlatır)
- `GET /api/neural/run/<id>/` → run detayı
- `GET/POST /api/neural/preferences/`
- `GET/POST /api/neural/proposals/`, `POST /api/neural/proposals/<id>/apply/`
- `GET /api/neural/tools/`, `POST /api/neural/tools/read/`, `POST /api/neural/tools/write/`, `POST /api/neural/tools/command/`, `POST /api/neural/tools/approvals/<id>/decide/`
- `GET/POST /api/neural/goals/`, `POST /api/neural/goals/<id>/toggle/`

- [ ] Tüm uçlar `require_auth` (sayfa yönlendirmeli) ve ana `neural_core` feature'ı ile korunur; alt yeteneklerde `neural_tools`, `neural_evolution`, `neural_autonomy` kontrol edilir.
- [ ] JSON gövdeleri güvenli ayrıştırılır; hatalar `{ok:false, error, code}`.
- [ ] Menüye `Neural Core` bağlantısı (feature açıksa).
- [ ] Feature flag'ler: `neural_core` (default True), `neural_tools` (default True, critical), `neural_evolution` (default True), `neural_autonomy` (default True).

### Task 7: `/neural/` arayüzü

**Files:**
- Create: `agent_app/templates/agent_app/neural.html`

- [ ] Üstte durum çubuğu (çevrimdışı/sağlayıcı), hedef + mod formu, çalıştır butonu.
- [ ] Plan adımları, alternatifler, araştırma bulguları, puan ve dersler panelleri.
- [ ] Tercihler, öneriler ve otonom hedefler bölümleri; onay/ret butonları.
- [ ] Araçlar: dosya okuma, yazma talebi, komut talebi ve onay kuyruğu.
- [ ] Poll ile çalışan run durumu; CSRF token cookie/header ile gönderilir.
- [ ] Mevcut `base.html` stil değişkenlerini kullan; mobil uyumlu.

### Task 8: Testler ve canlı doğrulama

**Files:**
- Create: `agent_app/tests_neural_core.py`

- [ ] Model/migration smoke testi.
- [ ] Planner/discovery/research çevrimdışı testleri.
- [ ] Gateway sağlayıcı seçimi ve hata düşüşü testleri.
- [ ] Kernel tam akış + başarısız hedef testi.
- [ ] Tercih/deneyim/evrim izin testleri.
- [ ] Araç traversal, onaysız yazma, onaylı yazma, allowlist dışı komut testleri.
- [ ] API yetki (oturumsuz 401/redirect), kullanıcı izolasyonu, run oluşturma testleri.
- [ ] `manage.py test agent_app` tamamı geçer; `/neural/` HTTP 200 ve API smoke.
