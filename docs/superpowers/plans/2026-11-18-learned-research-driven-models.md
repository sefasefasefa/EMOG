# Öğrenen, Araştırma-Tabanlı (Şablonsuz) Model Sistemi — Task List

Oluşturulma: 2026-11-18
Durum: Devam ediyor

## Kabul Edilen Varsayımlar (kullanıcı onayıyla inşa başlatıldı)
- **“Model” = uygulama-içi öğrenen bilgi modeli (DB):** ML ağırlık fine-tune değil; araştırma/üretimden öğrenilen bilgiler kalıcı olarak saklanır ve büyür.
- **Ayrı öğrenen modeller:** 3D varlık/sınıf bilgisi için ayrı, video üretimi için ayrı öğrenen bilgi kayıtları.
- **Her zaman internet araştırması:** Sınıf/özellik öğrenme ve araştırma eylemlerinde web araştırması her zaman yapılır; sonuçlar yerel öğrenen bilgiyle birleştirilip güncellenir.
- **Bottom-up hiyerarşi:** En temel katmandan yukarı doğru (malzeme/alt-bileşen → parça → montaj/sistem → bütün). Biyolojik varlıklarda “kan/hücre-benzeri temel → doku → organ → sistem → organizma” gibi derin katmanlar desteklenir; diğer varlıklarda eşdeğer alt bileşenlerden üste çıkılır.
- **Video başarı sinyali:** Üretim başarıyla tamamlandığında (completed/çıktı üretildi) öğrenme kaydı oluşturulur/güncellenir (ileride kullanıcı geri bildirimi eklenebilir).

## Görevler (sırayla)

- [x] 1. **Veri modeli (öğrenen bilgi):** 3D ve video için ayrı kalıcı öğrenen bilgi modelleri + migration (hiyerarşi, özellikler, kaynaklar, güven/özet, araştırma geçmişi, ilişkiler).
- [x] 2. **Araştırma & sentez motoru (3D):** Her zaman web araştırması + LLM sentezi ile bottom-up hiyerarşik sınıf/özellik tanımı üret; yerel öğrenen modelle birleştir/kaydet.
- [x] 3. **EntityClass akışını şablonsuzlaştırma:** Sabit `SYSTEM_ENTITY_CLASSES`/preset iskeletleri birincil kaynak olmaktan çıkar; sınıf araştır/oluştur/güncelle API’si ile dinamik, öğrenilmiş tanımdan `EntityClass` üretimi; instantiate/render dinamik spec ile uyumlu.
- [x] 4. **Studio UI (araştırma tabanlı sınıf):** “Araştır & Öğren/Oluştur” akışı, bottom-up hiyerarşi ve kaynak/öğrenme durumu görünümü.
- [x] 5. **Video öğrenen modeli & kayıt:** Başarılı video üretimlerinden öğrenilen profil/bilgi (prompt, ayarlar, sağlayıcı, çıktı, çıkarımlar) ayrı video bilgi modeline yazılır.
- [x] 6. **Video üretimine öğrenme entegrasyonu:** `run_generation`/tamamlanma yoluna başarı öğrenmesi hook’u (üretim ve düzenleme başarı akışlarında) bağlandı.
- [x] 7. **Doğrulama & ilerleme güncellemesi:** check/py_compile tamam; migration uygulandı.

## İlerleme Notları
- Başlangıç: plan ve tasklist oluşturuldu, uygulama başlıyor.
- 2026-11-18: Görev 1-4 uygulandı. `LearnedEntityKnowledge`/`LearnedVideoKnowledge` + migration; `agent_app/learned_models.py` (her araştırmada web araştırması + sentez + yerel öğrenen modele birleştirme); `ensure_system_entity_classes` sabit şablon üretimi durduruldu, dinamik/hiyerarşi tabanlı render fallback eklendi; sınıf araştırma API’leri ve Studio “Araştır & Öğren” akışı eklendi.
- 2026-11-18: Görev 5-6 uygulandı. Başarılı video üretim/düzenleme tamamlandığında `learn_from_successful_video_job` ile `LearnedVideoKnowledge` güncelleniyor (`agent_app/video_generation.py`, `agent_app/learned_models.py`). Görev 7 doğrulamaları: `py_compile` ve `manage.py check` geçti.