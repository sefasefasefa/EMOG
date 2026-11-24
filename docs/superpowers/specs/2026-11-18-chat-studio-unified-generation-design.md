# Chat + Studio Birleşik Üretim ve Medya Tasarımı

**Tarih:** 2026-11-18  
**Durum:** Yön onaylandı (kullanıcı: "onaylıyorum"); bu doküman kapsam onayı için sunulmuştur.  
**Bağlam:** EMOG (Django, `agent_app`) — mevcut Chat (`/chat/`), Video (`/video/`, `VideoJob`), 3D Scene Studio (`/studio/`, `Scene`/`SceneAsset`/`SceneNode`/`SceneGenerationJob`).

## 1. Amaç ve Neden
- **Kullanıcı kolaylığı:** Üretilen video ve görseller (ve ilgili medya/asset’ler) ayrı yerlere gitmeden **normal chat ekranında** görülebilsin; chat’ten üretim/ekleme yapılabilsin.
- **Studio bağımsızlığı:** Studio her zaman kendi başına üretim ve görüntüleme yapabilen ayrı bir uygulama gibi ele alınabilsin.
- **Tek üretim/yönetim modeli:** Aynı işler/kayıtlar Studio’da üretilip görünürken chat’e de yansıtılır; kullanıcı dağınık üretim yüzeyleriyle uğraşmaz.
- **Dosya ingest:** Chat’ten çoklu format dosya (görsel, video, ses, 3D modeller dahil) eklenebilsin; sistem dosyayı işleyip kullanıcı isteğine göre uygun yere koyabilsin (belirsizlikte kullanıcıya sorarak).

## 2. Onaylanan Kararlar (değişmez)
1. **`/video` kaldırılır, video üretim ve görüntüleme Studio’da toplanır.** `/video` route/uç yüzeyi üretim için kullanılmaz; kullanıcıyı Studio’daki ilgili medya/video bölümüne yönlendirecek şekilde ele alınır (route redirect veya kaldırma + nav/link güncellemesi; tasarım tercihi: `/video/` → Studio medya görünümüne redirect, eski deep-link kırılmasın).
2. **2D AI görsel üretimi eklenir** (Studio + Chat’ten tetiklenebilir, aynı backend/sonuç görünürlüğü).
3. **Belirsizlikte sistem kullanıcıya sorar, kafasından karar vermez.** Özellikle chat’te 3D dosya yüklenip hedef sahne belirtilmemişse (ve benzeri gerekli bilgi eksikse) sistem seçenekleri sorar ve **kullanıcının seçimine göre** işlem yapar.

> Ek ilke: “Chat’te üretim talebi gelirse iş Studio’da üretilip orada görünür ve aynı zamanda chat’e gönderilir.” Chat tetikleyici+ayna; Studio üretim/yönetim yüzeyidir.

## 3. Hedefler
- Video/görsel/ilgili üretim çıktılarının chat mesaj akışında inline görünmesi (oynatıcı/önizleme/indirme/kart).
- Chat’ten: video üretme, 2D görsel üretme, 3D üretim/instantiate tetikleme ve dosya ekleme/yönlendirme.
- Studio’nun bağımsız olarak video + 2D görsel + 3D üretim/görüntüleme yapabilmesi.
- Aynı job/asset kayıtlarının her iki yüzeyde tutarlı görünmesi (tek gerçek kaynak: DB + medya dosyaları).
- Çoklu format dosya ekleme ve kullanıcı yönlendirmeli yerleştirme (3D → sahne asset/node vb.).
- Sahiplik, yetki ve güvenli dosya erişimi korunur/genişletilir.

## 4. Kapsam Dışı / Hedef Dışı (Non-goals)
- Gerçek zamanlı çok kullanıcılı eşzamanlı düzenleme (collab) bu tasarımın kapsamı değil.
- Harici sağlayıcı API anahtarları, kota ve ağ erişimi olmadan gerçek harici üretim garantisi (entegrasyon ve yapılandırma sağlanır; kullanılabilirlik anahtara bağlıdır).
- Sunucu tarafı native fizik motoru veya tam skinned-mesh vertex-weight rig üretimi (mevcut/ayrı kapsam).
- Tam ML ağırlık fine-tune çalıştırılması (mevcut export/scaffold dışında).

## 5. Anti-goals (başarı sayılamayacak durumlar)
- Chat veya Studio’nun diğerini zorunlu kılması (Studio bağımsızlığının bozulması).
- Hedef/yer belirsizken sistemin sessizce sahne seçmesi, dosyayı yanlış yere koyması veya kullanıcıyı atlayarak işlem yapması.
- Medya/asset erişiminin sahiplik kontrolünü bypass etmesi (yetkisiz görüntüleme/indirme).
- `/video`’nun ayrı bir üretim yüzeyi olarak yaşamaya devam etmesi.

