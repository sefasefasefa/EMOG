# Chat + Studio Birleşik Üretim ve Medya Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chat ve Studio’yu ortak üretim altyapısında birleştirerek; video üretim/görüntülemeyi `/video`’dan Studio’ya taşımak, 2D AI görsel üretimini eklemek, chat’te çoklu-format dosya ekleme + inline medya gösterimi + üretim tetikleme sağlamak ve belirsiz hedeflerde (özellikle 3D yerleştirme) kullanıcıya sorarak işlem yapan akışı hayata geçirmek.

**Architecture:** Mevcut Django/`agent_app` mimarisi korunur: kanonik kayıtlar `VideoJob`, yeni `ImageJob`, `SceneGenerationJob`/`SceneAsset`/`SceneNode` ve sohbet `Node.meta_data` ekleridir. Studio bağımsız üretim/yönetim yüzeyi olur (video+görsel+3D), `/api/*` servisleri paylaşılır; Chat aynı API/servisleri tetikler, sonuçları izler ve mesaj ekleri olarak inline gösterir. Dosya yüklemeleri sahiplik kontrollü medya kaydı olarak saklanır; hedef/parametre belirsizse chat `conversation.meta_data.pending_action` tabanlı soru-cevap akışıyla kullanıcı seçimi alır (asla varsaymaz).

**Tech Stack:** Python 3.11, Django 4+, SQLite/dev DB, Django templates + vanilla JS (mevcut `chat.html`, `studio.html`), mevcut provider/ffmpeg/imageio desenleri (`video_generation.py`, `scene_generation.py`), `manage.py test`.

**Spec:** `docs/superpowers/specs/2026-11-18-chat-studio-unified-generation-design.md`

## Global Constraints

- Onaylı kararlar: `/video` üretim yüzeyi olmaktan çıkar; video üretim/görüntüleme Studio’da toplanır (`/video/` Studio medya görünümüne redirect). 2D AI görsel üretimi Studio + Chat’te eklenir. Hedef/parametre belirsizse sistem **kullanıcıya sorar ve seçimine göre** ilerler; **asla varsaymaz** (aktif sahneyi bile otomatik seçmez).
- Studio bağımsız uygulama gibi kalır: tüm üretim/görüntüleme Studio’dan yapılabilir; chat tetikleyici+ayna rolündedir. Aynı job/asset kayıtları her iki yüzeyde görünür.
- Chat 3D kapsamı (MVP): (a) `SceneGenerationJob` (`procedural/text_to_3d/image_to_3d`), (b) yüklenen 3D dosyaların hedef sahneye `SceneAsset` (+uygun `SceneNode`) eklenmesi, (c) mevcut/adı belirtilen `EntityClass`’ın seçilen sahneye instantiate edilmesi. İleri sınıf araştırma/oluşturma/düzenleme Studio’da kalır.
- 3D üretim/instantiate veya 3D upload’ta hedef sahne/yerleştirme belirsizse seçenekler: (i) mevcut sahneye yerleştir (seçtir), (ii) yeni sahne oluştur ve yerleştir, (iii) sadece üret/sakla (sahnesiz). Kullanıcı seçmeden işlem yapılmaz.
- Video düzenleme: mevcut edit yetenekleriyle sınırlı; hedef video/talimat eksik/belirsizse sorulur. Hedef önceliği bu sohbet akışındaki video ekleri/sonuçları; kullanıcı isterse kendi videolarından seçim sunulabilir. Tahmin yok.
- Sahiplik/güvenlik: tüm upload/download/job uçları auth + sahiplik + güvenli path/tip/boyut kontrolleriyle korunur. Eski mesajlar/kayıtlar geriye uyumlu çalışır.
- Harici sağlayıcı çıktıları API anahtarı/ağ/kotaya bağlıdır; graceful hata/durum davranışı sağlanır.

## Review Focus

