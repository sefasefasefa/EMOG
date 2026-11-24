"""
Proje Iskeleti (Scaffold) - Zorunlu Standart.

Tum AI tarafindan uretilen projelerde otomatik dahil edilir.

KURAL: EMOG'un olusturdugu / ice aktardigi HER proje, ilk anda
      zorunlu guvenlik dosyalarini alir. Bu dosyalar olmadan proje
      "tamamlanmis" sayilmaz.

A. HER PROJEDE BULUNMASI ZORUNLU DOSYALAR
   1. .gitignore     - sirlar/anahtarlar/veritabani/log ASLA commit edilmez
   2. .env.example   - tum ortam degiskenlerinin ANAHTAR ADLARI (degerler BOS)
   3. SECURITY.md    - projenin guvenlik standardi ozeti

   Bu dosyalar EKSIKSE proje uretilmis sayilmaz.
   Uretim sonrasi kontrol (critic) eksik dosyayi bulup reddeder.

B. .gitignore ICERIGI - ZORUNLU BLOKLAR
   1. SIRLAR: .env, .env.*, !.env.example, *.key, *.pem, *.p12, *.pfx,
      id_rsa, id_ed25519, secrets.json, *.keystore, *.jks
   2. 2FA/TOTP: admin-2fa-qr.png, *totp*.json, *otpauth*, *.otp
      (QR kod TOTP seed'i icerir - ASLA commit edilmemeli)
   3. LOGLAR: *.log, logs/, audit_logs/
   4. VERITABANI: *.sqlite3, *.db, postgres-data/, mysql-data/
   5. ZERO-TRUST: .vault_key, .vault_secrets/, .mtls_ca/, certs/
   6. OTURUM: *.session, session.json
   7. YIGINA GORE: python / node / go / rust / java / php / ruby / .NET
   8. IDE/OS: .vscode/, .idea/, .DS_Store, Thumbs.db
   ONEMLI: `.env` ignore EDILIR ama `.env.example` COMMIT EDILIR
   (istisna `!.env.example` satiri ile).

C. YIGIN TESPITI (ZORUNLU)
   .gitignore ve .env.example icerigi projenin teknoloji yiginine gore
   UYARLANIR. Sabit tek dosya kullanilmaz.
     python : requirements.txt, pyproject.toml, setup.py, Pipfile, manage.py
     node   : package.json, pnpm-lock.yaml, yarn.lock, tsconfig.json
     go/rust/java/php/ruby/dotnet : kendi dosya imzalariyla tespit edilir
   .env.example icerigi de yigina gore degisir (Node icin ENCRYPTION_KEY,
   Session/JWT signing key'leri; Python icin SECRET_KEY, DJANGO/EMOG_JWT_SECRET).
   Tespit basarisizsa python varsayilir.

D. KULLANICI OZELLIKLERI KORUNUR - ZORUNLU
   - Var olan dosya UZERINE YAZILMAZ
   - Kullanici kendi .gitignore'ina ekleme yaparsa o satirlar KALIR
   - force modunda SADECE eksik zorunlu kaliplar eklenir (append)
   - Proje sahibinin calismasi ASLA bozulmaz

E. ENTEGRASYON NOKTASI - ZORUNLU
   Scaffold, proje OLUSTURMA/KAYDETME yoluna baglanir (tek noktadan).
   Boylece hangi yolla olusursa olsun (AI, import, API, CLI) dosyalar
   garanti edilir. Iskelet yazimi asla proje kaydini engellemez;
   hata olursa sessizce gecilir (guvenlik dosyalari kritik degilse).

F. DENETIM (CI) - ZORUNLU
   Her build sonrasi kontrol edilir:
   1. .gitignore, .env.example, SECURITY.md var mi?
   2. .gitignore icinde ".env" deseni var mi?
   3. Anahtar dosya kaliplari (*.key, *.pem, id_rsa) var mi?
   4. `.env.example` istisnasi (`!.env.example`) tanimli mi?
   5. `.env` git ls-files ciktisinda gorunuyor mu? (KRITIK - build kirilir)
   Eksik/uyari varsa build BASARISIZ olur.

G. YAPILMAMASI GEREKENLER
   - Proje olusturulduktan SONRA .gitignore eklemek (ilk anda olmali)
   - Sabit tek .gitignore kullanmak (yigina gore uyarlanmali)
   - Var olan .gitignore'u EZmek (kullanici calismasi bozulur)
   - .env dosyasini olusturmak (sadece .env.example - degerler bos)
   - .env.example icine gercek deger yazmak
   - .gitignore'da `.env.example` satiri olmadan tum .env*'i ignore etmek
     (ornek dosya da ignore edilir, yeni gelistirici ne kuracagini bilemez)
   - 2FA QR kodunu / TOTP seed'ini commit etmek

REFERANS IMPLEMENTASYON:
   agent_app/project_scaffold.py (EMOG / Django)
"""