## 6. Kısıtlar ve Mevcut Durum (özet)
- Chat metin tabanlı: `Node(role, content, meta_data, ...)`, `chat_history` → `role/content/ts`, `chat.html` markdown-only render; `assistant_message` multipart ekleri proje `uploads/` altına kaydedip mesaj metnine not düşüyor, sunulabilir URL/inline medya yok.
- Video: `VideoJob` + `video_generation.py` + `/api/video/{generate,upload,list,detail,download,edit}` + ayrı `/video/` UI; indirme sahiplik kontrollü.
- 3D/Studio: `Scene*`, `Entity*`, `SceneGenerationJob`, `SceneAsset(file -> media scenes/...)`, `/api/scenes/*`, `/api/3d-jobs/*`, `/studio/` UI; glTF upload auto-node, 3D job’lar asset/node üretip sahneye yerleştirir.
- 2D AI image generation mevcut değil. Auth: `current_user`/`@require_auth`/feature gating; medya sunumu `MEDIA_ROOT`/`MEDIA_URL` yapılandırmasına ve özel download endpoint’lerine bağlı.

## 7. Değerlendirilen Yaklaşımlar
| Yaklaşım | Özet | Değerlendirme |
|---|---|---|
| A. Chat’e taşı (eski) | Tüm üretim chat’te, Studio sadece görüntüleme | **Reddedildi:** Studio bağımsızlığı ve “üretim Studio’da yapılacak” kararına aykırı. |
| B. Ayrı yüzeyler ayrı kalsın | /video ve studio üretime devam, sadece chat’te göster | **Reddedildi:** “başka üretimde/dağınık yüzey” ve tek yönetim hedefi karşılanmaz. |
| **C. Hibrit (seçilen): Studio kanonik üretim/yönetim, Chat tetikleyici+ayna** | Ortak backend/job+asset; Studio bağımsız üretir/gösterir; chat tetikler ve aynı sonuçları inline gösterir; /video Studio’ya konsolide edilir; belirsizlikte chat sorar | **Kabul:** Tüm kararları karşılar, Studio ayrı uygulama olarak kalabilir. |

## 8. Tasarım

### 8.1 Studio Konsolidasyonu (Video + 2D Görsel + mevcut 3D)
- **Video UI/API’nin Studio’ya taşınması:**
  - Studio UI’ye “Video & Medya” (veya eşdeğer) bölüm/panel eklenir: video üretim formu (prompt, opsiyonel girdi dosyası, süre/çözünürlük/format/provider/mod), job listesi, durum/progress, oynatma (`/api/video/<id>/download/`) ve düzenleme (`/api/video/<id>/edit/`) — mevcut `video.html` davranışı referans alınır.
  - `/api/video/*` endpoint’leri korunur (backend kanonik), Studio bu API’leri kullanır. `/video/` sayfası üretim yüzeyi olmaktan çıkar: **redirect → `/studio/?tab=media`** (veya eşdeğer) ve nav’da “Video” ayrı üretim sayfası yerine Studio medya görünümüne işaret eder/gizlenir (kullanıcı deneyimi: başka üretim yeri yok). Eski `video_page` template’i kaldırılabilir veya redirect-only bırakılır.
- **3D:** `/studio/` içindeki mevcut sahne/asset/node/3D-job üretim yetenekleri olduğu gibi kalır (Studio bağımsız).
- **Görünürlük:** Video ve 2D görsel çıktıları Studio medya bölümünde listelenir (kullanıcıya ait job’lar); 3D çıktılar ilgili sahnede görünmeye devam eder.

### 8.2 2D AI Görsel Üretimi (yeni)
- **Model:** `ImageJob` (öneri; `VideoJob` ile paralel alanlar): `id(UUID)`, `user(FK)`, `prompt`, `mode` (örn. `generate`, `edit`/`variation` — MVP’de `generate`), `provider`, `status` (`pending|queued|running|processing|completed|failed`), `progress`, `width/height/format` (png/jpg/webp), `output_path`, `input_path` (opsiyonel), `thumbnail_path`, `error`, `result/meta` (JSON), timestamps. Sahiplik `user` üzerinden.
- **Servis:** `agent_app/image_generation.py` (provider soyutlaması, `video_generation.py`/`scene_generation.py` desenleriyle uyumlu): config (`settings.IMAGE_GENERATION`), output/upload dizinleri (`MEDIA_ROOT/images` vb.), provider seçimi ve job çalıştırma (sync/async/background).
  - **Provider’lar (hedef set, kullanılabilirlik anahtara/ortama bağlı):** `openai` (Images API, ör. gpt-image/dall-e model ailesi — env: `OPENAI_API_KEY`), `stability`, `replicate`, `nvidia` (varsa uygun endpoint/LLM destekli akış; yoksa atla), `local` fallback (anahtar yoksa: basit placeholder/jeneratif fallback veya “harici sağlayıcı gerekli” hatası — implementasyon kararını builder netleştirir; en azından graceful failure/mesaj). Gerçek harici çıktı için anahtar/ağ gerekir.
