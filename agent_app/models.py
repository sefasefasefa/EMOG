from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid


class User(models.Model):
    """Custom user model with OAuth support"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    username = models.CharField(max_length=100, unique=True)
    password_hash = models.CharField(max_length=255, blank=True, null=True)
    full_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(max_length=500, blank=True)
    provider = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    provider_id = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    is_active = models.BooleanField(default=True)
    is_superuser = models.BooleanField(default=False)
    # Tarayıcı kapansa da arka plan görevleri devam etsin mi?
    # Varsayılan: hayır. Yalnızca açıkça etkinleştiren hesaplarda çalışır.
    continue_in_background = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    email_verification_token = models.CharField(max_length=255, blank=True, null=True)
    email_verification_sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'users'
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['username']),
            models.Index(fields=['provider']),
            models.Index(fields=['provider_id']),
            models.Index(fields=['email_verification_token']),
        ]

    def __str__(self):
        return self.email


class RefreshToken(models.Model):
    """
    Refresh token kaydı (rotating).

    Token düz metin olarak saklanmaz, sadece SHA-256 hash'i tutulur.
    Her kullanımda `rotated_at` dolar ve yeni bir token üretilir.
    Kullanılmış token tekrar gönderilirse aile iptal edilir (reuse detection).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="refresh_tokens"
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)

    # Token ailesi: rotasyon boyunca aynı kalır, hırsızlıkta tüm aile iptal edilir
    family = models.CharField(max_length=64, db_index=True)
    session = models.CharField(max_length=32, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    is_revoked = models.BooleanField(default=False, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=32, blank=True)

    user_agent = models.CharField(max_length=400, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "refresh_tokens"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_revoked"]),
            models.Index(fields=["family", "is_revoked"]),
            models.Index(fields=["session", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user.email} | {'revoked' if self.is_revoked else 'active'} | {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def is_expired(self):
        from django.utils import timezone

        return self.expires_at <= timezone.now()


class Conversation(models.Model):
    """Root conversation container"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversations',
        null=True,
        blank=True
    )
    # Uygulama kullanıcısı (agent_app.User) - sahiplik kontrolü için
    app_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='conversations', null=True, blank=True
    )
    title = models.CharField(max_length=500, blank=True)
    meta_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'conversations'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', '-updated_at']),
            models.Index(fields=['app_user', '-updated_at']),
        ]

    def __str__(self):
        return self.title or f'Conversation {self.id}'


class Node(models.Model):
    """Tree node representing a message in the conversation"""
    ROLE_CHOICES = [
        ('system', 'System'),
        ('user', 'User'),
        ('assistant', 'Assistant'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='nodes'
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        related_name='children',
        null=True,
        blank=True
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    tokens_used = models.IntegerField(default=0)
    model = models.CharField(max_length=200, blank=True)
    meta_data = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'nodes'
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['conversation', 'parent']),
            models.Index(fields=['conversation', 'timestamp']),
        ]

    def __str__(self):
        return f'{self.role}: {self.content[:50]}'

    def get_path_to_root(self):
        """Return list of nodes from root to this node (inclusive)"""
        path = []
        current = self
        while current:
            path.append(current)
            current = current.parent
        return list(reversed(path))

    def get_descendants(self, include_self=False):
        """Get all descendant nodes"""
        descendants = []
        if include_self:
            descendants.append(self)
        for child in self.children.all():
            descendants.extend(child.get_descendants(include_self=True))
        return descendants


class Checkpoint(models.Model):
    """Snapshot of conversation state at a specific node"""
    SNAPSHOT_TYPES = [
        ('full', 'Full State'),
        ('settings', 'Agent Settings'),
        ('context', 'System Context'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node = models.OneToOneField(
        Node,
        on_delete=models.CASCADE,
        related_name='checkpoint'
    )
    snapshot_type = models.CharField(max_length=20, choices=SNAPSHOT_TYPES, default='full')
    snapshot_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'checkpoints'
        ordering = ['-created_at']

    def __str__(self):
        return f'Checkpoint for {self.node} ({self.snapshot_type})'


class ErrorReport(models.Model):
    """Server-side error reports with full request context."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.CharField(max_length=32, db_index=True)
    exception_type = models.CharField(max_length=128)
    exception_message = models.TextField()
    traceback = models.TextField()
    context = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved = models.BooleanField(default=False, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "error_reports"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["request_id", "-created_at"]),
            models.Index(fields=["exception_type", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.exception_type}: {self.exception_message[:80]}"


class ClientErrorReport(models.Model):
    """Client-side error reports with full user action history."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_id = models.CharField(max_length=64, db_index=True)
    request_id = models.CharField(max_length=32, db_index=True)
    error_message = models.TextField()
    error_stack = models.TextField(blank=True)
    error_source = models.CharField(max_length=32)  # unhandled, promise, console, etc.
    client_logs = models.JSONField(default=list)  # Full action history
    checkpoints = models.JSONField(default=list)  # 30-step sync points
    user_agent = models.TextField(blank=True)
    url = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved = models.BooleanField(default=False, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "client_error_reports"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session_id", "-created_at"]),
            models.Index(fields=["request_id", "-created_at"]),
        ]

    def __str__(self):
        return f"ClientError: {self.error_message[:80]} (session={self.session_id[:8]})"


class WhatsAppSession(models.Model):
    """User's WhatsApp Web session - each user connects their own account."""
    STATUS_CHOICES = [
        ('not_initialized', 'Not Initialized'),
        ('initializing', 'Initializing'),
        ('qr_ready', 'QR Code Ready'),
        ('authenticated', 'Authenticated'),
        ('connected', 'Connected'),
        ('disconnected', 'Disconnected'),
        ('auth_failed', 'Auth Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='whatsapp_session'
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='not_initialized', db_index=True)
    
    # WhatsApp account info (populated when connected)
    phone_number = models.CharField(max_length=32, blank=True)
    pushname = models.CharField(max_length=255, blank=True)
    platform = models.CharField(max_length=64, blank=True)
    wid = models.CharField(max_length=64, blank=True)  # WhatsApp ID
    
    # Session data
    session_data = models.JSONField(default=dict, blank=True)  # Encrypted session backup
    last_qr_code = models.TextField(blank=True)  # Current QR code for display
    last_qr_at = models.DateTimeField(null=True, blank=True)
    
    # Connection tracking
    connected_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    
    # Settings
    auto_reconnect = models.BooleanField(default=True)
    notify_on_message = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_sessions'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', '-updated_at']),
        ]

    def __str__(self):
        return f"WhatsApp: {self.user.email} ({self.status})"

    @property
    def is_connected(self):
        return self.status == 'connected'

    def update_from_client(self, client_info):
        """Update from WhatsApp client info."""
        self.status = 'connected'
        self.phone_number = client_info.get('phone', '')
        self.pushname = client_info.get('pushname', '')
        self.platform = client_info.get('platform', '')
        self.wid = client_info.get('wid', '')
        self.connected_at = timezone.now()
        self.last_activity_at = timezone.now()
        self.save(update_fields=[
            'status', 'phone_number', 'pushname', 'platform', 'wid',
            'connected_at', 'last_activity_at', 'updated_at'
        ])

    def set_qr_code(self, qr_code):
        """Store QR code for display."""
        self.last_qr_code = qr_code
        self.last_qr_at = timezone.now()
        self.status = 'qr_ready'
        self.save(update_fields=['last_qr_code', 'last_qr_at', 'status', 'updated_at'])

    def set_disconnected(self, reason=''):
        """Mark as disconnected."""
        self.status = 'disconnected'
        self.disconnected_at = timezone.now()
        self.session_data = {'disconnect_reason': reason}
        self.save(update_fields=['status', 'disconnected_at', 'session_data', 'updated_at'])


class WhatsAppMessage(models.Model):
    """WhatsApp message history for each user."""
    DIRECTION_CHOICES = [
        ('inbound', 'Inbound'),
        ('outbound', 'Outbound'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        WhatsAppSession, on_delete=models.CASCADE, related_name='messages'
    )
    
    # Message identification
    whatsapp_id = models.CharField(max_length=64, db_index=True)  # Message ID from WhatsApp
    chat_id = models.CharField(max_length=64, db_index=True)  # Chat ID (e.g., 123456789@c.us)
    
    # Message content
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    message_type = models.CharField(max_length=32)  # text, image, video, document, audio, etc.
    body = models.TextField(blank=True)
    
    # Media (if applicable)
    media_mimetype = models.CharField(max_length=128, blank=True)
    media_data = models.TextField(blank=True)  # Base64 encoded (or path to stored file)
    media_filename = models.CharField(max_length=255, blank=True)
    media_url = models.URLField(blank=True)  # Original WhatsApp media URL
    
    # Contact info
    from_number = models.CharField(max_length=32, db_index=True)
    from_name = models.CharField(max_length=255, blank=True)
    to_number = models.CharField(max_length=32, blank=True)
    
    # Chat info
    chat_name = models.CharField(max_length=255, blank=True)
    chat_is_group = models.BooleanField(default=False)
    
    # Timestamps
    timestamp = models.DateTimeField(db_index=True)  # When message was sent/received
    ack_timestamp = models.DateTimeField(null=True, blank=True)  # When ack received
    
    # Metadata
    raw_data = models.JSONField(default=dict, blank=True)  # Full raw message data
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_messages'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['session', 'chat_id', '-timestamp']),
            models.Index(fields=['session', 'direction', '-timestamp']),
            models.Index(fields=['session', 'status', '-timestamp']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['whatsapp_id'], name='unique_whatsapp_message_id')
        ]

    def __str__(self):
        return f"{self.direction}: {self.body[:50]} ({self.chat_id})"


class WhatsAppChat(models.Model):
    """WhatsApp chat list for each user."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        WhatsAppSession, on_delete=models.CASCADE, related_name='chats'
    )
    
    # Chat identification
    chat_id = models.CharField(max_length=64, db_index=True)  # e.g., 123456789@c.us or 123456789-123456@g.us
    name = models.CharField(max_length=255, blank=True)
    is_group = models.BooleanField(default=False)
    
    # Group info (if applicable)
    group_owner = models.CharField(max_length=32, blank=True)
    group_participants = models.JSONField(default=list, blank=True)
    
    # Last message preview
    last_message_body = models.TextField(blank=True)
    last_message_from_me = models.BooleanField(default=False)
    last_message_at = models.DateTimeField(null=True, blank=True)
    unread_count = models.IntegerField(default=0)
    
    # Settings
    is_archived = models.BooleanField(default=False)
    is_muted = models.BooleanField(default=False)
    mute_until = models.DateTimeField(null=True, blank=True)
    is_pinned = models.BooleanField(default=False)
    
    # Metadata
    raw_data = models.JSONField(default=dict, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_chats'
        ordering = ['-is_pinned', '-last_message_at', '-updated_at']
        indexes = [
            models.Index(fields=['session', 'is_archived', '-last_message_at']),
            models.Index(fields=['session', 'unread_count', '-last_message_at']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['session', 'chat_id'], name='unique_session_chat')
        ]

    def __str__(self):
        return f"Chat: {self.name or self.chat_id} ({'Group' if self.is_group else 'Private'})"


class WhatsAppContact(models.Model):
    """WhatsApp contacts for each user."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        WhatsAppSession, on_delete=models.CASCADE, related_name='contacts'
    )
    
    # Contact identification
    contact_id = models.CharField(max_length=64, db_index=True)  # e.g., 123456789@c.us
    number = models.CharField(max_length=32, db_index=True)
    name = models.CharField(max_length=255, blank=True)
    pushname = models.CharField(max_length=255, blank=True)
    
    # Profile
    profile_pic_url = models.URLField(blank=True)
    status = models.TextField(blank=True)
    is_business = models.BooleanField(default=False)
    is_enterprise = models.BooleanField(default=False)
    
    # Metadata
    raw_data = models.JSONField(default=dict, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_contacts'
        ordering = ['name', 'pushname', 'number']
        indexes = [
            models.Index(fields=['session', 'number']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['session', 'contact_id'], name='unique_session_contact')
        ]

    def __str__(self):
        return f"Contact: {self.name or self.pushname or self.number}"


# ======================================================================
# Otonom proje uretimi (buildkit)
# ======================================================================
class BuildRun(models.Model):
    """Tek bir "proje yap" uretim kosusu.

    Dosya tarafi `task_runs/<run_id>.{plan,research,benchmark,blueprint}.json`
    ve `task_runs/<run_id>.log`; buradaki kayit sorgulanabilir/raporlanabilir
    tarafi. Pipeline DB'ye yazamazsa (migration uygulanmamissa) sessizce
    devam eder, dosya tarafi yetalidir.
    """

    STAGE_CHOICES = [
        ("research", "Arastirma"),
        ("benchmark", "Rakip analizi"),
        ("blueprint", "Spesifikasyon"),
        ("planning", "Plan"),
        ("building", "Uygulama"),
        ("reviewing", "Eleştiri"),
        ("polishing", "Son kontrol"),
        ("completed", "Tamamlandi"),
        ("partial", "Kismi"),
        ("aborted", "Durduruldu"),
        ("failed", "Hata"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_id = models.CharField(max_length=32, unique=True, db_index=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="build_runs", null=True, blank=True,
    )
    project_id = models.CharField(max_length=64, blank=True, db_index=True)
    user_id_str = models.CharField(max_length=64, blank=True, db_index=True)

    request = models.TextField(blank=True)
    model = models.CharField(max_length=160, blank=True)

    status = models.CharField(max_length=24, default="queued", db_index=True)
    stage = models.CharField(max_length=24, choices=STAGE_CHOICES, default="research", db_index=True)
    progress = models.PositiveSmallIntegerField(default=0)
    rounds = models.PositiveSmallIntegerField(default=0)
    plan_total = models.PositiveIntegerField(default=0)

    # Kullaniciya teslim edilen kararlar
    user_requested_features = models.JSONField(default=list, blank=True)
    tech_stack = models.JSONField(default=dict, blank=True)
    blueprint = models.JSONField(default=dict, blank=True)
    research_summary = models.TextField(blank=True)
    benchmark_summary = models.JSONField(default=list, blank=True)
    summary = models.TextField(blank=True)
    stats = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "build_runs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["project_id", "-created_at"]),
        ]

    def __str__(self):
        return f"BuildRun {self.run_id[:8]} ({self.stage}/{self.status})"

    @property
    def is_finished(self):
        return self.status in ("completed", "partial", "aborted", "failed")


class ResearchDossier(models.Model):
    """Arastirma dosyasi (ne ogrenildi)."""

    # Baglanti FK yerine run_id (string) uzerinden kurulur: constraint ve
    # index'ler run_id uzerine kurulu, pipeline satirlari string ile eslesir.
    run_id = models.CharField(max_length=32, db_index=True)

    domain = models.CharField(max_length=200, blank=True)
    platform = models.CharField(max_length=32, blank=True)
    summary = models.TextField(blank=True)
    how_it_works = models.TextField(blank=True)
    domain_glossary = models.JSONField(default=list, blank=True)
    must_have_features = models.JSONField(default=list, blank=True)
    advanced_features = models.JSONField(default=list, blank=True)
    pitfalls = models.JSONField(default=list, blank=True)
    tech_recommendations = models.JSONField(default=list, blank=True)
    ux_principles = models.JSONField(default=list, blank=True)
    quality_checklists = models.JSONField(default=dict, blank=True)
    differentiation = models.JSONField(default=list, blank=True)
    open_questions = models.JSONField(default=list, blank=True)
    queries = models.JSONField(default=list, blank=True)
    stats = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "research_dossiers"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["run_id", "-created_at"])]

    def __str__(self):
        return f"Dossier: {self.domain or self.run_id[:8]}"


class ResearchSource(models.Model):
    """Arastirmada okunan tek bir kaynak."""

    dossier = models.ForeignKey(
        ResearchDossier, on_delete=models.CASCADE, related_name="sources", null=True, blank=True,
    )
    run_id = models.CharField(max_length=32, db_index=True)
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=400, blank=True)
    domain = models.CharField(max_length=200, blank=True)
    provider = models.CharField(max_length=40, blank=True)
    query = models.CharField(max_length=400, blank=True)
    notes = models.JSONField(default=dict, blank=True)
    relevance = models.FloatField(default=0.0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "research_sources"
        ordering = ["-relevance", "domain"]
        indexes = [
            models.Index(fields=["run_id", "domain"]),
            models.Index(fields=["dossier", "-relevance"]),
        ]

    def __str__(self):
        return f"{self.domain or self.url[:40]} ({self.provider or '-'})"


class BuildPlanItem(models.Model):
    """Plan kalemi. Durumu worker her adimda guncelleyerek yazar."""

    STATUS_CHOICES = [
        ("pending", "Bekliyor"),
        ("in_progress", "Uygulaniyor"),
        ("done", "Tamamlandi"),
        ("failed", "Basarisiz"),
        ("skipped", "Atlandi"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Baglanti FK yerine run_id (string) uzerinden kurulur: unique constraint
    # ve index'ler run_id uzerine kurulu, pipeline satirlari string ile eslesir.
    run_id = models.CharField(max_length=32, db_index=True)

    key = models.CharField(max_length=64, db_index=True)
    order = models.PositiveIntegerField(default=0)
    phase = models.CharField(max_length=32, default="foundation", db_index=True)
    source = models.CharField(max_length=16, default="plan")

    title = models.CharField(max_length=300)
    objective = models.TextField(blank=True)
    detail = models.TextField(blank=True)
    acceptance = models.JSONField(default=list, blank=True)
    expected_files = models.JSONField(default=list, blank=True)
    verify_command = models.CharField(max_length=220, blank=True)
    depends_on = models.JSONField(default=list, blank=True)
    complexity = models.PositiveSmallIntegerField(default=3)
    user_requested = models.BooleanField(default=False, db_index=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    result_summary = models.TextField(blank=True)
    verify_output = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "build_plan_items"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["run_id", "key"], name="unique_plan_item_key"),
        ]
        indexes = [
            models.Index(fields=["run_id", "order"]),
            models.Index(fields=["run_id", "status"]),
        ]

    def __str__(self):
        mark = {"done": "✓", "failed": "✗", "in_progress": "→"}.get(self.status, " ")
        return f"{mark} [{self.phase}] {self.title}"


# ======================================================================
# Video uretimi işleri
# ======================================================================
class VideoJob(models.Model):
    """Bir video uretim/duzenleme isi.

    Uzun suren isler arka plan thread'inde calisir; bu kayit durum/ilerleme
    ve cikti yolu icin tek kaynak (source of truth) olarak kullanilir.
    """

    MODE_CHOICES = [
        ("generate", "Uret"),
        ("edit", "Duzenle"),
        ("text_to_video", "Metinden video"),
        ("image_to_video", "Gorselden video"),
        ("slideshow", "Slayt"),
    ]
    STATUS_CHOICES = [
        ("pending", "Bekliyor"),
        ("queued", "Kuyrukta"),
        ("running", "Calisiyor"),
        ("processing", "Isleniyor"),
        ("completed", "Tamamlandi"),
        ("failed", "Hata"),
        ("cancelled", "Iptal"),
    ]
    FORMAT_CHOICES = [
        ("mp4", "MP4"),
        ("gif", "GIF"),
        ("webm", "WebM"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="video_jobs",
        null=True, blank=True,
    )

    prompt = models.TextField(blank=True)
    mode = models.CharField(
        max_length=24, choices=MODE_CHOICES, default="generate", db_index=True
    )
    provider = models.CharField(max_length=32, blank=True, db_index=True)

    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    progress = models.PositiveSmallIntegerField(default=0)

    # Uretim parametreleri
    duration = models.PositiveIntegerField(default=10)
    width = models.PositiveIntegerField(default=1920)
    height = models.PositiveIntegerField(default=1080)
    fps = models.PositiveSmallIntegerField(default=24)
    format = models.CharField(
        max_length=8, choices=FORMAT_CHOICES, default="mp4"
    )

    # Dosya yollari (media/ altinda, traversal reddedilir)
    output_path = models.CharField(max_length=500, blank=True)
    input_path = models.CharField(max_length=500, blank=True)
    thumbnail_path = models.CharField(max_length=500, blank=True)

    # Duzenleme istegi
    instruction = models.TextField(blank=True)

    # Sonuc / hata
    error = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    scenes = models.JSONField(default=list, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "video_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["mode", "-created_at"]),
        ]

    def __str__(self):
        return f"VideoJob {str(self.id)[:8]} ({self.mode}/{self.status})"

    @property
    def is_finished(self):
        return self.status in ("completed", "failed", "cancelled")

    # --- Uyumluluk kisayollari -------------------------------------------
    @property
    def resolution(self):
        return (self.width, self.height)

    @resolution.setter
    def resolution(self, value):
        if value is None:
            return
        if isinstance(value, str):
            parts = value.lower().replace("x", ",").split(",")
            if len(parts) >= 2:
                self.width = int(float(parts[0].strip()))
                self.height = int(float(parts[1].strip()))
            return
        if isinstance(value, (tuple, list)):
            if len(value) >= 2:
                self.width = int(value[0])
                self.height = int(value[1])
            return
        if isinstance(value, dict):
            if "width" in value:
                self.width = int(value["width"])
            if "height" in value:
                self.height = int(value["height"])
            return

    @property
    def fmt(self):
        return self.format

    @fmt.setter
    def fmt(self, value):
        if value:
            self.format = str(value).lower()

    @property
    def file_format(self):
        return self.format

    @file_format.setter
    def file_format(self, value):
        if value:
            self.format = str(value).lower()

    @property
    def output(self):
        return self.output_path

    @output.setter
    def output(self, value):
        self.output_path = value or ""


# NEURAL CORE modelleri (agent_app uygulamasına bağlı)
from .neural_core.models import (  # noqa: E402,F401
    NeuralDiscovery,
    NeuralExperience,
    NeuralGoal,
    NeuralPreference,
    NeuralProposal,
    NeuralRun,
    NeuralToolApproval,
)