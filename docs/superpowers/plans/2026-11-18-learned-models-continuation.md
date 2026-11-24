# Öğrenen Model Sistemi — Devam (Iteratif Bottom-Up, Geri Besleme)

Oluşturulma: 2026-11-18
Durum: Devam ediyor

## Hedef
- 3D: Her araştırmada **en dipten başlayıp sırayla yukarı** (katman katman) internet araştırması yapılmalı; her katman araştırması **öğrenen modele eklenmeli**.
- Video: Ayrı öğrenen video modeli başarılı üretimlerden büyümeli ve öğrendikleri sonraki üretimlere geri beslenmeli.
- Öğrenilen bilgi görünür ve yeniden kullanılabilir olmalı.

## Görevler
- [x] 1. **3D iteratif bottom-up araştırma:** Katmanları (en temel → bütün) sırayla araştır, her adımda web araştırması yap ve sonucu `LearnedEntityKnowledge`’e anlık birleştir/kaydet (araştırma geçmişi adım adım).
- [x] 2. **Video öğrenme geri beslemesi:** `LearnedVideoKnowledge` profilini üretim öncesi/sağlayıcı-seçiminde kullan (başarılı sağlayıcı/format/çözünürlük eğilimleri), her başarılı üretimde öğrenmeye devam et.
- [x] 3. **Hiyerarşik/kompozit instantiate:** Öğrenilmiş bottom-up katman/parçalardan sahnede yapılandırılmış düğüm/alt-düğüm temsili (kompozisyon) oluştur.
- [x] 4. **Öğrenen bilgi görünürlüğü:** Öğrenilmiş 3D/video bilgi kayıtlarını listeleme/detay API ve Studio’da görüntüleme.
- [x] 5. **Doğrulama ve ilerleme:** py_compile/check ve tasklist güncellemesi.

## İlerleme
- 2026-11-18: Görev 1 uygulandı (`learned_models.py`: katman katman bottom-up web araştırması + sentez + her adımda `LearnedEntityKnowledge` kaydı).
- 2026-11-18: Görev 2 uygulandı (`video_generation.py`: öğrenilmiş profil ile sağlayıcı tercih/geri besleme meta; başarıda öğrenme devam ediyor).
- 2026-11-18: Görev 3 uygulandı (`entity_system.instantiate_entity_class`: kök + bottom-up katman/kompozisyon alt düğümleri).
- 2026-11-18: Görev 4 uygulandı (`/api/learned-knowledge/entities/*`, `/api/learned-knowledge/video/`, Studio “Öğrenen Bilgi Modelleri” özeti).
- 2026-11-18: Görev 5 doğrulandı (`py_compile`, `manage.py check` OK).