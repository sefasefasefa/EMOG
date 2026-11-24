TOKEN_ARCHITECTURE_TEMPLATE = """
ZORUNLU TOKEN MIMARISI (Tum projelerde otomatik dahil):

A. ACCESS TOKEN - KISA OMURLU
   - Form: JWT imzali (RS256 tercih edilir, HS256 kabul)
   - Omur: 5-15 DAKIKA (config ile ayarlanabilir, ACCESS_TOKEN_TTL_MINUTES)
   - Icermesi ZORUNLU: sub, typ="access", iat, exp, jti, iss, aud
   - jti (unique id) ZORUNLU - token iptali icin
   - Saklama: HttpOnly + Secure + SameSite=Strict/Lax cookie
   - ASLA localStorage/sessionStorage'da tutulmaz (XSS riski)
   - ASLA Authorization disinda baska yere yazilmaz

B. REFRESH TOKEN - HER KULLANIMDA YENILENIR (ROTATION)
   - Form: Opak rastgele (crypto.randomBytes(48+)), JWT DEGIL
   - Saklama: DB'de SADECE SHA-256 hash (duz metin ASLA saklanmaz)
   - Omur: 7-30 GUN
   - ROTATION: Her basarili kullanimda eski token iptal edilir,
     yeni bir refresh token uretilir ve istemciye verilir
   - TOKEN AILE (family) KAVRAMI: Tum rotasyonlar ayni family ID'sini paylasir
   - Tablo: RefreshToken (token_hash, family, session, expires_at,
     rotated_at, is_revoked, revoked_at, revoked_reason, user_agent, ip_address)

C. REUSE DETECTION (TOKEN HIRSIZLIGI TESPITI) - ZORUNLU
   - Kullanilmis (rotated_at dolu) veya iptal edilmis bir refresh token
     TEKRAR sunulursa: TUM AILE acilen iptal edilir
   - Kullanici tum cihazlardan cikis yapilir
   - CRITICAL seviyesinde SecurityEvent yazilir ve SIEM'e gonderilir
   - Yanit: 401 + {code: "reuse_detected", reauth_required: true}

D. OTOMATIK YENILEME (3 KATMAN - ZORUNLU)
   1. PROAKTIF: Frontend, access token suresi dolmadan ~60 sn once
      /auth/refresh/ cagirir (setTimeout/interval ile zamanlama)
   2. SEFFAF (transparency): API 401 dondugunde backend, gecerli refresh
      token ile sessizce yeniler ve yeni token'i response cookie'sine yazar
   3. REACTIVE: Frontend 401 aldiysa bir kez refresh dener, istegi tekrarlar,
      basarisizsa /auth/login/ adresine yonlendirir
   - Kullanici hicbir zaman "session suresi doldu" yazisi gormemeli

E. CIKIS (LOGOUT)
   - Tum token ailesi iptal edilir (family bazli)
   - Tum cookie'ler temizlenir (access + refresh + ttl)

F. BAKIM
   - Suresi dolmus token kayitlari gunluk cron ile silinir
   - Pasif kullanici (is_active=False) token yenileyemez, ailesi iptal edilir
   - Token tablosu index'li: (user, is_revoked), (family, is_revoked)

G. YAPILMAMASI GEREKENLER
   - Access token'i localStorage'da saklamak
   - Refresh token'i DB'de duz metin saklamak
   - Refresh token'i yenilemeden tekrar kullanmak
   - Reuse detection OLMAMASI
   - Token'i URL query parametriyle tasimak
   - Son kullanma kontrolu (expiry) yapmamak

GEREKEN ENV DEGISKENLERI:
   ACCESS_TOKEN_TTL_MINUTES=15
   REFRESH_TOKEN_TTL_DAYS=30
   JWT_SECRET=<32+ karakter, env'de>
   JWT_ALGORITHM=RS256
   REFRESH_COOKIE_PATH=/auth/
"""