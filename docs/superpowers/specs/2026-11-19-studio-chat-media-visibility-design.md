# Design (kısa): Studio üretim merkezi, chat ekranında sonuç gösterimi

Tarih: 2026-11-19
Durum: Onaylandı

## Niyet

1. Üretilen video/görüntüler **tek bir yerde üretilsin**: Studio ("Video & Medya", "Görsel İşleri" listeleri).
2. Sonuçlar **hem studio kısmında hem de normal chat ekranında** görünür olmalı.
3. Chat'ten gelen video üretme talebi **studio ile üretilsin**; studio başka bir uygulama olarak ele alınabilir.
4. Chat ekranına **çoklu format (3D model dahil)** dosya yükleme seçeneği geliştirilmeli; sistem ona göre işleyip istenilen yere yerleştirmeli.

## Non-goals / Kaçınma

- Yeni veritabanı tablosu / çift üretim sistemi kurgulanmayacak — mevcut `VideoJob`/`ImageJob` (studio liste kaynağı) tek kaynak.
- Chat içinde ayrı bir üretim alanı yaratılmayacak.

## Yaklaşımlar

**A (seçildi): studio iş tablosu + live studio refresh + chat kartı studio linki**
- `chat_media._job_attachment`: video/image attachment'larına `studio_url = '/studio/?tab=media'` ekle → chat bubble kartlarına "Studio" linki.
- `studio.html`: media tabı aktifken `/api/video/` + `/api/image/` yenilemesini 3–5 sn aralıkla sürdür (poller), chat ile üretilen işler canlı görünür.
- Chat upload `accept` niteliğini çoklu format + 3D uzantılarıyla geliştir.
- Mevcut fonksiyonlar tutulur: chat üretim zaten `video_generation._generate_video_impl` / `image_generation._generate_image_impl` (studio'nun üretim fonksiyonları) ile kullanılıyor → işler studio tablosuna düşer.

**B: chat için tamamen ayrı bir "audit/mirror" veri yolu** — chat'te üretim sonucunu sadece chat verisinde tutar, studio listesine düşürme; mevcut mimariye aykırı, tek kaynak gereksiz kabaca olur.
