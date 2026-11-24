# 3D Kesiklerin Tamamlanması — Kısa Tasarım

**Tarih:** 2026-11-18  
**Durum:** Onaylandı (kullanıcı: “tamamla herşeyi”)  
**Kapsam:** `docs/superpowers/plans/2026-11-18-3d-remaining-tasks.md` görev 4–7’nin pratik olarak tamamlanması + öğrenen-model akışlarının tutarlı güçlendirilmesi.

## Amaç
Sahne/3D stüdyosunda kalan MVP/hook boşluklarını, mevcut mimariyle uyumlu ve bu ortamda çalışır şekilde uçtan uca tamamlamak: daha zengin animasyon/pose, pratik fizik-eylem simülasyonu, gerçek zamanlı timeline/scrub UI ve asenkron 3D üretim job/provider akışı.

## Mevcut Durum (özet)
- 4–7 şema/API temel hook’ları var (node action, scene timeline saklama, provider listesi). UI kısmi (JSON tabanlı, sıralı playback). Harici motor/job entegrasyonu yok.
- 3D/video için ayrı öğrenen bilgi modelleri ve iteratif bottom-up araştırma + başarıdan öğrenme/geri besleme uygulanmış.

## Yaklaşım (onaylanan)

### 4) Animasyon/Poz
- `entity_system`: action/pose preset’lerini ve pose veri modelini genişlet (bone/transform hedefleri, reset/blend basitliği), keyframe’tan pose üret/uygula yardımcıları.
- `scene_views`/API: pose preset uygulama ve keyframe snapshot/uygulama tutarlı; bileşen merge kimlik kilitlerini korur.
- `studio.html`: seçili node/pose preset uygulama ve viewer’da uygulanabilir transform/pose preview (mevcut GLTF objelerine node transform yansıtma ve pose verisini görünür kılma; tam skinned-rig deformasyonu ortam kısıtı olarak notlanır).

### 5) Fizik ve Eylem Komutları
- Fizik bileşen şemasını zenginleştir (body/collider/mass/constraints/damping/velocity/impulse alanları) ve varsayılanlarla normalize et.
- Eylem şablonlarını animasyon+fizik parametrelerine daha kapsamlı eşle.
- Timeline tabanlı **pratik simülasyon**: keyframe/eylem/impuls’tan frame adımı hesaplayan uygulama-içi step/playback (harici fizik motoru olmadan deterministik basit entegrasyon).

### 6) Timeline / Gerçek Zamanlı UI
- Timeline veri modelini track/keyframe odaklı netleştir (scene settings’te saklama, geriye dönük uyum).
- Studio UI: scrub/seek, frame göstergesi, track/keyframe listesi ve düzenleme, anlık (non-destructive) önizleme ve uygulama/oynatma kontrolleri.

### 7) 3D Üretim Sağlayıcıları ve Async Job
- 3D üretim için job kaydı ve async akış (oluştur, durum sorgula, arka planda çalıştır) + provider arayüzü/hook.
- `procedural` sağlayıcı ile uçtan uca çalışan job akışı; harici text/image-to-3D sağlayıcılar yapılandırma/anahtar ile bağlanabilir şekilde tanımlanır.
- `locked_fields` ve `stable_id` (tutarlılık) üretim/düzenleme/aksiyon akışlarında korunur.

### Öğrenen Model Güçlendirme
- Bottom-up araştırma/uygulama ve instantiate/research yollarında öğrenen bilginin her zaman kaydedilip uygulandığını doğrula; gerekli küçük güçlendirmeleri ekle.
- 3D ve video öğrenme ayrı modellerde kalır; başarılı video üretimlerinde geri besleme korunur.

## Kısıtlar / Notlar
- Gerçek fizik motoru (Rapier/Ammo/cannon) ve tam skinned-rig bone deformation bu ortamda ek bağımlılık/rig altyapısı gerektirir; pratik simülasyon, veri modeli ve preview/scrub tamamlanır, motor entegrasyonu hook/uyumlu tasarlanır.
- Harici 3D üreticiler API anahtarı/servis olmadan gerçek çıktı üretemez; async job+provider arayüzü ve prosedürel sağlayıcı tamamlanır.

## Doğrulama
- `py_compile` (değişen modüller), `manage.py check`.
- Studio `/studio/`: sınıf araştır/instantiate, node düzenle/aksiyon, timeline scrub/playback, 3D job akışı.
- İlgili API’ler: scenes/nodes/timeline/actions, entity-classes/research/instantiate, learned-knowledge, generation-providers/jobs. 

## Plan Kapsamı
Görev 4–7 ve öğrenen-model tutarlılığı tek uygulama turunda tamamlanacak; tasklist güncellenecek.