"""
Rate Limiting / Brute Force Koruma - Zorunlu Standart.

Tum AI tarafindan uretilen projelerde otomatik dahil edilir.

A. KADEMELI YAVASLATMA (PROGRESSIVE DELAY) - ZORUNLU
   Sabit kilit yerine kademeli artan gecirme:
     1-4  hatali deneme : normal (sadece kayit)
     5+   hatali deneme : 2 sn gecikme
     8+   hatali deneme : 60 sn kilit
     12+  hatali deneme : 300 sn (5 dk) kilit
     16+  hatali deneme : 1800 sn (30 dk) kilit
     20+  hatali deneme : 3600 sn (1 saat) kilit

B. OLÇUT (ZORUNLU)
   - Kullanici (email/username) + IP KOMBINASYONU: her ikisi birlikte sayilir
   - AYRICA IP bazli genel sinir: 30 hatali denemede 10 dk kilit
     (farkli hesaplara yonelmis dagitik saldiri korumasi)
   - Yanit SIZDIRMAZ: kullanici adi var/yok bilgisi verilmez
     her zaman ayni genel hata mesaji doner

C. ZAMAN PENCERESI (SLIDING WINDOW)
   - Hata sayaci 15 dk (900 sn) sonra kendiliginden sifirlanir
   - Kilit suresi kendi TTL'ine sahip

D. YANIT FORMATI (ZORUNLU)
   HTTP 429 + Retry-After header + govde:
   {
     "error": "Cok fazla basarisiz deneme. N saniye sonra tekrar deneyin.",
     "code": "rate_limited",
     "retry_after": 60,
     "retry_after_minutes": 1,
     "locked": true
   }
   Kilitliyken DOGRU parola bile reddedilir (bilgi sizdirmaz).

E. BASARILI GIRISTE SIFIRLAMA (ZORUNLU)
   Basarili giris sonrasi kullanici ve IP sayaclari sifirlanir.
   Boylece gercek kullanici yanlis giris yapsa bile kilitlenmez.

F. KAPSAM (ZORUNLU) - HANGI ENDPOINT'LER SINIRLANIR
   - /auth/login        (giris)
   - /auth/register     (kayit - spam/kayit bombasi)
   - /auth/refresh      (token yenileme deneme bombasi)
   - /auth/password-reset (hesap ele gecirme)
   - /auth/verify-email (token tahmini)
   Her biri icin acik/kapali anahtari olmali (LOGIN_LIMIT_*)

G. GECIKME (TIME-BASED DELAY) - ZORUNLU
   Kademeli gecirme yanit dondurulmeden ONCE uygulanir.
   Maksimum bekleme 5 sn ile sinirlanir (kullanici deneyimi).
   Redis tabanli sistemlerde ek olarak Lua script ile atomik sayac kullanilir.

H. SAKLAMA (STORAGE)
   - Redis: tutarlil icin tercih edilir (TTL + atomiklik)
   - Memcached: kabul edilir
   - DB tablosu: son cares
   - Process belleigi (RAM): YASAK (birden fazla instance'ta tutarsiz)

I. GUVENLIK OLAYI KAYDI (ZORUNLU)
   - Her kilit tetiklendiginde SecurityEvent / audit log yazilir
   - severity: medium (kisa kilit), high (uzun kilit), critical (1 saat+)
   - SIEM'e gonderim icin yapilandirilabilir
   - Kullanici e-postasini log'a YAZMA (sadece sayim)

J. YAPILMAMASI GEREKENLER
   - Sabit 5 deneme sonrasi kalici kilit (kullaniciyi disarida birakir)
   - Kilitliyken dogru parolayi kabul etmek (bilgi sizdirir)
   - Sayaci basarili giriste sifirlamamak
   - IP bazli sinir olmamak (dagitik saldiri korumasi eksik kalir)
   - Proses belleginde sayac tutmak (coklu instance'ta tutarsiz)
   - Kullanici adinin var/yok bilgisini sizdirmak (user enumeration)

GEREKEN ENV DEGISKENLERI:
   LOGIN_MAX_ATTEMPTS=5
   LOGIN_LOCKOUT_TIERS="5:2,8:60,12:300,16:1800,20:3600"
   LOGIN_IP_MAX_ATTEMPTS=30
   LOGIN_IP_LOCKOUT_SECONDS=600
   LOGIN_WINDOW_SECONDS=900
   LOGIN_MAX_DELAY_SECONDS=5
   LOGIN_LIMIT_REGISTER=true
   LOGIN_LIMIT_REFRESH=true
   LOGIN_LIMIT_PASSWORD_RESET=true

REFERANS IMPLEMENTASYON: agent_app/rate_limit.py (EMOG / Django)
"""