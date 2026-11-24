# API Anahtarlari ve Sirlar - Zorunlu Standart

**TEMEL KURAL: API anahtarlari ve sirlar ASLA koda gomulmez.**
Referans: `agent_app/secrets.py`, `management/commands/secrets_scan.py` (EMOG / Django)

"""
API Anahtarlari ve Sirlar - Zorunlu Standart.

Tum AI tarafindan uretilen projelerde otomatik dahil edilir.

TEMEL KURAL: API anahtarlari ve sirlar ASLA koda gomulmez.
Sadece ortam degiskeninden (.env) okunur.

A. ASLA KODA GOMULMEZ - ZORUNLU
   Asagidakiler KODDA BULUNMAZ:
   - API anahtarlari (OpenAI, Anthropic, NVIDIA, Google, AWS, Slack...)
   - JWT/SESSION imzalama anahtarlari
   - Veritabani / SMTP / Redis parolalari
   - OAuth client secret'leri
   - Webhook signing secret'leri
   - Her turlu API anahtari, erisim token'i, PAT

   DOGRU YOL:
     import os
     api_key = os.environ.get("OPENAI_API_KEY")
     if not api_key:
         raise RuntimeError("OPENAI_API_KEY tanimli degil (.env dosyasina ekleyin)")

   YANLIS YOL:
     api_key = "sk-proj-abc123..."            # ASLA
     api_key = os.getenv("OPENAI_API_KEY", "sk-proj-abc123")   # ASLA
     SECRET_KEY = "hardcoded-string"          # ASLA

B. FALLBACK (VARSAYILAN) DEGER - KRITIK SIRLARDA YASAK - ZORUNLU
   Kritik sir icin os.getenv(..., "varsayilan") deseni YAPILAMAZ.
   Bilinen bir varsayilan ile imzalama yapilirsa TOKEN FORGING mumkundur.
   Eksikse hata firlatilir veya ozellik devre disi kalir.

   DOGRU:
     secret = os.environ.get("EMOG_JWT_SECRET")
     if not secret or len(secret) < 32:
         raise RuntimeError("JWT anahtari eksik")

   YANLIS:
     secret = os.environ.get("EMOG_JWT_SECRET", "dev-secret-change-in-production")

C. ZAYIF DEGER TESPITI - ZORUNLU
   Asagidaki desenler KABUL EDILMEZ:
     django-insecure-...      (Django otomatik yer tutucusu)
     your-secret-key, your-super-secret, your-...
     change-me, changeme, replace-me
     dev-secret, supersecret, super-secret
     example, placeholder, dummy, xxx, foobar, insecure

   Uygulama baslarken dogrulanir:
     - DEBUG acik  -> ekrana uyari basar, calisir
     - DEBUG kapali -> REFUSE-TO-START (uygulama calismaz)

D. MERKEZI SIR YONETICISI - ZORUNLU
   Tum sir erisimi tek modulden olmelidir:
   - get(name) / require(name) / has(name)
   - validate() -> eksik ve zayif sir raporu
   - fail_loudly() -> kritik eksikse uygulamayi durdurur
   - mask(value) / mask_env(name) -> loglanabilir hale getirir
   - redact_text(text) / redact_mapping(data) -> serbest metinden sir temizler
   API anahtarlari her yere dagtilmaz, tek noktadan yonetilir.

E. LOGLAMA - ZORUNLU
   - API anahtarlari ASLA loglanmaz (bilerek kismi de olsa)
   - Authorization / Bearer / api-key basliklari maskelenir
   - Log'a yazilan her sey redactor'dan gecmelidir
   - Hata mesajlari sirlari ICERMEZ
   - Token'in ilk/son 4 karakteri gorunur, ortasi degil:
       f"{token[:4]}...{token[-4:]}"
   - Proxy/SDK katmaninda TUM header loglamasi yasaktir

F. HATA AYIKLAMA ENDPOINTLERI - ZORUNLU
   - Debug/health endpointleri sir DEGERI dondremez
   - Yalnizca VAR/YOK (true/false) bildirir
   - Admin-only olmalidir
   - DEBUG=False iken tamamen devre disi kalmalidir

G. GIT GUVENLIGI - ZORUNLU
   .gitignore'da ASLA olmamasi gerekenler:
     .env, .env.*, *.pem, *.key, *.p12, *.pfx, id_rsa, id_ed25519,
     .vault_key, .vault_secrets/, .mtls_ca/, secrets.json,
     admin-2fa-qr.png, *totp*.json
   Istisna: .env.example ISLENIR (sadece anahtar ADLARI, degerler bos)

   CI/CD'de sirlar environment secret olarak tanimlanir, ASLA dosya olarak
   repoya konmaz. Pre-commit hook ile tarama ZORUNLUDUR.

H. OTOMATIK TARAMA - ZORUNLU
   Her build/CI adiminda sizinti taramasi calisir:
   - Bilinen anahtar kaliplari (nvapi-, sk-, ghp_, AKIA, AIza, xox*, glpat-)
   - PRIK PRIVATE KEY bloklari
   - os.getenv(...) icinde gomulu deger
   - SECRET_KEY = "..." deseni
   Bulgu varsa build BASARISIZ olur (kirilmis deger degil, kirilan build).

I. SIR ROTASYONU - ZORUNLU
   - Anahtar sizarsa (git gecmisine girdiyse) ROTASYON ZORUNLUDUR
     (silmek yetmez, anahtar degismelidir)
   - .env yedekleri proje disina tasinir
   - Gunluk/haftalik periyodla rotasyon onerilir
   - Eski anahtarla imzalanmis token'lar gecersiz sayilir

J. URETIMDE ZORUNLU AYARLAR
   DEBUG=false
   ALLOWED_HOSTS=<gercek domainler>   ('*' YASAK)
   SESSION_COOKIE_SECURE=true
   CSRF_COOKIE_SECURE=true
   Tum kritik sirler guclu ve benzersiz

K. YAPILMAMASI GEREKENLER
   - Koda gomulu API anahtari / fallback degeri
   - "Sadece gelistirme icin" yorumuyla anahtar gommek
   - Log'a tam token / tam header yazmak
   - .env dosyasini commit etmek
   - Debug endpointlerinden deger dondurmek
   - Sifri version control'a girdikten sonra sadece silmek (rotasyon şart)
   - Zayif/ornek degeri sessizce kabul etmek

REFERANS IMPLEMENTASYON:
   agent_app/secrets.py            (merkezi sir yoneticisi)
   management/commands/secrets_scan.py  (tarama komutu)
