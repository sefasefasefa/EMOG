# Video Üretimi — EMOG Tasarımı

Tarih: 2026-11-18

## Amaç

EMOG'a prompt'tan video üretebilme yeteneği eklemek. Birincil olarak AI text-to-video
sağlayıcıları (OpenAI Sora, Stability, Replicate, NVIDIA NIM) kullanılacak; API anahtarı
yoksa veya sağlayıcı hata verirse yerel slideshow üreticisine (PIL + imageio-ffmpeg) düşülecek.

## Kapsam

- Yeni `video_gen` Django app'i DEĞİL; her şey `agent_app` içinde (mevcut kısıt).
- Model: `VideoJob` (id, user, prompt, provider, status, progress, output_path, error, created_at...).
- Servis: `agent_app/video_generation.py` (sağlayıcı soyutlaması + yerel fallback).
- API: `POST /api/video/generate/`, `GET /api/video/<id>/`, `GET /api/video/<id>/download/`, `GET /api/video/`.
- Sayfa: `/video/` (prompt formu, durum poll, önizleme/indirme).
- Depolama: `media/videos/<user_guid>/`.
- Feature flag: `video_generation` (default True).

## Sağlayıcı sırası

1. OpenAI (Sora) — `OPENAI_API_KEY`
2. Stability (Stable Video Diffusion) — `STABILITY_API_KEY`
3. Replicate — `REPLICATE_API_TOKEN`
4. NVIDIA NIM — `NVIDIA_API_KEY`
5. Yerel slideshow fallback (her zaman mevcut)

Sağlayıcı yoksa/hata verirse bir sonrakine geç; hiçbiri yoksa yerel üretici çalışır.

## Görsel üretimi

- Her sahne için önce AI ile görsel üretilir (OpenAI DALL-E / Stability / NVIDIA), varsa.
- Görsel yoksa PIL ile metin tabanlı frame render edilir.
- Üretilen görseller sahne arka planı olarak kullanılır.

## Yerel fallback

Prompt'tan sahne listesi üret (LLM veya deterministik şablon), her sahne için (AI görsel varsa onu, yoksa PIL) frame render et, imageio-ffmpeg ile MP4 encode et (varsayılan 1920x1080, 24fps, 5-300 sn; 4K'ya kadar yapılandırılabilir). Uzun videolarda arka plan işi olarak çalıştırılır.

## Güvenlik

- Tüm uçlar `require_auth`; kullanıcı yalnızca kendi işlerini görür.
- Çıktı yolu `media/videos/` altında; traversal reddedilir.
- Maksimum süre/boyut sınırları (config); varsayılan üst sınır 300 sn (5 dk).

## Alt yazı (Subtitle)

- Her sahne metni video üzerinde alt yazı olarak gösterilir (PIL ile render).
- Alt yazı stili yapılandırılabilir: font, renk, pozisyon (alt/üst/orta), arka plan.

## Ses/TTS

- Her sahne metni TTS ile seslendirilir (gTTS veya pyttsx3, mevcut ise).
- Ses parçası ffmpeg ile videoya mux edilir.
- TTS yoksa sessiz video üretilir (fallback).

## Çıktı formatları

- MP4 (varsayılan), GIF, WebM desteklenir.

## Dosya yükleme ve AI düzenleme

- Kullanıcı resim/video dosyaları yükleyebilir (media/uploads).
- Yüklenen dosyalar AI tarafından kullanıcı isteğine göre düzenlenir:
  kırpma, birleştirme, geçişler, alt yazı, efektler, hız değişimi vb.
- AI, doğal dil isteğini düzenleme adımlarına çevirir ve ffmpeg ile uygular.
- Tüm düzenleme işleri arka plan job olarak çalışır; durum poll edilir.
- Kullanıcı sadece isteğini yazar; AI her şeyi kendisi yapar.

## Video düzenleme

- Basit kırpma (trim) ve birleştirme (concat) desteklenir.
- Yerel ffmpeg (imageio-ffmpeg) ile yapılır.

## Non-goals

- Gerçek zamanlı streaming video.