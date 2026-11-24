# Öğrenilmiş 3D/Video Eğitim Verisi ve Pipeline (Scaffold)

Bu belge, EMOG içi öğrenen bilgi modellerinden (`LearnedEntityKnowledge`, `LearnedVideoKnowledge`)
fine-tune/dataset hazırlığı için mevcut scaffold'u açıklar. **Not:** Gerçek model ağırlığı eğitimi (fine-tune)
GPU/eğitim altyapısı, hedef model/runtime ve uygun veri hacmi gerektirir; bu ortamda yalnızca veri
dışa aktarımı ve pipeline iskeleti sağlanır.

## Dışa Aktarım

```powershell
.\.venv\Scripts\python.exe manage.py export_learned_training_data --limit 1000
# isteğe bağlı:
# --output-dir <dizin>
# --user-id <id>
```

Üretilen dosyalar (varsayılan: `MEDIA_ROOT/training_exports/`):
- `entity_3d_training.jsonl`: bottom-up 3D sınıf/varlık hiyerarşisi, bileşen/iskelet/render/parametre ve güven bilgisi (chat-style örnekler).
- `video_training.jsonl`: başarılı video üretim profili ve iş içgörüleri (sağlayıcı/format/çözünürlük/süre vb.).

## Kullanım (örnek akış)
1. Öğrenen modelleri araştırma/üretim akışlarıyla doldurun (3D sınıf araştırma/instantiate, başarılı video işleri).
2. Export komutunu çalıştırın ve JSONL dosyalarını toplayın.
3. Hedef modele uygun fine-tune formatına dönüştürün/gerekirse filtreleyin (ör. SFT chat formatı).
4. Eğitim altyapınızda (GPU, trainer/PEFT vb.) ayrı bir eğitim işi olarak çalıştırın; üretilen ağırlıkları/adapter'ları
   uygulama yapılandırmasına uygun şekilde entegre edin.

## Harici 3D Sağlayıcı Yapılandırması (kod entegrasyonu mevcut)

`agent_app/scene_generation.py` şu sağlayıcıları destekler (API anahtarı/base URL ile etkinleşir):
- **Meshy:** `MESHY_API_KEY` (ops. `MESHY_BASE_URL`, varsayılan `https://api.meshy.ai`)
- **Tripo:** `TRIPO_API_KEY` (ops. `TRIPO_BASE_URL`, varsayılan `https://api.tripo3d.ai/v2`)
- **Generic HTTP:** `SCENE_3D_API_KEY` + `SCENE_3D_BASE_URL` (`/generate` ve opsiyonel task polling sözleşmesi)

Alternatif olarak `settings.SCENE_GENERATION["providers"]` üzerinden `api_key/base_url/enabled` verilebilir.
Anahtar/erişim olmadan sağlayıcılar "unavailable/hook" olarak raporlanır.

## Kısıtlar
- Ağırlık eğitimi bu scaffold ile **çalıştırılmaz** (altyapı bağımlı).
- Harici sağlayıcı çıktıları anahtar, kota ve ağ erişimine bağlıdır.
- Prosedürel humanoid modellerde rigid segmented kemik hiyerarşisi üretilir (pose node rotasyonları uygulanabilir);
  tam skinned-mesh (vertex weight) deformasyonu ve sunucu tarafı gerçek fizik motoru ek entegrasyon gerektirir.
