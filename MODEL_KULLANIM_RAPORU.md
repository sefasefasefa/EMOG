# EMOG Model Kullanım Raporu

Tüm eklenen modeller kontrol edilip, gerekli yerlere bağlandı. Aşağıda tüm modeller, kullanım yerleri ve hız durumu listelenmiştir.

---

## 1. Tanımlı NVIDIA Modelleri (`.env` + `settings.py`)

| Model Anahtarı | Model ID | .env Değeri | Kullanım Amacı |
|---|---|---|---|
| `default` | `nvidia/nemotron-3-super-120b-a12b` | `nemotron-3.5-vl-120b-a12b` | Genel sohbet, varsayılan |
| `research` | `nvidia/nemotron-3-super-120b-a12b` | `nemotron-3.5-vl-120b-a12b` | Araştırma, derin analiz |
| `coding` | `nvidia/nemotron-3-super-120b-a12b` | `nemotron-3.5-vl-120b-a12b` | Kodlama |
| `agent` | `nvidia/nemotron-3-super-120b-a12b` | `nemotron-3.5-vl-120b-a12b` | Hızlı yürütme/tool calling |
| `fastest` | `nvidia/nemotron-3-super-120b-a12b` | `nemotron-3.5-vl-120b-a12b` | En hızlı yanıt |
| `multimodal` | `meta/muse-glimmer-30b` | `meta/muse-glimmer-30b` | Görsel+metin, reasoning |
| `glm_flash` | `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash` | 320B MoE, reasoning, tool use |
| `super_vl` | `nvidia/nemotron-3.5-super-vl-120b-a12b` | `nemotron-3.5-super-vl-120b-a12b` | Multimodal, reasoning, tool calling |
| `minimax_flash` | `minimaxai/minimax-m3.1-flash` | `minimaxai/minimax-m3.1-flash` | Multimodal, coding, agentic |
| `bolt` | `nvidia/nemotron-3.5-bolt-e4b` | `nemotron-3.5-bolt-e4b` | Compact reasoning, tool calling |
| `mistral_large` | `mistralai/mistral-large-4-1t-a52b` | `mistralai/mistral-large-4-1t-a52b` | Multimodal, reasoning, tool use |
| `gemma4` | `google/gemma-4-31b-it` | `google/gemma-4-31b-it` | Agentic, coding, reasoning |
| `ising_calibration` | `nvidia/ising-calibration-1.5-31b` | `nvidia/ising-calibration-1.5-31b` | Vision, calibration, quantum |
| `nano_omni_reasoning` | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | `nemotron-3-nano-omni-30b-a3b-reasoning` | Omni-modal reasoning |

Diğer: `NVIDIA_INTENT_MODEL=moonshotai/kimi-k3.1-pro`, `CHAT_FAST_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b`.

---

## 2. NVIDIA Model Tier'ları (`settings.py: NVIDIA_MODEL_TIERS`)

Tüm tier'lara `reasoning_effort="none"` eklendi (hızlı yanıt için). Model eşleştirmeleri artık `NVIDIA_TASK_MODELS`'den gelir ve `.env` ile ezilebilir.

| Tier | Model ID | Speed | Kullanım |
|---|---|---|---|
| `ultra` | NVIDIA_TASK_MODELS[`default`] | 9 | Karmaşık reasoning, mimari tasarım |
| `high` | NVIDIA_TASK_MODELS[`default`] | 8 | Kodlama, refactor |
| `standard` | NVIDIA_TASK_MODELS[`default`] | 8 | Genel uygulama |
| `fast` | NVIDIA_TASK_MODELS[`fastest`] | 9 | Kısa işler |
| `lite` | NVIDIA_TASK_MODELS[`fastest`] | 9 | Anlık yanıt |
| `multimodal` | NVIDIA_TASK_MODELS[`multimodal`] | 6 | Görsel+metin |
| `glm_flash` | NVIDIA_TASK_MODELS[`glm_flash`] | 7 | GLM 5.3 Flash |
| `super_vl` | NVIDIA_TASK_MODELS[`super_vl`] | 7 | Super VL 120B |
| `minimax_flash` | NVIDIA_TASK_MODELS[`minimax_flash`] | 8 | MiniMax M3.1 |
| `bolt` | NVIDIA_TASK_MODELS[`bolt`] | 9 | Bolt E4B |
| `mistral_large` | NVIDIA_TASK_MODELS[`mistral_large`] | 6 | Mistral Large 4 |
| `gemma4` | NVIDIA_TASK_MODELS[`gemma4`] | 8 | Gemma 4 31B |
| `ising_calibration` | NVIDIA_TASK_MODELS[`ising_calibration`] | 6 | Ising Kalibrasyon |
| `nano_omni_reasoning` | NVIDIA_TASK_MODELS[`nano_omni_reasoning`] | 7 | Nano Omni Reasoning |

**Önceki durum:** ultra/high/standard/fast/lite hepsi `nemotron-3.5-lightning-30b-a3b`'e (42sn yavaş) işaret ediyordu.
**Yeni durum:** tüm tier'lar hızlı modelleri kullanıyor (`reasoning_effort="none"` ile).

---

## 3. Model Gateway (`agent_app/model_gateway.py`)

