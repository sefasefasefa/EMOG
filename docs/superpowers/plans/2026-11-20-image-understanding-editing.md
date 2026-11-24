# Plan: Görsel Anlama (Vision) + Görsel Düzenleme (Image Editing)

Tarih: 2026-11-20
Durum: Uygulama devam ediyor

## Kullanıcı İsteği
Sistemin resimleri okuyup yorumlayabilmesi ve gerekirse düzenleyebilmesi.

## Gerçeklik / Sınırlama
NVIDIA hesabında multimodal (vision) LLM etkin değil (tüm vision/omni modelleri 400/422 döndürür). Dolayısıyla gerçek LLM-görsel anlama mevcut değil. Yerine:
- **Görsel anlama**: Pillow (PIL) ile deterministik analiz — format, boyut, renk dağılımı, parlaklık, yön, kırpma alanı.
- **Görsel düzenleme**: Pillow ile yerel düzenleme — boyutlandırma, kırpma, döndürme, aynalama, gri-ton, bulanıklık, parlaklık/kontrast, metin filigranı. Dış API gerektirmez.

## Global Constraints
- Yeni DB tablosu yok.
- Mevcut dosya yapısı bozulmayacak.
- NVIDIA vision modeli yok → PIL tabanlı çözüm.
- İstemci JS değişiklikleri syntax check ile doğrulanacak.

## Görevler

### Task 1: Görsel anlama iyileştirmesi (chat_media.py)
`_pil_summary_for_attachment` genişletilsin: format, boyut, mod, piksel sayısı, renk dağılımı (top 6), ortalama parlaklık, renklilik/mono karar, yön (geniş/yüksek/kare). `maybe_handle_chat_vision_analysis` içindeki PIL fallback yanıtı bu özetle dönsün.

### Task 2: Görsel düzenleme — PIL tabanlı (chat_media.py)
Yeni işlevler:
- `_pil_edit_image(image_bytes, instruction)` — instruction'a göre düzenleme: `küçült/boyutlandır` (width/height), `kırp` (left/top/right/bottom veya yüzde), `döndür` (derece), `ayna` (yatay/dikey), `gri` (grayscale), `bulanıklık` (blur), `parlaklık` / `kontrast` (faktor), `metin ekle` (text overlay), `çevir` (format dönüşüm png/jpg/webp).
- Düzenlenen görüntü yeni `ChatAttachment` olarak kaydedilsin ve chat yanıtına eklensin.

### Task 3: Düzenleme niyet yönlendirmesi (views.py)
`assistant_message` akışına: "resmi düzenle/küçült/döndür/gri yap" gibi kalıplar → image edit intent → `_run_image_edit` → yanıtta düzenlenmiş görüntü attachment'ı.

### Task 4: Test + doğrulama
- `manage.py check` geçsin.
- Yeni işlevler birim testi ile (PIL analyze + edit) doğrulsun.
- 4/intended dosya clean commitlensin.

## Review Focus
- Görsel analiz çıktısı boş mu?
- Düzenleme işlemi geçerli bir PNG/JPG üretir mi?
- Eski akışlar (chat, video, studio) bozulmuş mu?

---
Plan onaylandı; subagent-driven-development ile görevler sırayla uygulanacak.