- **Belirsizlikte sessiz işlem:** 3D upload veya 3D üretim/instantiate için sahne/yerleştirme bilgisi eksikken sistem sahne seçmemeli, node oluşturmamalı, varsayılan uygulamamalı; chat’te soru/pending state üretmeli ve yalnızca kullanıcı seçimiyle devam etmeli.
- **Sahiplik bypass:** chat ekleri, image/video indirme ve medya URL’leri başka kullanıcının kaydını/dosyasını açmamalı (ID tahmini, path traversal, yetkisiz node/job erişimi).
- **Eski/boş veri:** `Node.meta_data` veya `conversation.meta_data` yok/eksikken `chat_history` ve render çökmesin; attachments alanı opsiyonel kabul edilmeli.
- **Async/uzun iş uyumu:** üretim tamamlanmadan chat’in yanıt vermesi durumunda sonuç (tamamlandı/başarısız) sonradan chat’te ve Studio’da görünmeli; kayıp sessiz başarı iddiası olmamalı.
- **/video geçişi:** `/video/` üretim UI’sı olarak kalmamalı (Studio’ya yönlenmeli) ancak mevcut video job’larına indirme/listeleme erişimi ve Studio bağımsız üretim kırılmamalı.

## File Structure

- **Modeller/migration:** `agent_app/models.py` (yeni `ImageJob`, yeni `ChatAttachment`), `agent_app/migrations/0018_chatattachment_imagejob.py` (veya makemigrations çıktısı) — medya/job ve chat eki kalıcı kayıtları.
- **2D görsel üretim:** `agent_app/image_generation.py` (config, provider seçimi, job create/run, local/fallback) — Video/Scene generation desenleriyle uyumlu servis.
- **API/views:** `agent_app/image_views.py` (image generate/list/detail/download), `agent_app/video_views.py` (mevcut, korunur), `agent_app/scene_views.py` (gerekirse küçük uyum), `agent_app/views.py` (chat history attachments, assistant_message multipart+media+intent/pending), `agent_app/chat_media.py` (yeni yardımcı: attachment kaydet/serialize/serve-path çözümü, ownership) — endpoint ve sohbet akışı.
- **Routing/nav:** `agent_app/urls.py`, `agent_app/video_page_view.py` (redirect davranışı), `agent_app/templates/agent_app/base.html` — `/video` redirect ve gezinme.
- **UI:** `agent_app/templates/agent_app/chat.html` (ek butonu, FormData gönderim, medya render, polling/soru akışı), `agent_app/templates/agent_app/studio.html` (Video & Medya bölümü: video + 2D görsel üretim/görüntüleme) — kullanıcı yüzeyleri.
- **Testler:** `agent_app/tests_image_generation.py`, `agent_app/tests_chat_media.py` (veya mevcut test dosyalarına ek), mevcut `agent_app/tests_video_*` uyumluluk kontrolleri — davranış doğrulama.

---

### Task 1: ChatAttachment ve ImageJob modelleri + migration

**Files:**
- Modify: `agent_app/models.py`
- Create: `agent_app/migrations/0018_chatattachment_imagejob.py` (makemigrations ile üretilecek)
- Test: `agent_app/tests_chat_media.py`

**Interfaces:**
- Consumes: `User`, `Conversation`, `Node`, `uuid`, `timezone`, Django `FileField`/`JSONField`.
- Produces: `ChatAttachment(user, conversation, node?, kind, name, mime_type, size, file, meta, created_at)` ve `ImageJob` (VideoJob ile paralel: `user,prompt,mode,provider,status,progress,width,height,format,output_path,input_path,thumbnail_path,error,result,meta,created_at/updated_at/started_at/finished_at`, `is_finished`, `download_url` benzeri erişim için path alanları). Sohbet ekleri için sahiplik `attachment.user_id == current_user.id` ve/veya `conversation.app_user_id` ile doğrulanabilir.

- [ ] **Step 1: Write the failing tests**