- **API:** `/api/image/generate/` (POST), `/api/image/` (GET list), `/api/image/<id>/` (GET detail), `/api/image/<id>/download/` (GET, sahiplik + path güvenliği, `FileResponse`), gerekirse `/api/image/upload/` ve `/api/image/<id>/edit/` (MVP’de generate+download yeterli olabilir; tasarım edit’i genişlemeye açık tutar). Feature flag: `image_generation` (öneri).
- **Studio UI:** Medya bölümünde görsel üretim formu + job kartları/galeri ve indirme/önizleme.
- **Migration:** `ImageJob` için yeni migration.

### 8.3 Chat Medya Modeli, Geçmiş ve Render
- **Mesaj ekleri/sonuçları:** `Node.meta_data` içinde yapılandırılmış medya alanı (örn. `meta_data["attachments"]` listesi). Her öğe minimum: `type` (`image|video|file|3d_model|scene_asset`), `name`, `mime`, `size`, `status` (`uploading|ready|processing|completed|failed`), ve kaynak referansları: `url` veya `download_url` (sunulabilir/sahiplik uyumlu), `job_id`+`job_kind` (`video|image|3d`), `scene_id`/`asset_id`/`node_id` (3D/scene bağlamı), `thumbnail_url` (varsa), `error`.
  - Uygulama notu: yalnızca `content` string’ine gömmek yerine yapılandırılmış taşıma tercih edilir; geriye uyum için `content` metni korunur/özetlenir.
- **API/Geçmiş:**
  - `chat_history` her mesaj için `attachments`/`media` döndürür; frontend geçmişi render ederken kullanır.
  - `assistant_message` ve ilgili akışlar ekli dosyaları kabul eder, job/asset üretince ilişkilendirir, anlık/nihai mesajlarda medya bilgisini döndürür.
  - `_append_chat_message` yardımcıları medya metadata’sı yazabilir hale getirilir.
- **Frontend (`chat.html`):** Composer’a dosya ekleme butonu + çoklu seçim + seçili dosya chip/listesi; `FormData`/multipart gönderim. `renderBubble` (veya render pipeline) metin + ekleri işler: resim → `<img>` (lazy, tıklanınca büyütme opsiyonel), video → `<video controls>` + indirme, 3D/dosya → kart (ad, tür, boyut, indirme, varsa “Studio’da aç” linki). Aynı-origin oturum ile sahiplik kontrollü download endpoint’leri kullanılabilir; gerekirse medya erişimi için mevcut download/proxy desenleri izlenir.

### 8.4 Chat Dosya Yükleme, İşleme ve Yerleştirme (Karar 3)
- **Yükleme:** Chat multipart ekleri güvenli depoya alınır (öneri: `MEDIA_ROOT/chat_uploads/<user_id>/<conversation_or_date>/...` veya kayıtlı `ChatAttachment`/mevcut `SceneAsset` uyumlu yapı — builder mevcut `SceneAsset` ve video upload desenleriyle en az sürtünmeli yolu seçer; önemli olan: **sahiplik**, **sunulabilir URL/endpoint**, **tip/boyut doğrulama**).
- **Tip/format desteği (örnek, genişletilebilir):** görsel (`png,jpg,jpeg,webp,gif`), video (`mp4,webm,mov`), 3D (`glb,gltf,obj,fbx,stl,zip` — desteklenenler sunucu/Studio yeteneğine göre; desteklenmeyenler dosya olarak saklanır ve kullanıcı bilgilendirilir), ses/belge (en az dosya kartı). Boyut sınırları ayarlanabilir.
- **Niyet ve yönlendirme:** Kullanıcı mesajı + ek türü ile işlem belirlenir (sohbete ekle/göster, video üretim/düzenleme girdisi, 2D üretim girdisi, 3D image-to-3d girdisi, sahneye 3D asset olarak ekle, vb.). 
- **Zorunlu soru sorma kuralı (onaylı):** Gerekli bilgi (özellikle **hedef sahne**, yerleştirme modu, üretim türü/mod, hedef kayıt) eksik veya muğlaksa sistem **chat içinde açık soru ve seçenekler sunar** ve kullanıcı yanıtına göre devam eder. Asla varsayılan sahne/kayıt seçmez, sessizce yerleştirmez veya tahmin etmez.
  - **3D dosya yükleme, sahne belirtilmemiş:** (i) kullanıcıya ait mevcut sahnelerden seçim sun, (ii) “Yeni sahne oluştur” sun (gerekirse ad sor), (iii) “Sadece dosya olarak ekle (sahneye koyma)” sun. Aktif/son sahne olsa bile **otomatik seçilmez** (seçenek olarak gösterilebilir, karar kullanıcıdadır).
  - **Chat’ten 3D üretim/instantiate, hedef sahne veya yerleştirme belirsiz:** aynı ilke geçerlidir — (i) mevcut sahneye yerleştir (seçtir), (ii) yeni sahne oluştur ve yerleştir, (iii) **sahneye yerleştirme, sadece üret/sakla** (sahnesiz asset/job; `scene_id` olmadan). Hiçbiri varsayılmaz; kullanıcı seçer.
  - **Çoklu sahne/çoklu dosya/belirsiz hedef:** her belirsiz öğe için gerekli seçim alınır (tek mesajda toplu soru veya adım adım; UX kararı builder’a, kural korunarak).
