# 3D Sahne Sistemi — Kalan Görevler (Sırayla)

Oluşturulma: 2026-11-18
Durum: Tamamlandı (pratik kapsam, 2026-11-18)

## Görev Listesi

1. **Sınıf (EntityClass) yönetimi ve editörü**
   - API: class listele/oluştur/güncelle/sil (kullanıcı sınıfları), detay getir.
   - Studio/UI: sınıf oluştur/düzenle, parametre ve bileşen/iskelet şablonu (JSON) düzenleme, önizleme/instantiate.
   - Kimlik kilitleme (`identity.locked_fields`) ve parametre varyasyonları desteği.

2. **Human anatomisi ve 3D geometriyi geliştirme**
   - Daha detaylı kemik/eklem hiyerarşisi (parmaklar, omurga segmentleri, ayak bileği vb. genişletilebilir).
   - Daha iyi prosedürel humanoid mesh (oranlar, segmentler) ve parametrelerle varyasyon (boy, yapı).
   - İskelet verisinin node/component’lerde tam temsil edilmesi.

3. **Sahne/node düzenleme (gerçek zamanlı düzenleme temeli)**
   - Studio’da node seçimi, transform düzenleme (position/rotation/scale), görünürlük, silme/çoğaltma.
   - Bileşen (identity/skeleton/physics/animation) görüntüleme ve düzenleme.
   - Değişikliklerin anlık kaydı ve görüntüleyiciye yansıması.

4. **Animasyon/poz sistemi (temel)**
   - Pose/keyframe veri modeli (components.animation), kemik dönüşümlerini saklama.
   - Basit pose preset’leri ve pose uygulama; timeline’a hazırlık.

5. **Fizik ve eylem komutları (temel)**
   - Fizik bileşeni şeması (body, collider, mass, constraints) ve node’lara uygulama.
   - Eylem komutlarının (“kılıçla vur”, “yürü” vb.) animasyon/fizik parametrelerine eşlenmesi için eylem şablonları ve API kancası.

6. **Timeline/keyframe ve gerçek zamanlı düzenleme UI**
   - Timeline veri modeli (kare/zaman, track, keyframe) ve sahneye bağlı saklama.
   - Studio’da temel timeline UI ve oynatma/önizleme (istemci tarafı).

7. **3D üretim sağlayıcı kancaları ve tutarlılık**
   - Harici text/image-to-3D sağlayıcı arayüzü (hook) ve asenkron job entegrasyonu.
   - Kimlik/tutarlılık alanlarının (locked_fields, stable_id) üretim ve düzenleme akışında korunması.

## İlerleme
- [x] 1. Sınıf yönetimi ve editörü (API + Studio sınıf editörü/kilitli alan bilgisi/instantiate akışı)
- [x] 2. Human anatomisi ve 3D geometri (detaylı iskelet şablonu, parmak/omurga segmentleri, boy/yapı parametreleri, prosedürel humanoid render)
- [x] 3. Sahne/node düzenleme (node seçimi, transform/kimlik/iskelet/animasyon/fizik düzenleme, kaydet/sil/çoğalt, sahne yenileme)
- [x] 4. Animasyon/poz sistemi — pose preset/aksiyon seti genişletildi (`entity_system`), pose/animasyon bileşen normalizasyonu, keyframe yakalama ve timeline-state ile pose/transform önizleme uygulanması; prosedürel humanoid glTF’lerde **rigid segmented kemik hiyerarşisi** (adlandırılmış bone node’lar, parent-child + joint pivot) üretilir ve Studio’da pose node rotasyonları uygulanabilir. Yüklenen harici rigged/skinned glTF’lerde kemik/SkinnedMesh poz uygulaması desteklenir.
- [x] 5. Fizik ve eylem komutları — fizik şeması (gravity/damping/velocity/impulse vb.) normalize edildi, eylem→animasyon+fizik eşlemeleri genişletildi, deterministik simülasyon adımı (`step_physics`) ve timeline preview; Studio’da **cannon-es tabanlı client-side gerçek zamanlı fizik** toggle/senkron eklendi (sunucu tarafı motor entegrasyonu ayrı).
- [x] 6. Timeline/keyframe ve gerçek zamanlı düzenleme UI — normalize timeline (tracks/keyframes), scrub/seek + zaman göstergesi + play/pause/step, non-destructive preview (`/timeline/state/`) ve “uygula & kaydet” akışı, keyframe listesi/güncelleme
- [x] 7. 3D üretim sağlayıcı kancaları ve tutarlılık — async job modeli (`SceneGenerationJob`, migration 0017), provider arayüzü + prosedürel uçtan uca job akışı (`scene_generation.py`, `/api/3d-jobs/*`), **Meshy/Tripo/generic HTTP** entegrasyon kodu (API key/base URL ile etkinleşir); `locked_fields/stable_id` tutarlılığı korunur
- [x] 8. Eğitim/export scaffold — `learned_models` export yardımcıları ve `manage.py export_learned_training_data` ile 3D/video JSONL dataset dışa aktarımı (bkz. `docs/training-pipeline.md`)

## Notlar
- 2026-11-18: 4–7 ve feasible ek iyileştirmeler tamamlandı. `entity_system.py` (normalize/pose/action/timeline/step_physics + rigged humanoid glTF hiyerarşisi), `scene_generation.py` (async job, prosedürel + harici sağlayıcı entegrasyonları), `scene_views.py`/`urls.py` (timeline state, 3D jobs/providers), `studio.html` (scrub/track/non-destructive preview, job UI, cannon-es fizik toggle, bone/pose uygulama), `learned_models.py` ve export komutu güncellendi. `py_compile`, `manage.py check`, `agent_app.tests_scene_features` (9 OK) ve hedefli API/job/timeline/studio smoke doğrulandı.
- Ortam/dış bağımlılık gerektirenler: harici 3D sağlayıcı **çıktıları** için geçerli API anahtarı/kota/ağ gerekir (kod entegrasyonu hazır). Gerçek ML ağırlık fine-tune için GPU/eğitim altyapısı ve hedef runtime gerekir (dataset export/scaffold hazır). Tam skinned-mesh (vertex weight) deformasyonu ve sunucu tarafı gerçek fizik motoru bu kapsamda ek entegrasyon ister; prosedürel modellerde rigid segmented rig ve client-side fizik sağlandı.