```python
# agent_app/tests_chat_media.py
import tempfile
from pathlib import Path
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from agent_app.models import User, Conversation, Node, ChatAttachment, ImageJob


class ChatAttachmentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="ca@example.com", username="ca-user")
        self.conv = Conversation.objects.create(app_user=self.user, title="t")

    def test_create_attachment(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(MEDIA_ROOT=d):
                up = SimpleUploadedFile("a.txt", b"hello", content_type="text/plain")
                att = ChatAttachment.objects.create(
                    user=self.user, conversation=self.conv, node=None,
                    kind="file", name="a.txt", mime_type="text/plain", size=5, file=up,
                )
                self.assertEqual(att.user_id, self.user.id)
                self.assertEqual(att.conversation_id, self.conv.id)
                self.assertTrue(att.file.name)


class ImageJobModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="ij@example.com", username="ij-user")

    def test_defaults(self):
        job = ImageJob.objects.create(user=self.user, prompt="cat")
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.progress, 0)
        self.assertEqual(job.format, "png")
        self.assertFalse(job.is_finished)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_chat_media -v 2`
Expected: FAIL (models `ChatAttachment`/`ImageJob` yok veya alanlar eksik)

- [ ] **Step 3: Implement models and upload path helper in `agent_app/models.py`**

  - `chat_attachment_upload_to(instance, filename)` → `chat_uploads/<user_id>/<uuid>_<safe_name>`.
  - `ChatAttachment`: UUID pk, `user` FK(User), `conversation` FK(Conversation), `node` FK(Node, null/blank), `kind` (image/video/file/3d_model/other), `name`, `mime_type`, `size` (int), `file` FileField(upload_to=chat_attachment_upload_to), `meta` JSONField(default=dict), `created_at`.
  - `ImageJob`: VideoJob’a paralel alanlar ve `is_finished` property; `format` default `png`; `width/height` default makul değer (örn. 1024x1024). İndirme yolu için `output_path` tutulur (video ile aynı relative-to-media yaklaşımı).

- [ ] **Step 4: Create and apply migration**

Run: `.\.venv\Scripts\python.exe manage.py makemigrations agent_app -n chatattachment_imagejob`
Run: `.\.venv\Scripts\python.exe manage.py migrate`
Expected: migration oluşur ve uygulanır.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_chat_media -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent_app/models.py agent_app/migrations agent_app/tests_chat_media.py
git commit -m "feat: add chat attachment and image job models"
```

---

### Task 2: 2D görsel üretim servisi (image_generation.py)

**Files:**
- Create: `agent_app/image_generation.py`
- Test: `agent_app/tests_image_generation.py`

**Interfaces:**
- Consumes: `ImageJob`, `settings.IMAGE_GENERATION`, `MEDIA_ROOT`, env API keys (OpenAI/Stability/Replicate/NVIDIA), `django.utils.timezone`.
- Produces: `_cfg()`, `_output_dir()`, `available_providers()`, `get_provider()`, `_generate_image_impl(prompt, user, width, height, fmt, provider, **kw) -> ImageJob`, `_run_generation_impl(job_id, background=False) -> ImageJob`, `generate_image()`/`run_generation()` public wrappers (video_generation ile uyumlu sync/async davranış), `run_generation_background(job_id)`.

- [ ] **Step 1: Write the failing tests**

```python
# agent_app/tests_image_generation.py
import tempfile, asyncio, inspect
from pathlib import Path
from django.test import TestCase, override_settings
from agent_app.models import User, ImageJob
from agent_app import image_generation as ig


class ImageGenerationServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="ig@example.com", username="ig-user")
        self.media_root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.media_root, ignore_errors=True))
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root, IMAGE_GENERATION={"offline": True})
        self.settings_override.enable(); self.addCleanup(self.settings_override.disable)

    def test_generate_creates_job_and_runs_local(self):
        job = ig._generate_image_impl(prompt="a cat", user=self.user, width=64, height=64, fmt="png")
        self.assertIsInstance(job, ImageJob)
        self.assertEqual(job.status, "pending")
        finished = ig._run_generation_impl(str(job.id))
        finished.refresh_from_db()
        self.assertEqual(finished.status, "completed", finished.error)
        self.assertTrue(finished.output_path)
        self.assertTrue((self.media_root / finished.output_path).is_file())

    def test_provider_registry_has_local(self):
        self.assertIn("local", ig.available_providers({"offline": True}))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_image_generation -v 2`
Expected: FAIL (module/functions yok)

- [ ] **Step 3: Implement `agent_app/image_generation.py`**

  - Config: `IMAGE_GENERATION` (enabled, offline, output_dir default `images`, formats `[png,jpg,webp]`, providers/order, boyut limitleri). `_media_root/_output_dir/_upload_dir` media altında.
  - Provider arayüzü ve registry: `openai`, `stability`, `replicate`, `nvidia` (anahtar/yapılandırma ile available), `local` her zaman fallback. Harici sağlayıcılar ağ/anahtar olmadan graceful fail; `offline` modda local seçilir.
  - Local provider: PIL ile basit deterministik görsel (prompt tabanlı renk/başlık) üretip belirtilen formatta kaydet; `output_path` relative (örn. `images/<job_id>.<fmt>`), thumbnail (opsiyonel).
  - Job lifecycle: create (`_generate_image_impl`) pending job; run (`_run_generation_impl`) running→completed/failed, progress, output/result/error, timestamps. `generate_image`/`run_generation` sync/async wrapper (video_generation deseni).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_image_generation -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/image_generation.py agent_app/tests_image_generation.py
git commit -m "feat: add 2D image generation service"
```

---

### Task 3: Image generation API ve routing

**Files:**
- Create: `agent_app/image_views.py`
- Modify: `agent_app/urls.py`
- Test: `agent_app/tests_image_generation.py` (API testleri ekle)

**Interfaces:**
- Consumes: `current_user`, `require_auth`/`require_feature` (uygunsa `image_generation`), `ImageJob`, `image_generation` servisi, `MEDIA_ROOT`.
- Produces: `GET /api/image/` (liste, user-scoped), `POST /api/image/generate/` (job oluştur, kısa işte sync run veya background), `GET /api/image/<id>/` (detay), `GET /api/image/<id>/download/` (sahiplik + güvenli path + `FileResponse`).

- [ ] **Step 1: Write the failing API tests** (aynı test dosyasına ekle)