- **Yerleştirme sonuçları:** Sahneye ekleme seçilirse `SceneAsset` (+ glTF/uygun türde `SceneNode`, mevcut `scene_assets_api` davranışıyla uyumlu) oluşturulur; sonuç hem Studio sahnesinde hem chat’te referans/önizleme ile görünür. Sahnesiz üretim seçilirse çıktı/asset kayıt olarak saklanır ve Studio ile chat’te görünür (sahne içi node olmadan).

### 8.5 Chat’ten Üretim Tetikleme ve Studio ile Senkron Görünürlük
- **Intent/routing (`assistant_message`):** Mevcut `chat|execute|build|health|models` karar mekanizması genişletilir (veya araç/aksiyon katmanı eklenir) — video üret/düzenle, 2D görsel üret, 3D üretim/instantiate/sahneye ekleme isteklerini tanır. Gerekli parametreler (prompt, hedef sahne, sınıf, mod, dosya, hedef kayıt/talimat) eksikse kullanıcıya soru döner.
- **Chat’ten tetiklenecek 3D kapsamı (onaylı, MVP):**
  - (a) **3D üretim job’ları:** `SceneGenerationJob` ile mevcut akışlar (`procedural` / `text_to_3d` / `image_to_3d`, mevcut provider/modlar).
  - (b) **Yüklenen 3D dosyaların yerleştirilmesi:** hedef sahneye `SceneAsset` (+ uygun `SceneNode`) ekleme.
  - (c) **Instantiate:** mevcut/adı belirtilen bir `EntityClass`’ın seçilen sahneye örneklenmesi (mevcut instantiate akışı).
  - **Kapsam dışı (chat için):** ileri sınıf **araştırma/oluşturma/düzenleme** gibi authoring işlemleri Studio’da kalır; ancak üretilen/var olan sonuçlar ve kayıtlar her iki yüzeyde görünür. Eksik parametreler (hedef sahne, mod, prompt/girdi, sınıf, yerleştirme) chat tarafından sorulur (bkz. 8.4).
- **Video düzenleme hedefleme ve kapsamı (onaylı):** Düzenleme mevcut `/api/video/<id>/edit/` yetenekleriyle sınırlıdır. Hedef video veya talimat eksik/belirsizse chat sorar ve kullanıcı seçimine göre ilerler; hedefi tahmin etmez. Hedef seçiminde öncelik **bu sohbet akışındaki** ilgili video ekleri/sonuçlarıdır; kullanıcı açıkça isterse kendi mevcut videolarından seçim de sunulabilir.
- **Job oluşturma:** Chat aynı create/run endpoint’lerini veya servis fonksiyonlarını çağırır; `VideoJob`/`ImageJob`/`SceneGenerationJob` DB’ye yazılır → Studio listeleri/scene görünümü doğal olarak güncel veriyi gösterir.
- **Sonucun chat’e gönderimi:**
  - Kısa/senkron tamamlanan işlerde: yanıt içinde medya sonucu.
  - Asenkron işlerde: chat’e durum mesajı (job id/kind) eklenir; frontend polling (`/api/video/<id>`, `/api/image/<id>`, `/api/3d-jobs/<id>`) ve/veya mevcut SSE/task akışı ile tamamlanmayı izler, tamamlanınca mesajı/ekleri günceller veya yeni asistan mesajı ekler. (Kesin mekanizma builder implementasyon detayı; gereksinim: sonuç chat’te görünür.)