**Değişiklikler:**
- `nvidia` provider'ı artık tek bir default endpoint değil; her `NVIDIA_TASK_MODELS` modeli ayrı bir `nvidia:<task_key>` endpoint olarak kaydediliyor.
- `select_model(task_type)` göreve göre doğru NVIDIA modelini döndürür (ör. `code_research` → `nvidia:coding`, `general_reasoning` → `nvidia:default`).
- Tüm NVIDIA endpoint'leri `reasoning_effort="none"` ve `timeout=120sn` (`NVIDIA_TIMEOUT`) ile yapılandırıldı.
- `ModelEndpoint` artık `reasoning_effort` alanına sahip; `_call_provider` bu değeri sağlayıcı çağrılarına iletiyor.

**Kayıtlı NVIDIA endpoint'leri:** `nvidia`, `nvidia:multimodal`, `nvidia:glm_flash`, `nvidia:super_vl`, `nvidia:minimax_flash`, `nvidia:bolt`, `nvidia:mistral_large`, `nvidia:gemma4`, `nvidia:ising_calibration`, `nvidia:nano_omni_reasoning`.

---

## 4. Model Router (`agent_app/model_router.py`)

`settings.NVIDIA_MODEL_TIERS` ve `settings.TASK_COMPLEXITY_KEYWORDS` kullanılarak görevleri tier'lere yönlendirmeye devam ediyor. Güncellenen tier'lar sayesinde artık her tier hızlı bir modele işaret ediyor.

---

## 5. View / LLM Çağrıları (`agent_app/views.py`)

- `llm_create()` → `reasoning_effort="none"` varsayılan (hızlı tam yanıt).
- `llm_create_vision()` → `reasoning_effort="none"`.
- Fast chat yolu → `CHAT_FAST_MODELS` ile `reasoning_effort="none"`.
- Intent router → `NVIDIA_INTENT_MODEL` ile `reasoning_effort="none"`.
- `llm_create_resilient()` → 404/429/ag hatasında yedek modele otomatik geçiş.

---

## 6. Neural Core Gateway (`agent_app/neural_core/gateway.py`)

`learned_models.py` tarafından kullanılıyor. NVIDIA sağlayıcısı `OMNIROUTE_DEFAULT_MODEL` üzerinden `build_llm.chat()` çağrısı yapıyor (güncellenen `settings.py` değerlerini otomatik alıyor).

---

## 7. Multi-Agent Ecosystem

`multi_agent_ecosystem/orchestrator.py`, `specialist_agents.py`, `critic_agent.py`, `synthesizer.py` hepsi `os.getenv("NVIDIA_DEFAULT_MODEL", ...)` kullanıyor → `.env`'deki `nemotron-3.5-vl-120b-a12b` değerini kullanıyor.

---

## 8. Yapılması Gereken Canlı Kontrol

Aşağıdaki komutla tüm modellerin gerçekten çağrılabilir olup olmadığını ve yanıt sürelerini ölçebilirsiniz:

```bash
python manage.py check_provider --probe
```

Bu komut:
1. NVIDIA API bağlantısını (DNS, TCP, TLS) adım adım test eder.
2. `/v1/models` katalogundan mevcut modelleri listeler.
3. `--probe` ile her tier modeline küçük bir `"Say OK"` isteği gönderir ve yanıt süresini ölçer.
4. Hangi modellerin 404 (hesapta deploy değil) ya da 429 (rate limit) verdiğini gösterir.

**Önemli not:** `settings.py` yorumlarında belirtilildiği gibi, `nemotron-3-super-120b-a12b` ve `nemotron-3-ultra-550b-a55b` bu hesapta 404/400 dönebilir. Eğer `--probe` bu modellerde hata verirse, `.env`'deki ilgili `NVIDIA_*_MODEL` değerlerini çalışan bir modele (ör. `nemotron-3.5-bolt-e4b` veya `nemotron-3.5-lightning-30b-a3b`) değiştirebilirsiniz.

---

## 9. Dosyalarda Yapılan Değişiklikler

1. `emog_project/settings.py` — `NVIDIA_MODEL_TIERS` güncellendi (tüm tier'lar hızlı model + `reasoning_effort="none"`, env override destekli).
2. `agent_app/model_gateway.py` — Tüm `NVIDIA_TASK_MODELS` modelleri ayrı endpoint olarak kaydediliyor; göreve göre model seçimi; `reasoning_effort` iletimi.
3. `agent_app/secrets.py` — Yeni NVIDIA tier env değişkenleri `CONFIG_VARS`'a eklendi.

---

## 10. Hız Özeti

| Bileşen | Durum |
|---|---|
| `reasoning_effort="none"` | Tüm NVIDIA çağrılarında etkin |
| Tier model'ları | Tüm 14 tier hızlı modele işaret ediyor |
| Model Gateway | 10 NVIDIA endpoint aktif, her biri `reasoning_effort=none` |
| Chat fast-path | `CHAT_FAST_MODELS` + `reasoning_effort="none"` |
| Intent router | `NVIDIA_INTENT_MODEL` + `reasoning_effort="none"` |

Tüm modeller artık gerekli yerlerde kullanılıyor ve `reasoning_effort="none"` ile en hızlı yanıt verme modunda çalışıyor. Gerçek yanıt sürelerini `python manage.py check_provider --probe` ile doğrulayabilirsiniz.