```python
from django.test import Client
from agent_app.models import User, ImageJob

class ImageApiTests(TestCase):
    def test_list_scoped(self):
        # user auth test client pattern'ine uygun şekilde (app current_user) sahiplik kontrolü
        ...
```

  - (Not: projede auth `agent_app.permissions.current_user` ile; testlerde `RequestFactory` veya uygun login/force yardımcıları kullanılmalı. En azından serializer ve ownership mantığı unit düzeyinde, endpoint’ler RequestFactory ile smoke edilebilir.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_image_generation -v 2`
Expected: FAIL (views/urls yok)

- [ ] **Step 3: Implement `agent_app/image_views.py` and wire `agent_app/urls.py`**

  - `_serialize_job` download_url `/api/image/<id>/download/` ile.
  - Liste yalnızca `request` kullanıcısının job’ları. Detay/download sahiplik kontrolü (404/403 değil, bilgi sızdırmamak için not-found tercih edilebilir — mevcut video deseniyle uyumlu).
  - Download: `output_path` relative resolve, media root dışına çıkış engeli, traversal reddi, content-type.
  - URL’ler: `api/image/generate/`, `api/image/<id>/download/`, `api/image/<id>/`, `api/image/`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_image_generation -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/image_views.py agent_app/urls.py agent_app/tests_image_generation.py
git commit -m "feat: add image generation API endpoints"
```

---

### Task 4: /video konsolidasyonu — redirect ve navigasyon

**Files:**
- Modify: `agent_app/video_page_view.py`
- Modify: `agent_app/urls.py`
- Modify: `agent_app/templates/agent_app/base.html`
- (Opsiyonel) Remove/retire: `agent_app/templates/agent_app/video.html` üretim sayfası kullanımı (Studio’ya taşınacağı için; redirect varsa template referansı kalmayabilir)
- Test: `agent_app/tests_chat_media.py` veya yeni `agent_app/tests_unified_routes.py`

**Interfaces:**
- Consumes: Django `redirect`.
- Produces: `GET /video/` → `302` `/studio/?tab=media` (veya `section=media`); nav’da ayrı “Video” üretim linki Studio medya görünümüne yönlendirir veya Studio etiketiyle sunulur.

- [ ] **Step 1: Write the failing test**

```python
from django.test import TestCase, RequestFactory
from agent_app import urls  # ensure wired

class VideoRedirectTests(TestCase):
    def test_video_page_redirects_to_studio_media(self):
        # auth gereksinimine göre: require_auth varsa authenticated request ile 302 hedefi doğrula
        ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_unified_routes -v 2` (veya seçilen dosya)
Expected: FAIL

- [ ] **Step 3: Implement redirect and nav updates**

  - `video_page_view.video_page`: render yerine `redirect('/studio/?tab=media')` (veya named URL + query).
  - `base.html`: `/video/` menü öğesini Studio medya hedefine güncelle/kaldır (kullanıcı “başka üretim yeri olmasın”).
  - `/api/video/*` endpoint’leri ve `video_views` korunur.

- [ ] **Step 4: Run tests to verify they pass**

Run: ilgili test
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/video_page_view.py agent_app/urls.py agent_app/templates/agent_app/base.html agent_app/tests_unified_routes.py
git commit -m "feat: redirect video page to studio media"
```

---

### Task 5: Studio UI — Video & Medya bölümü (video + 2D görsel)

**Files:**
- Modify: `agent_app/templates/agent_app/studio.html`

**Interfaces:**
- Consumes: `/api/video/*`, `/api/image/*`, mevcut `/api/scenes/*`, `/api/3d-jobs/*` JS fetch desenleri; URL query `tab/section=media`.
- Produces: Studio içinde Video üretim/düzenleme/liste/izleme ve 2D görsel üretim/liste/önizleme/indirme UI; sayfa yüklendiğinde `tab=media` ise ilgili bölüm açılır. Mevcut 3D Studio işlevleri çalışır kalır.

- [ ] **Step 1: Plan the UI integration points** (kod incelemesi: `studio.html` mevcut paneller/sekme yapısı, `video.html` form/polling JS’i referans)
- [ ] **Step 2: Implement media section in `studio.html`**
  - “Video & Medya” paneli/sekmesi ekle: video generate form (prompt, dosya, duration/format/resolution/provider), job list/poll (`/api/video/`, `/api/video/<id>/`), `<video>` preview + download, edit form.
  - 2D image generate form (prompt, boyut/format/provider) + job list/poll (`/api/image/`) + `<img>` preview + download.
  - Query param ile media sekmesini aç; hata/boş durum mesajları.
- [ ] **Step 3: Verify manually/smoke**
  - Run: `.\.venv\Scripts\python.exe manage.py check`
  - Smoke: RequestFactory veya `Client` ile `/studio/?tab=media` 200 (auth bağlamına uygun) ve template render hatası yok; mevcut 3D bölümleri etkilenmemiş.
- [ ] **Step 4: Commit**

```bash
git add agent_app/templates/agent_app/studio.html
git commit -m "feat: add video and image media section to studio"
```

---

### Task 6: Chat medya backend — ek kaydı, serileştirme ve indirme

**Files:**
- Create: `agent_app/chat_media.py`
- Modify: `agent_app/views.py`
- Modify: `agent_app/urls.py`
- Test: `agent_app/tests_chat_media.py`

**Interfaces:**
- Consumes: `ChatAttachment`, `Conversation`, `Node`, `User`, request `FILES`, `MEDIA_ROOT`.
- Produces:
  - `save_chat_attachments(request, user, conversation, node=None) -> list[dict]` (güvenli kayıt + metadata dict: `id,type,name,mime,size,url,status,kind`)
  - `serialize_attachments_for_node(node_or_meta) -> list[dict]` ve `chat_history` yanıtında `attachments` alanı
  - `GET /api/chat/attachments/<att_id>/download/` (sahiplik: attachment.user == current_user veya conversation.app_user == current_user)
  - `assistant_message` multipart: ekleri kaydeder, kullanıcı mesajı `Node.meta_data.attachments` içine yazar (ve özet metni `content`’te tutar), yanıt payload’ında ek bilgisi döner. Geçici/proje-uploads notu yerine (veya ek olarak) sunulabilir kayıt kullanılır.

- [ ] **Step 1: Write the failing tests**

```python
class ChatAttachmentApiAndHistoryTests(TestCase):
    def test_history_includes_attachments_field(self):
        # conversation+node with meta_data.attachments -> chat_history returns attachments list
        ...
    def test_download_forbidden_for_other_user(self):
        # attachment owned by A, request as B -> 404/403
        ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_chat_media -v 2`
Expected: FAIL

- [ ] **Step 3: Implement `chat_media.py`, update `views.py` and `urls.py`**

  - Dosya tipi/boyut doğrulama (görsel, video, audio, 3D uzantıları dahil; limit). 3D uzantıları: `glb,gltf,obj,fbx,stl,zip` (sakla; işleme/yerleştirme ayrı adımda).
  - `chat_history`: her message için `attachments` (boş liste varsayılan).
  - `assistant_message`: `attachments` FILES alır, kaydeder, `Node.meta_data`’ya yazar; yanıt JSON’unda `attachments` ve/veya kullanıcı mesajı eko verisi döndürür (frontend’in göstermesi için).
  - Download view: path traversal koruması, `FileResponse`, doğru content-type.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_chat_media -v 2`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_app/chat_media.py agent_app/views.py agent_app/urls.py agent_app/tests_chat_media.py
git commit -m "feat: persist and serve chat attachments with history metadata"
```

---

### Task 7: Chat UI — dosya ekleme ve medya render

**Files:**
- Modify: `agent_app/templates/agent_app/chat.html`

**Interfaces:**
- Consumes: `POST /assistant/message/` (multipart FormData: `message`, `attachments[]`), `GET /chat/history/` (`messages[].attachments`), attachment `url/download_url`, job download URL’leri.
- Produces: ek butonu + dosya seçici (multiple, çoklu format), seçili dosya önizleme/çıkarma, multipart gönderim; history ve yeni mesajlarda metinle birlikte ek render: görsel `<img>`, video `<video controls>`, 3D/dosya kartı (ad, boyut, indir, varsa Studio/sahne linki). Eski mesajlar (attachments yok) bozulmaz.

- [ ] **Step 1: Implement attachment picker and FormData send** (mevcut `sendMessage` akışını genişlet; metin-only path geriye uyumlu kalsın)
- [ ] **Step 2: Implement attachment rendering in `renderBubble`/history load** (`attachments` array’ini güvenle oku; unknown type → dosya kartı)
- [ ] **Step 3: Verify**
  - `manage.py check`
  - Manuel/smoke: chat sayfası yüklenir, ek seçilip gönderildiğinde istek multipart gider, history’de attachment alanı işlenir.
- [ ] **Step 4: Commit**

```bash
git add agent_app/templates/agent_app/chat.html
git commit -m "feat: add chat file attachments and inline media rendering"
```

---

### Task 8: Chat üretim tetikleme, sonuç yansıtma ve belirsizlikte soru akışı

**Files:**
- Modify: `agent_app/views.py`
- Modify: `agent_app/templates/agent_app/chat.html`
- Modify: `agent_app/chat_media.py` (gerekirse yardımcılar)
- Test: `agent_app/tests_chat_media.py`

**Interfaces:**
- Consumes: `video_generation`/`video_views` servisleri, `image_generation`/`image_views`, `scene_generation`/`scene_views`/`entity_system`, `Conversation.meta_data` (`pending_action`), `ChatAttachment`, `Scene`, `EntityClass`, `VideoJob`, `ImageJob`.
- Produces:
  - **Intent/aksiyonlar:** video üret (`VideoJob`), 2D görsel üret (`ImageJob`), 3D üretim job (`SceneGenerationJob`), yüklenen 3D’ü sahneye ekleme (`SceneAsset`+`SceneNode`), mevcut `EntityClass` instantiate. Sohbet aynı backend’i çağırır; oluşan kayıtlar Studio’da görünür.
  - **Zorunlu soru akışı (karar 3):** eksik hedef/parametre varsa `assistant_message` kullanıcıya seçenekli soru döner ve `conversation.meta_data.pending_action` kaydeder. Sonraki kullanıcı mesajı pending action’a yanıt olarak çözümlenir (seçim/serbest metin eşlemesi makul şekilde); **kullanıcı seçimi olmadan** sahne/yer/hedef uygulanmaz. 3D upload/generation belirsizliğinde seçenekler: (i) mevcut sahneye yerleştir (seçtir), (ii) yeni sahne oluştur ve yerleştir, (iii) sadece üret/sakla (sahnesiz). Sahne seçimi sunulurken kullanıcıya ait sahneler listelenir.
  - **Video düzenleme:** hedef/talimat belirsizse sor; hedef önceliği bu sohbet akışındaki video ekleri/sonuçları, kullanıcı isterse videolarından seçim. Tahmin yok.
  - **Chat’e sonuç:** tamamlanan işler için asistan mesajına medya attachment eklenir (`job_id/job_kind/download_url/thumbnail`); async işler için durum bilgisi döner ve frontend polling ile güncelleyip sonucu inline gösterir.

- [ ] **Step 1: Write failing tests for pending-action/question behavior**

```python
def test_3d_upload_without_scene_creates_pending_question_not_placement(self):
    # attach .glb without scene info -> response asks user, no SceneAsset/SceneNode created, pending_action set
    ...

def test_pending_action_resolved_by_user_choice_places_asset(self):
    # set pending_action, user replies with choice -> asset/node created in chosen scene
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_chat_media -v 2`
Expected: FAIL

- [ ] **Step 3: Implement pending action state, question/answer resolution, and generation/placement handlers in `views.py` (and helpers)**

  - Pending action şeması: `{id,type,created_at,params,missing,options,source_attachments,...}`; yanıt eşleştirme: numara/seçenek id/isim veya açık ifade.
  - Kullanıcının sahneleri `Scene.objects.filter(user=user)` ile listelenir (seçenek üretimi ve doğrulama).
  - “Yeni sahne oluştur”: ad sorulmadıysa varsayılan adla oluşturulabilir **ancak sadece kullanıcı bu seçeneği seçtiğinde** (karar kullanıcıdan gelir).
  - 3D job sahnesiz seçiminde `scene_id=None` ile job/asset üretimi desteklenir (mevcut scene_generation davranışına uygun).
  - Video/image async: job id + poll endpoint bilgisini chat yanıtına ekle; completion’da assistant mesajına medya attachment yazan yardımcı (senkron tamamlamada doğrudan ekle).

- [ ] **Step 4: Implement frontend polling and pending-question UX in `chat.html`**

  - Asistan sorusunu/opsiyonlarını göster; kullanıcı yanıtını normal mesaj olarak gönderir (backend pending’i çözer).
  - `pendingJobs` poll: periyodik `/api/video/<id>`, `/api/image/<id>`, `/api/3d-jobs/<id>`; tamamlanınca inline medya ekle/güncelle (Studio’da aynı kayıt zaten görünür).

- [ ] **Step 5: Run tests and smoke**

Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_chat_media agent_app.tests_image_generation -v 2`
Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_video_job agent_app.tests_video_generation -v 1` (uyumluluk)
Run: `.\.venv\Scripts\python.exe manage.py check`
Expected: PASS / 0 issue

- [ ] **Step 6: Commit**

```bash
git add agent_app/views.py agent_app/chat_media.py agent_app/templates/agent_app/chat.html agent_app/tests_chat_media.py
git commit -m "feat: chat-driven generation, mirroring, and user-confirmed placement flow"
```

---

### Task 9: Uçtan uca doğrulama ve dokümantasyon notları

**Files:**
- Modify (gerekirse): `docs/superpowers/specs/2026-11-18-chat-studio-unified-generation-design.md` (yalnızca implementasyon sırasında sabitlenen küçük açıklamalar)
- Modify (gerekirse): `docs/training-pipeline.md` veya ilgili notlar (yok; bu özellik için gerekmiyorsa dokunma)

- [ ] **Step 1: Full verification**

Run: `.\.venv\Scripts\python.exe -m py_compile agent_app/models.py agent_app/views.py agent_app/urls.py agent_app/chat_media.py agent_app/image_generation.py agent_app/image_views.py agent_app/video_views.py agent_app/video_page_view.py agent_app/scene_generation.py agent_app/scene_views.py`
Run: `.\.venv\Scripts\python.exe manage.py check`
Run: `.\.venv\Scripts\python.exe manage.py test agent_app.tests_chat_media agent_app.tests_image_generation agent_app.tests_video_job agent_app.tests_video_generation agent_app.tests_scene_features -v 1`
Expected: compile OK, 0 issue, ilgili testler PASS

- [ ] **Step 2: Targeted smoke (Django test client / RequestFactory)**

  - `/video/` → 302 `/studio/?tab=media` (auth’a uygun).
  - `chat_history` eski mesajda `attachments` anahtarı güvenli (boş liste/absent toleransı render tarafında).
  - 3D upload belirsiz hedef: placement yapılmaz, pending/question üretilir (Task 8 testleri kanıtlar).
  - Studio bağımsız: `/api/video/*`, `/api/image/*`, `/api/3d-jobs/*` mevcut akışlarla çalışır.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: verify unified chat-studio generation flows"
```

## Self-Review

- **Spec coverage:** Karar 1 (video Studio’da, `/video` redirect) — Task 4,5. Karar 2 (2D image gen Studio+Chat) — Task 1,2,3,5,7,8. Karar 3 (belirsizlikte sor, varsayma; 3D seçenekleri i/ii/iii) — Task 6,8 ve Review Focus. Chat inline medya — Task 6,7,8. Ortak backend, Studio bağımsız — Task 2,3,4,5,8. 3D chat kapsamı (job/upload-place/instantiate, class authoring hariç) — Task 8. Video edit hedefleme — Task 8.
- **Type/consistency:** `ChatAttachment`/`ImageJob` alanları Task 1’de tanımlı ve sonraki task’lerce kullanılır; API yolları Task 3,4,6,8’de tutarlı (`/api/image/*`, `/api/chat/attachments/<id>/download/`, `/api/video/*` korunur). `pending_action` conversation.meta_data’da dict şema ile taşınır.
- **Review Focus coverage:** Belirsizlikte sessiz işlem → Task 8 testleri; sahiplik → Task 3,6,8 kontrolleri; eski/boş veri → Task 6,7 render/history; async sonuç görünürlüğü → Task 7,8 polling/ekleme; `/video` geçişi → Task 4,5 ve Task 9 uyumluluk testleri. Her biri owning task’ta adım/test ile ele alınır.
- **Proportion/gaps:** Plan, özellikle Task 5 (Studio template refactor) ve Task 8 (intent/pending akışı) için imza/arayüz ve doğrulama adımlarını sabitler; tam JS/ifade gövdeleri implementasyona bırakılmıştır (belirsizliği imza ve davranış testleriyle sınırlar). Yeni bir spec gereksinimi tespit edilirse ilgili task’a adım eklenmelidir.