- **Studio bağımsızlığı:** Studio UI kendi formlarından aynı API/servislerle üretime devam eder; chat kapalıyken de işler ve sonuçlar Studio’da görünür.

### 8.6 Navigasyon, Route ve Geriye Uyumluluk
- `/video/` → Studio medya görünümüne redirect (tercih) veya kaldırma; `base.html` ve diğer linkler güncellenir (“Video” üretim sayfası yerine Studio medya).
- `assistant_message`/chat history şeması geriye dönük uyumlu genişler (attachments opsiyonel). Eski mesajlar attachments olmadan render edilir.
- Feature flag’ler: `video_generation` (mevcut), `image_generation` (yeni, öneri), 3D/studio ilgili mevcut kontroller.

### 8.7 Güvenlik ve Doğrulama
- Tüm üretim/upload/download uçları auth + sahiplik kontrolü (`current_user`, `job.user`, `asset.user/scene.user`) ile korunur.
- Dosya yükleme: uzantı/MIME allowlist, boyut limiti, güvenli dosya adı/path traversal koruması, saklama yolu kullanıcı kapsamlı.
- Chat inline medya: aynı-origin sahiplik kontrollü endpoint/URL; yetkisiz erişim engellenir.
- Kullanıcı girdisi (prompt, dosya) mevcut güvenlik/doğrulama katmanlarıyla uyumlu işlenir.

## 9. Veri/Arayüz Özeti (hedef)
- **Yeni/Değişen modeller:** `ImageJob` (yeni), `Node.meta_data.attachments` sözleşmesi (şema genişlemesi), opsiyonel `ChatAttachment` (builder kararı).
- **Yeni/Değişen endpoint’ler:** `/api/image/*` (yeni); `/video/` redirect; `chat_history` + `assistant_message` medya/ek desteği; gerekirse chat-özel upload/attachment serve endpoint’i.
- **UI:** `studio.html` (medya bölümü: video+image, `/video` işlevi), `chat.html` (ek butonu, multipart, medya render, soru/aksiyon akışı), `base.html` nav.

## 10. Doğrulama Yaklaşımı (uygulama sırasında)
- Unit/integration: `ImageJob`/`image_generation` servis ve provider seçimi (mock ağ), `/api/image/*`, chat history/attachments serileştirme, dosya tipi/boyut/sahiplik kontrolleri.
- Akış: chat’ten video/2D üretim → job oluşur, Studio listesinde görünür, chat’te sonuç görünür; Studio’dan bağımsız üretim çalışır.
- Karar 3: sahnesiz 3D upload → chat soru sorar, kullanıcı seçimi olmadan yerleştirme yapmaz; seçim sonrası doğru sahneye asset/node eklenir ve her iki yerde görünür.
- `/video/` redirect ve nav, geriye uyumlu eski mesaj render.
- `manage.py check`, ilgili testler, hedefli manuel smoke.

## 11. Açık Uygulama Kararları (builder’a bırakılan, yönü bu dokümanla sabit)
- Chat attachment depolama yapısı (ayrı model vs `SceneAsset`/dosya referansı) — güvenli URL ve sahiplik şartlarıyla.
- 2D `local` fallback’in tam davranışı (placeholder üretim mi, yoksa yapılandırma hatası mesajı mı) ve NVIDIA image yolu kullanılabilirliği.
- Asenkron sonuç bildirimi için polling mi SSE mi (veya ikisi) — “sonuç chat’te görünür” şartını sağlayacak şekilde.
- Studio medya bölümü yerleşimi (sekme/panel) ve `/video` redirect hedefi (`?tab=media` vb.).
- `obj/fbx/stl/zip` gibi 3D formatların SceneAsset/önizleme desteği düzeyi (en az güvenli saklama+kart; dönüştürme varsa ayrıca belirtilir).

## 12. Riskler ve Azaltım
- **Kapsam büyüklüğü:** Aşamalı uygulama (plan’da bağımsız, test edilebilir adımlar) ve her adımda doğrulama.
- **Medya erişimi/auth:** Download endpoint’leri ve aynı-origin oturum ile test; doğrudan media URL bağımlılığını minimize et.
- **Yanlış yerleştirme:** Karar 3 kuralı + test senaryoları ile zorunlu kıl.
- **Provider bağımlılığı:** Graceful degrade, açık hata/durum mesajı, local/fallback tanımı.
- **Geriye uyumluluk:** Attachments opsiyonel, eski kayıtlar bozulmaz.
