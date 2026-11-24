# Istek Baglantisi (Request Binding) - Zorunlu Standart

Her istekte isteminin ayni kisi oldugu dogrulanir.
Kimlik dogrulama + yetkilendirme tek basina yeterli DEGILDIR.
Referans: `agent_app/binding.py`, `agent_app/binding_middleware.py` (EMOG / Django)

"""
Istek Baglantisi (Request Binding) - Zorunlu Standart.

Tum AI tarafindan uretilen projelerde otomatik dahil edilir.

"Bu istek gercekten ayni kisi mi?" sorusu HER istekte cevaplanmalidir.
Kimlik dogrulama (kim?) ile yetkilendirme (ne yapabilir?) yeterli DEGILDIR;
istegin sahibiyle baglantisi da dogrulanmalidir (token calinmasi onlenir).

A. ISTEMCI PARMAK IZI (CLIENT FINGERPRINT) - ZORUNLU
   Token verildiginde istemcinin parmak izi hesaplanir ve token'a gomulur.
   Her istekte parmak izi YENIDEN hesaplanir ve token ile karsilastirilir.

   Parmak izi bilesenleri:
   - User-Agent tam degeri
   - User-Agent FAMILYESI (surum haric: Chrome/, Safari/, Firefox/...)
     -> tarayici surumu her guncellemede degistigi icin cikarilir
   - Accept-Language
   - Sec-CH-UA, Sec-CH-UA-Mobile, Sec-CH-UA-Platform (istemci ipuclari)
   - Istemci ekran boyutu (varsa)

   SAKLAMA KURALI (ZORUNLU): parmak izi HAM veri olarak ASLA saklanmaz.
   HMAC-SHA256(secret, parmak_izi) ile geri donus olmayan ozet saklanir.

   NEDEN: token calinan biri ayni tarayici User-Agent bilgisini
   taklit edebilir ama Client-Hints zincirini taklit etmesi cok daha zordur.

B. IP BLOGU BINDING (CIDR esnekligi ile) - ZORUNLU
   - Tam IP degil, AG BLOGU eslestirilir: IPv4 /24, IPv6 /48
     (mobil kullanicilarin IP'si surekli degisir)
   - Token'da kayitli blogu ile mevcut blogu karsilastirilir
   - Tolerans: token yeni olusturulduktan sonra IP_GRACE_SECONDS
     icinde degisiklik kabul edilir (ayni kisi, yeni ag)
   - Gerekirse REQUEST_BINDING_STRICT_IP ile tam IP zorlanabilir
   - Mobil kullanicilar icin tolerans ZORUNLUDUR (yoksa her ag
     degisiminde kullanici kilitlenir)

C. CSRF DOUBLE-SUBMIT - ZORUNLU
   csrf_exempt endpoint'lerde de koruma saglanir.
   1. Sunucu okunabilir bir CSRF cookie'si yazar (HttpOnly DEGIL)
      deger imzalidir: raw.hmac
   2. Istemci ayni degeri X-CSRF-Token header'inda geri gonderir
   3. Sunucu cookie ile header'i HMAC ile karsilastirir
   - Saldirganin tarayiciya cookie YAZAMAMASI (cross-site) nedeniyle
     header degeri tutmaz -> istek 403 reddedilir
   - Ayni origin sinyali (Sec-Fetch-Site) varsa ek kontrol olarak kullanilir
   - ISTEK YAPISINDA GLOBAL MIDDLEWARE UYGULANIR; her endpoint'e
     ayri ayri eklemek hataya aciktir
   - Login/register endpoint'leri istisnadır: henuz cookie yoktur,
     cookie varsa yine dogrulanir

D. IHLAL YANITI (FAIL-CLOSED) - ZORUNLU
   Parmak izi uyusmazligi  -> 401 client_binding_mismatch + reauth
   IP blogu uyusmazligi   -> 401 ip_binding_mismatch
   CSRF cookie yok        -> 403 csrf_cookie_missing
   CSRF header yok        -> 403 csrf_token_missing
   CSRF deger farkli      -> 403 csrf_token_mismatch
   CSRF imzasi gecersiz   -> 403 csrf_signature_invalid

   IHLAL SONRASI ZORUNLU EYLEMLER:
   - Kullanicinin TUM refresh token'lari iptal edilir
     (token calinma isaretidir, sadece istegi reddetmek yeterli DEGILDIR)
   - SecurityEvent severity=critical yazilir
   - SIEM'e gonderim yapilir
   - Log: parmak izi (ozet), ag blogu, UA ailesi, dil - ham veri YOK

E. KAPSAM - ZORUNLU
   - GET/HEAD/OPTIONS/TRACE: CSRF gerekmez, parmak izi YINE kontrol edilir
   - POST/PUT/PATCH/DELETE: parmak izi + CSRF
   - Token yenileme (refresh) istegi de parmak izi kontrolunden gecer
   - Sayfa istekleri (HTML) 401 yerine login'e yonlendirilir

F. FRONTEND UYGULAMASI - ZORUNLU
   - Global fetch interceptor: her mutasyon istegi otomatik CSRF header'i alir
     (her sayfada ayri ayri eklemek hataya aciktir)
   - Token yenileme sonrasi CSRF degeri tazelenir
   - 401 sonrasi tekrar denemede CSRF degeri tekrar okunur

G. AYARLAR (ENV)
   REQUEST_BINDING_ENABLED=true
   REQUEST_BINDING_STRICT_IP=false
   REQUEST_BINDING_IP_GRACE_SECONDS=900
   CSRF_BINDING_ENABLED=true
   CSRF_COOKIE_SAMESITE=Lax
   CSRF_COOKIE_MAX_AGE=3600

H. TEST ZORUNLULUGU
   1. Ayni istemci, ayni token -> KABUL
   2. Farkli tarayici/cihaz, ayni token -> 401 client_binding_mismatch
   3. Ayni tarayici, farkli IP blogu -> tolerans icinde KABUL, disinda 401
   4. CSRF'siz POST -> 403
   5. Yanlis CSRF degeri -> 403
   6. Dogru CSRF degeri -> gecer
   7. Ihlal sonrasi refresh token'lar iptal edilmis olmali
   8. SecurityEvent (critical) yazilmis olmali

I. YAPILMAMASI GEREKENLER
   - Parmak izini duz metin saklamak (raw UA, raw IP)
   - Surum numarasini parmak izine dahil etmek (her guncellemede kacar)
   - Tam IP zorunlulugu (mobil kullaniciyi kilitler)
   - CSRF korumasini sadece bazi endpoint'lere eklemek
   - Ihlalde sadece istegi reddetmek (token iptali olmadan)
   - Cookie'yi HttpOnly yapmak (JS okuyamaz, double-submit calismaz)
   - GET isteklerinde CSRF zorunlu kilmak (her sayfa yuklemesi kirilir)

REFERANS IMPLEMENTASYON:
   agent_app/binding.py          (parmak izi + CSRF uretimi/dogrulama)
   agent_app/binding_middleware.py (her istekte uygulama)
