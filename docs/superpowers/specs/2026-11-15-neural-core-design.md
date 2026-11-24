# NEURAL CORE — EMOG Entegrasyon Tasarımı

Tarih: 2026-11-15
Durum: Onaylandı (kullanıcı: "hepsini tamamla ve EMOG'a entegre et")

## Amaç

EMOG'un içinde, sekiz yeteneği tek bir karar–deney–öğrenme döngüsünde birleştiren
bir bilişsel çekirdek kurmak. Ayrı bir proje/script değil; EMOG'un kimlik
doğrulaması, veritabanı, arayüzü, özellik anahtarları ve testleriyle entegre bir
modül olacak.

## Kapsam ve yetenek eşleşmesi

| Yetenek | EMOG içi karşılık |
|---|---|
| Alternatif düşünme | `discovery.py`: çoklu yaklaşım üretimi, artı/eksi ve puanlama |
| Zor problem çözme | `planner.py` + `kernel.py`: hedefi adımlara bölme, adım adım yürütme |
| Bağımsız araştırma | `research.py` + `AutonomyGoal` + `neural_cycle` komutu |
| Deneyimden öğrenme | `CoreExperience` + `experience.py`: başarı/başarısızlık ve ders kaydı |
| Kişisel öğrenme | `UserPreference` + `preferences.py`: dil, ton, teknoloji tercihleri |
| Model bağımsızlığı | `gateway.py`: NVIDIA/OpenAI/DeepSeek/Anthropic/Ollama + fallback |
| Bilgisayar kontrolü | `tools.py`: izinli dosya/komut araçları, onay kapısı |
| Kendini geliştirme | `evolution.py`: deneyimlerden güvenli politika önerileri, onayla uygulama |

## Mimari

```
/neural/ (EMOG arayüzü)
      |
CognitiveKernel
  ├─ Planner          → adımlar
  ├─ DiscoveryEngine  → alternatif yaklaşımlar
  ├─ ResearchEngine   → kaynaklı araştırma
  ├─ ModelGateway     → sağlayıcı bağımsız LLM
  ├─ ToolRegistry     → izin/onay kontrollü araçlar
  ├─ Evaluator        → çıktı puanı
  ├─ ExperienceStore  → başarı/başarısızlık dersleri
  ├─ PreferenceStore  → kullanıcı tercihleri
  └─ EvolutionEngine  → güvenli politika önerileri
      |
Django ORM / SQLite (neural_* tabloları)
```

## Veri modeli

- `NeuralCoreRun`: hedef, mod, durum, plan, adımlar, çıktı, değerlendirme.
- `DiscoveryRun`: aday yaklaşımlar, seçilen yaklaşım, araştırma bulguları.
- `CoreExperience`: deneyim, sonuç, puan, ders, bağlam.
- `UserPreference`: kullanıcıya özel anahtar/değer, güven, kaynak.
- `EvolutionProposal`: öneri, gerekçe, risk, durum.
- `ToolApproval`: araç adı, argümanlar, durum, sonuç.
- `AutonomyGoal`: kullanıcı hedefi, aralık, etkinlik, sonraki çalışma.

## Güvenlik sınırları

- Dosya araçları yalnızca `projects/` ve `task_runs/` altında çalışır.
- Yazma ve komut çalıştırma **her zaman** kullanıcı onayı ister.
- Komutlar beyaz listeye ve 30 saniye zaman aşımına tabidir.
- Kendini geliştirme kod veya model ağırlığı değiştirmez; yalnızca
  `planner.depth`, `research.enabled`, `gateway.preferred`, `prompt.addendum`,
  `tools.require_approval` gibi güvenli politika anahtarlarını önerir.
- Onay olmadan hiçbir politika uygulanmaz.
- Tüm API'ler EMOG oturumu ister; CSRF koruması Django üzerinden çalışır.

## Entegrasyon noktaları

- `agent_app/urls.py`: `/neural/` ve `/api/neural/*` yolları.
- `agent_app/templates/agent_app/base.html`: "Neural Core" menü bağlantısı.
- `audit/features.py`: `neural_core` ve alt özellik anahtarları.
- `emog_project/settings.py`: sağlayıcı, araç ve güvenlik ayarları.
- `agent_app/models.py`: yeni modellerin kaydı.
- `agent_app/migrations/`: `neural_*` tabloları.
- `agent_app/tests_neural_core.py`: birim + API testleri.

## Çalışma biçimi

1. Kullanıcı `/neural/` sayfasından hedef ve mod seçer.
2. Kernel arka planda çalışır; arayüz durumu poll eder.
3. LLM sağlayıcısı yoksa çekirdek deterministik plan/keşif/araştırma
   şablonlarıyla çalışmaya devam eder (çevrimdışı mod).
4. Her çalıştırma deneyim olarak kaydedilir; tercihler sonraki çalıştırmalara
   taşınır.
5. Otonom hedefler `python manage.py neural_cycle` ile zamanlanır.

## Non-goals

- Model ağırlığı eğitmek veya kodu kendi kendine değiştirmek.
- Sınırsız/onaysız bilgisayar kontrolü.
- Ayrı bir mikroservis veya ayrı proje oluşturmak.
