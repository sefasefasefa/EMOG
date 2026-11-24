# Sunucu Tarafi Yetkilendirme - Zorunlu Standart

Tum AI tarafindan uretilen projelerde otomatik dahil edilir.
Referans: `agent_app/permissions.py`, `agent_app/auth_middleware.py`, `agent_app/projects/security.py` (EMOG / Django)

"""
Sunucu Tarafi Yetkilendirme - Zorunlu Standart.

Tum AI tarafindan uretilen projelerde otomatik dahil edilir.
Arayuzde bir butonu gizlemek yetki DEGILDIR.

A. KATMANLAR (ZORUNLU - her katman ayri bir kontrol noktasidir)
   1. MIDDLEWARE: tum istekler tek noktadan dogrulanir (varsayilan KAPALI rotalar haric)
   2. VIEW GUARD: her mutasyon view'inin basinda acikca yetki kontrolu
      (middleware tek basina yeterli degildir - defalarda bypass riski)
   3. RESOURCE LEVEL: nesne sahipligi (IDOR korumasi)
   4. FEATURE GATE: ozelligi kapaliyken sunucu reddeder (arayuzde gizlemek YETMEZ)

B. YETKI SEVIYELERI (ZORUNLU)
   public       : kimlik gerekmez (landing, saglik, login, register)
   authenticated: gecerli access token (JWT) sarti
   feature      : ustune ozellik anahtari ACIK olmali
   admin        : sadece yetkili hesap (SUPERUSER_EMAIL / rol=admin)
   owner        : kaynagin sahibi olmak gerekir (IDOR korumasi)

C. FAIL-CLOSED KURALI (EN ONEMLI MADDE)
   Kontrol HATASI veya BEKLENMEYEN durumda varsayilan IZIN VERILMEZ.
   Sahiplik kaydi yoksa -> erisim REDDEDILIR.
   Feature anahtari okunamazsa -> erisim REDDEDILIR (guvenli taraf).
   Sadece acikca izin verilen yol listesinden gecilebilir.

D. ARAYUZ YETKI DEGILDIR (ZORUNLU)
   - Bir endpoint'i template'te gizlemek / DOM'dan kaldirmak / butonu
     display:none yapmak YETKI SAGLAMAZ. Saldirgan endpoint'i dogrudan cagirir.
   - Feature flag UI'da sekmeyi gizler, SUNUCUDA endpoint'i kapatir.
   - Iki katman birlikte calisir ama yetki karari daima sunucudadir.

E. ADMIN-ONLY AYRI TARAFA (ZORUNLU)
   - Debug endpoint'leri, ayar yazma, token yazma, proje silme
   - Sadece yetkili admin hesabina aciktir (403 doner)
   - Statik shared-secret yerine KULLANICI tabanli kontrol
     (statik sifir .env'de kalirsa butun sistem bir sirla ele gecirilir)

F. IDOR KORUMASI (ZORUNLU)
   - Her nesne erisiminde sahiplik kontrolu: filter(id=..., owner=current_user)
   - YALNIZCA id ile getirme YASAK
   - Liste endpoint'leri de filtrelenir (kullaniciya ait olmayanlar gorunmez)
   - Sahiplik alani iki kez sorgulanmaz, tek sorguda filtrelenir

G. YOL GUVENLIGI (ZORUNLU)
   - Path traversal: cozulmus yol proje/root dizini icinde kalmali
   - Mutlak yol, "..", sembolik link kacisi engellenir
   - Sentinel ekleme ya da realpath + startswith kontrolu
   - Dosya yazma/silme/terminal icin ZORUNLU

H. KOMUT BEYAZ LISTESI (ZORUNLU) - shell erisimi olan her yer icin
   - Izinli komut listesi tanimlanir (ls, git, python, npm...)
   - Tehlikeli kaliplar: rm -rf /, curl, wget, nc, mkfs, dd, shutdown,
     eval, base64 -d, python -c, sh -c, chmod -R 777 /
   - shell=True + kullanici girdisi = YASAK, shell=False tercih edilir
   - timeout ZORUNLU (sonsuz dongu / resource exhaustion)
   - cwd de ayni guvenlik kontrolunden gecmelidir

I. YANIT KODLARI (ZORUNLU)
   401 authentication_required  - kimlik yok / token gecersiz / suresi dolmus
   403 forbidden                - kimlik var ama yetki yok
   403 feature_disabled         - ozellik kapatilmis
   403 admin_required           - sadece yonetici
   403 not_owner                - kaynak baska kullaniciya ait (IDOR)
   403 csrf_failed              - CSRF dogrulamasi basarisiz
   429 rate_limited             - cok fazla deneme
   Hata govdesinde `code` alani ZORUNLUDUR (frontend buna gore davranir)

J. GUMLER (ZORUNLU)
   - Hata mesaji kullaniciya AYRI NT olmayacak sekilde yazilir
   - "bu e-posta kayitli" gibi ifadeler kullanici sayimi sizdirir (user
     enumeration) - her zaman ayni genel mesaj kullanilir
   - Stack trace / SQL / dosya yolu ASLA istemciye donmez
   - Yetki basarisizligi guvenlik olayina (SecurityEvent) yazilir

K. TEST ZORUNLULUGU (ZORUNLU)
   Her mutasyon endpoint'i icin otomatik test ZORUNLU:
   1. Anonim istek        -> 401
   2. Gecersiz/expired JWT-> 401
   3. Dus yetkili kullanici -> 200 (veya 403, is tanimina gore)
   4. Admin-only endpoint  -> normal kullaniciya 403
   5. Feature kapali       -> 403 feature_disabled
   6. IDOR                 -> baska kullanicinin kaynagi 403
   7. Path traversal       -> 400 unsafe_path
   Sunucu calistirildiktan sonra butun testler gecmelidir.

L. YAPILMAMASI GEREKENLER
   - Arayuzde gizleyerek yetki saymak
   - Tek middleware'e guvenmek (view seviyesi kontrol olmadan)
   - id ile nesne getirip kontrol etmemek (IDOR)
   - Statik shared-secret ile yetkilendirme
   - shell=True + kullanici girdisi
   - Path.resolve() sonrasi startswith kontrolu olmaması
   - Sahiplik kaydi yoksa izin vermek (fail-open)
   - Hata mesajinda kullanici varligini sizdirmak

REFERANS IMPLEMENTASYONLAR:
   agent_app/permissions.py          (dekoratorler + yardimcilar)
   agent_app/auth_middleware.py      (AuthorizationMiddleware)
   agent_app/projects/security.py    (yol guvenligi + komut beyaz listesi)
