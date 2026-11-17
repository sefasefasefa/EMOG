"""
EMOG özellik kataloğu (feature registry).

Her özellik için:
  key      : benzersiz anahtar (veritabanında saklanır)
  label    : panelde görünen ad
  group    : kategori
  description: kısa açıklama
  default  : varsayılan açık/kapalı
  critical : kapatılması riskli mi (panelde uyarı gösterilir)
  paths    : bu özellik kapalıyken engellenecek URL önekleri
  nav      : menüde görünecek mi
"""

FEATURE_GROUPS = [
    ("ui", "Arayüz"),
    ("ai", "Yapay Zekâ / Ajan"),
    ("tools", "Araçlar"),
    ("integration", "Entegrasyonlar"),
    ("infra", "Altyapı"),
    ("security", "Güvenlik"),
    ("telemetry", "Telemetri"),
    ("sandbox", "Sandbox"),
]


FEATURES = [
    # ---------------- ARAYÜZ ----------------
    dict(
        key="landing_page", label="Landing Sayfası", group="ui",
        description="Giriş öncesi tanıtım sayfası", default=True,
    ),
    dict(
        key="chat", label="Chat Paneli", group="ui",
        description="AI sohbet arayüzü ve mesajlaşma", default=True,
        paths=["/chat/"], nav=True,
    ),
    dict(
        key="panel_integrations", label="Integrations Sekmesi", group="ui",
        description="Figma / Vercel / Netlify / Supabase / Docker kartları", default=True,
    ),
    dict(
        key="panel_preview", label="Preview Sekmesi", group="ui",
        description="Canlı önizleme, HMR ve konsol", default=True,
    ),
    dict(
        key="panel_tools", label="Tools Sekmesi", group="ui",
        description="Dosya ağacı ve kod editörü", default=True,
    ),
    dict(
        key="projects", label="Projects Sayfası", group="ui",
        description="Proje listesi, oluşturma ve içe aktarma", default=True,
        paths=["/projects/"], nav=True,
    ),
    dict(
        key="projects_editor", label="Proje Editörü", group="ui",
        description="Proje modalında dosya editörü", default=True,
    ),
    dict(
        key="projects_terminal", label="Proje Terminali", group="ui",
        description="Proje dizininde shell komutları", default=True,
    ),
    dict(
        key="projects_git", label="Proje Git Sekmesi", group="ui",
        description="Commit / push / pull / log arayüzü", default=True,
    ),
    dict(
        key="tasks_page", label="Görev Kuyruğu", group="ui",
        description="Task listesi ve kuyruk yönetimi", default=True,
        paths=["/tasks/"], nav=True,
    ),
    dict(
        key="runs_page", label="Çalıştırma Geçmişi", group="ui",
        description="Run listesi ve log görüntüleme", default=True,
        paths=["/runs/"], nav=True,
    ),
    dict(
        key="oauth_login", label="OAuth Giriş (Google/GitHub/Apple)", group="ui",
        description="Sosyal medya ile giriş butonları", default=True,
    ),
    dict(
        key="email_verification", label="E-posta Doğrulama", group="ui",
        description="Kayıt sonrası doğrulama maili", default=True,
    ),

    # ---------------- YZ / AJAN ----------------
    dict(
        key="ai_chat", label="AI Sohbeti", group="ai",
        description="OmniRoute / NVIDIA model çağrıları", default=True,
        paths=["/assistant/message/", "/omni/", "/nvidia/"], critical=True,
    ),
    dict(
        key="self_healing", label="Self-Healing Döngüsü", group="ai",
        description="Hata tespit edip otomatik düzelten ajan döngüsü", default=True,
    ),
    dict(
        key="agent_tools", label="Ajan Araçları (tool calling)", group="ai",
        description="read_file, write_file, grep, run_command", default=True, critical=True,
    ),
    dict(
        key="background_loop", label="Arka Plan Döngüsü", group="ai",
        description="Sürekli hedef çalıştıran autonomous loop", default=False,
        paths=["/background-loop/"],
    ),
    dict(
        key="task_queue", label="Görev Kuyruğu (worker)", group="ai",
        description="FIFO iş kuyruğu ve arka plan worker", default=True,
        paths=["/queue/", "/tasks/list/"],
    ),
    dict(
        key="task_stream", label="Canlı Log Akışı (SSE)", group="ai",
        description="EventSource ile gerçek zamanlı log", default=True,
        paths=["/task/stream/"],
    ),
    dict(
        key="conversation_history", label="Sohbet Geçmişi (Time Travel)", group="ai",
        description="Dal açma / geri alma / birleştirme", default=True,
        paths=["/api/v1/conversations/", "/api/v1/nodes/"],
    ),
    dict(
        key="planning", label="Mimari Planlama (core/planning)", group="ai",
        description="Mimari plan ve TDD senaryosu üretimi", default=False,
    ),
    dict(
        key="self_reflection", label="Öz-Düşünce (core/self_reflection)", group="ai",
        description="Model çıktısını çalıştırmadan önce denetleme", default=False,
    ),
    dict(
        key="simulation", label="Simülasyon (core/simulation)", group="ai",
        description="Varsayımsal senaryo ile kendini eğitme", default=False,
    ),
    dict(
        key="validation", label="Doğrulama (core/validation)", group="ai",
        description="Gereksinimleri adım adım doğrulama", default=False,
    ),
    dict(
        key="learning", label="Hata Öğrenmesi (core/learning)", group="ai",
        description="Hatalardan deneyim çıkarma", default=False,
    ),
    dict(
        key="memory_layered", label="Katmanlı Bellek", group="ai",
        description="Kısa/uzun süreli + episodic + semantic bellek", default=True,
    ),
    dict(
        key="plugin_marketplace", label="Plugin Marketplace", group="ai",
        description="Dış modül keşfi ve dinamik yükleme", default=False,
    ),
    dict(
        key="multi_agent", label="Çoklu Ajan Orkestrasyonu", group="ai",
        description="Uzman ajanlar + critic + synthesizer", default=False,
    ),
    dict(
        key="voice", label="Sesli Komut", group="ai",
        description="Whisper ile sesli komut desteği", default=False,
    ),
    dict(
        key="neural_core", label="NEURAL CORE", group="ai",
        description="Planlama, keşif, araştırma, deneyim ve güvenli evrim çekirdeği",
        default=True, paths=["/neural/", "/api/neural/"], critical=True,
    ),
    dict(
        key="neural_tools", label="NEURAL Bilgisayar Araçları", group="ai",
        description="Onay kontrollü dosya yazma ve komut çalıştırma", default=True, critical=True,
    ),
    dict(
        key="neural_evolution", label="NEURAL Kendini Geliştirme", group="ai",
        description="Güvenli politika önerileri ve onaylı uygulama", default=True,
    ),
    dict(
        key="neural_autonomy", label="NEURAL Otonom Hedefler", group="ai",
        description="Zamanlanmış araştırma/öğrenme hedefleri", default=True,
    ),
    dict(
        key="video_generation", label="Video Aoretimi", group="ai",
        description="Prompt'tan video uretimi ve yuklenen dosyalarin AI ile duzenlenmesi",
        default=True, paths=["/api/video/", "/video/"],
    ),

    # ---------------- ARAÇLAR ----------------
    dict(
        key="tools_file_tree", label="Dosya Ağacı", group="tools",
        description="Proje dizin ağacını görüntüleme", default=True,
        paths=["/tools/file-tree/"],
    ),
    dict(
        key="tools_read_file", label="Dosya Okuma", group="tools",
        description="Sunucudan dosya içeriği okuma", default=True,
        paths=["/tools/read-file/"], critical=True,
    ),
    dict(
        key="tools_write_file", label="Dosya Yazma", group="tools",
        description="Sunucuya dosya yazma", default=True,
        paths=["/tools/write-file/"], critical=True,
    ),
    dict(
        key="code_execution", label="Kod Çalıştırma", group="tools",
        description="exec_code / run_task endpoint'leri", default=True,
        paths=["/exec_code_no_csrf/", "/run_task/", "/run_task_no_csrf/"], critical=True,
    ),
    dict(
        key="settings_save", label="Ayarları Kaydet", group="tools",
        description=".env üzerinden model/ayar güncelleme", default=False,
        paths=["/settings/save/"], critical=True,
    ),

    # ---------------- ENTEGRASYONLAR ----------------
    dict(
        key="github", label="GitHub Entegrasyonu", group="integration",
        description="Repo listeleme, push, create, OAuth", default=True,
        paths=["/github/"], nav=True,
    ),
    dict(
        key="github_write", label="GitHub Yazma İşlemleri", group="integration",
        description="Repo oluşturma ve kod gönderme", default=False,
        paths=["/github/create/", "/github/push/"], critical=True,
    ),
    dict(
        key="figma", label="Figma Entegrasyonu", group="integration",
        description="Tasarım dosyası içe aktarma", default=False,
    ),
    dict(
        key="vercel", label="Vercel Deployment", group="integration",
        description="Vercel'e deploy", default=False,
    ),
    dict(
        key="netlify", label="Netlify Deployment", group="integration",
        description="Netlify'e deploy", default=False,
    ),
    dict(
        key="supabase", label="Supabase", group="integration",
        description="Supabase proje bağlantısı", default=False,
    ),
    dict(
        key="docker", label="Docker", group="integration",
        description="Image build ve container çalıştırma", default=False,
    ),
    dict(
        key="whatsapp", label="WhatsApp", group="integration",
        description="Meta Cloud API / Twilio / webhook ile mesaj gönderimi",
        default=False, paths=["/whatsapp/"], critical=True,
    ),
    dict(
        key="preview_design", label="Tasarım Analizi", group="integration",
        description="Görselden HTML/Tailwind üretimi", default=False,
        paths=["/preview/analyze-design/"],
    ),
    dict(
        key="visual_qa", label="Görsel QA", group="integration",
        description="Otomatik görsel kalite kontrolü", default=False,
        paths=["/preview/visual-qa/"],
    ),
    dict(
        key="hmr", label="Canlı Yeniden Yükleme (HMR)", group="integration",
        description="Preview panelinde HMR akışı", default=True,
        paths=["/preview/hmr/"],
    ),
    dict(
        key="email_smtp", label="E-posta Gönderimi", group="integration",
        description="SMTP ile doğrulama maili", default=True,
    ),

    # ---------------- ALTYAPI ----------------
    dict(
        key="celery", label="Celery Kuyruğu", group="infra",
        description="Redis broker ile asenkron görevler", default=True,
    ),
    dict(
        key="telemetry_cost", label="Maliyet Takibi", group="infra",
        description="LLM çağrılarında token ve maliyet izleme", default=True,
    ),
    dict(
        key="nvidia_api", label="NVIDIA API", group="infra",
        description="NVIDIA NIM endpoint kullanımı", default=True,
    ),

    # ---------------- GÜVENLİK ----------------
    dict(
        key="audit_logging", label="Denetim Kaydı", group="security",
        description="Her isteğin kaydı (AuditLog)", default=True, critical=True,
    ),
    dict(
        key="client_telemetry", label="İstemci Telemetrisi", group="telemetry",
        description="Ekran/dil/cihaz bilgisi toplama", default=True,
    ),
    dict(
        key="bruteforce_protection", label="Brute-Force Koruması", group="security",
        description="django-axes ile hesap kilitleme", default=True, critical=True,
    ),
    dict(
        key="secrets_management", label="Sır Yönetimi", group="security",
        description="API anahtarları asla koda gömülmez, sadece .env'den okunur",
        default=True, critical=True,
    ),
dict(
        key="secrets_scan", label="Sır Sızıntı Taraması", group="security",
        description="Kod tabanında gömülü anahtar taraması (CI'da zorunlu)",
        default=True,
    ),
    dict(
        key="log_redaction", label="Log Maskeleme", group="security",
        description="API anahtarları ve token'lar loglara yazılmaz", default=True,
    ),
    dict(
        key="request_binding", label="İstek Bağlantısı (Request Binding)", group="security",
        description="Her istekte istemci parmak izi doğrulanır (token çalınma koruması)",
        default=True, critical=True,
    ),
    dict(
        key="csrf_double_submit", label="CSRF Double-Submit", group="security",
        description="csrf_exempt endpoint'lerde imzalı cookie + header doğrulaması",
        default=True, critical=True,
    ),
    dict(
        key="server_authz", label="Sunucu Tarafı Yetkilendirme", group="security",
        description="Tüm istekler sunucuda doğrulanır (arayüz gizleme yetki değildir)",
        default=True, critical=True,
    ),
    dict(
        key="idor_protection", label="IDOR Koruması", group="security",
        description="Her nesne erişiminde sahiplik kontrolü", default=True, critical=True,
    ),
    dict(
        key="path_traversal_guard", label="Yol Güvenliği", group="security",
        description="Path traversal engeli (proje dizini dışına çıkış yasak)",
        default=True, critical=True,
    ),
    dict(
        key="command_allowlist", label="Komut Beyaz Listesi", group="security",
        description="Terminal komutları izinli listeyle sınırlandırılır", default=True,
    ),
    dict(
        key="login_rate_limit", label="Giriş Deneme Sınırı", group="security",
        description="Kademeli yavaşlatma + kullanici/IP kilidi (brute force koruması)",
        default=True, critical=True,
    ),
    dict(
        key="login_rate_limit_register", label="Kayıt Deneme Sınırı", group="security",
        description="Spam ve kayıt bombası koruması", default=True,
    ),
    dict(
        key="login_rate_limit_refresh", label="Refresh Deneme Sınırı", group="security",
        description="Token yenileme deneme bombası koruması", default=True,
    ),
    dict(
        key="token_rotation", label="Token Rotasyonu", group="security",
        description="Kısa ömürlü access token + her kullanımda yenilenen refresh token",
        default=True, critical=True,
    ),
    dict(
        key="token_reuse_detection", label="Token Reuse Detection", group="security",
        description="Kullanılmış refresh token tekrar sunulursa aile iptali", default=True,
    ),
    dict(
        key="transparent_token_refresh", label="Şeffaf Token Yenileme", group="security",
        description="Access token dolduğunda kullanıcı farkında olmadan yenilenir",
        default=True,
    ),
    dict(
        key="zero_trust", label="Zero Trust / Vault", group="security",
        description="Sandbox için sır yönetimi", default=False,
    ),
    dict(
        key="vulnerability_scan", label="Güvenlik Açığı Tarama", group="security",
        description="core/vulnerability ile kod analizi", default=False,
    ),
    dict(
        key="auto_testing", label="Otomatik Test (core/auto_testing)", group="security",
        description="Deploy edilen sistemi otomatik test etme", default=False,
    ),

    # ---------------- SANDBOX ----------------
    dict(
        key="sandbox", label="Kod Sandbox'ı", group="sandbox",
        description="İzole kod çalıştırma ortamı", default=False,
        paths=["/sandbox/"], critical=True,
    ),
    dict(
        key="sandbox_docker", label="Docker Sandbox", group="sandbox",
        description="Konteyner tabanlı çalıştırma", default=False,
    ),
    dict(
        key="sandbox_db", label="Veritabanı İzolasyonu", group="sandbox",
        description="Read-only replika ile sorgu", default=False,
    ),
    dict(
        key="sandbox_lsp", label="LSP Desteği", group="sandbox",
        description="Dil sunucusu entegrasyonu", default=False,
    ),
    dict(
        key="sandbox_collab", label="Ortak Düzenleme", group="sandbox",
        description="Gerçek zamanlı çoklu kullanıcı düzenleme", default=False,
    ),
    dict(
        key="approval_gates", label="Onay Kapıları", group="sandbox",
        description="Yıkıcı işlemler için onay", default=False,
    ),
]


FEATURE_MAP = {feature["key"]: feature for feature in FEATURES}