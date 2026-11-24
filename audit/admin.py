from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import AuditLog, SecurityEvent, VisitorSession


@admin.register(VisitorSession)
class VisitorSessionAdmin(ModelAdmin):
    list_display = [
        "user_display", "ip_address", "country", "language", "device",
        "os", "browser", "screen_width", "viewport_width", "request_count",
        "api_count", "error_count", "last_seen",
    ]
    list_filter = ["country", "language", "device", "os", "browser", "touch"]
    search_fields = ["user__username", "user__email", "ip_address", "user_agent", "session_key"]
    readonly_fields = [field.name for field in VisitorSession._meta.fields]
    list_per_page = 50
    ordering = ["-last_seen"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @display(description=_("Kullanıcı"))
    def user_display(self, obj):
        if obj.user:
            return f"{obj.user.get_username()} ({obj.user.email})"
        return obj.app_user_id or "Anonim"


@admin.register(AuditLog)
class AuditLogAdmin(ModelAdmin):
    list_display = [
        "created_at", "action_badge", "model_name", "object_repr_short",
        "user_link", "ip_address", "response_status", "response_time_ms", "is_security_event"
    ]
    list_filter = [
        "action",
        "model_name",
        "response_status",
        "created_at",
    ]
    search_fields = [
        "user_email", "object_repr", "ip_address",
        "request_path", "trace_id"
    ]
    readonly_fields = [
        "id", "action", "model_name", "object_id", "object_repr",
        "changes", "user", "user_email", "ip_address", "user_agent",
        "referer", "request_method", "request_path", "request_body",
        "response_status", "response_time_ms", "ai_model",
        "ai_prompt_tokens", "ai_completion_tokens", "ai_cost_usd",
        "created_at", "trace_id", "span_id"
    ]
    list_per_page = 50
    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = request.user
        
        if user.is_superuser:
            return qs
        if user.is_staff:
            return qs.exclude(user__is_superuser=True)
        return qs.filter(user=request.user)
    
    @display(description=_("Eylem"))
    def action_badge(self, obj):
        colors = {
            AuditLog.Action.CREATE: "bg-green-100 text-green-800",
            AuditLog.Action.UPDATE: "bg-blue-100 text-blue-800",
            AuditLog.Action.DELETE: "bg-red-100 text-red-800",
            AuditLog.Action.LOGIN: "bg-purple-100 text-purple-800",
            AuditLog.Action.LOGOUT: "bg-gray-100 text-gray-800",
            AuditLog.Action.LOGIN_FAILED: "bg-red-100 text-red-800",
            AuditLog.Action.ROLE_CHANGE: "bg-orange-100 text-orange-800",
            AuditLog.Action.PERMISSION_CHANGE: "bg-orange-100 text-orange-800",
        }
        color = colors.get(obj.action, "bg-gray-100 text-gray-800")
        return format_html(
            '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium {}">{}</span>',
            color, obj.get_action_display()
        )
    
    @display(description=_("Nesne"))
    def object_repr_short(self, obj):
        return obj.object_repr[:50] + "..." if len(obj.object_repr) > 50 else obj.object_repr
    
    @display(description=_("Kullanıcı"))
    def user_link(self, obj):
        if obj.user:
            from django.urls import reverse
            url = reverse("admin:auth_user_change", args=[obj.user.pk])
            return format_html('<a href="{}">{}</a>', url, obj.user.get_full_name() or obj.user.email)
        return obj.user_email or "Sistem"
    
    @display(description=_("Güvenlik"))
    def is_security_event(self, obj):
        if obj.is_security_event:
            return format_html(
                '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">⚠ Güvenlik</span>'
            )
        return format_html(
            '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">Normal</span>'
        )


@admin.register(SecurityEvent)
class SecurityEventAdmin(ModelAdmin):
    list_display = [
        "created_at", "severity_badge", "event_type", "title_short",
        "user_link", "ip_address", "is_resolved_badge"
    ]
    list_filter = [
        "severity", "event_type", "is_resolved", "created_at"
    ]
    search_fields = ["title", "description", "ip_address", "user__email"]
    readonly_fields = [
        "id", "event_type", "severity", "user", "ip_address",
        "title", "description", "details", "created_at",
        "resolved_by", "resolved_at", "resolution_notes"
    ]
    list_per_page = 50
    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    actions = ["mark_resolved", "mark_unresolved"]
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = request.user
        
        if user.is_superuser:
            return qs
        if user.is_staff:
            return qs.exclude(user__is_superuser=True)
        return qs.filter(user=request.user)
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
    
    @display(description=_("Şiddet"))
    def severity_badge(self, obj):
        colors = {
            SecurityEvent.Severity.LOW: "bg-gray-100 text-gray-800",
            SecurityEvent.Severity.MEDIUM: "bg-yellow-100 text-yellow-800",
            SecurityEvent.Severity.HIGH: "bg-orange-100 text-orange-800",
            SecurityEvent.Severity.CRITICAL: "bg-red-100 text-red-800",
        }
        color = colors.get(obj.severity, "bg-gray-100 text-gray-800")
        return format_html(
            '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium {}">{}</span>',
            color, obj.get_severity_display()
        )
    
    @display(description=_("Başlık"))
    def title_short(self, obj):
        return obj.title[:60] + "..." if len(obj.title) > 60 else obj.title
    
    @display(description=_("Kullanıcı"))
    def user_link(self, obj):
        if obj.user:
            from django.urls import reverse
            url = reverse("admin:auth_user_change", args=[obj.user.pk])
            return format_html('<a href="{}">{}</a>', url, obj.user.get_full_name() or obj.user.email)
        return "Sistem"
    
    @display(description=_("Durum"))
    def is_resolved_badge(self, obj):
        if obj.is_resolved:
            return format_html(
                '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">✓ Çözüldü</span>'
            )
        return format_html(
            '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">✗ Açık</span>'
        )
    
    @admin.action(description="Seçili olayları çözüldü olarak işaretle")
    def mark_resolved(self, request, queryset):
        updated = queryset.filter(is_resolved=False).update(
            is_resolved=True,
            resolved_by=request.user,
            resolved_at=timezone.now()
        )
        self.message_user(request, f"{updated} olay çözüldü olarak işaretlendi.")
    
    @admin.action(description="Seçili olayları açık olarak işaretle")
    def mark_unresolved(self, request, queryset):
        updated = queryset.filter(is_resolved=True).update(
            is_resolved=False,
            resolved_by=None,
            resolved_at=None
        )
        self.message_user(request, f"{updated} olay açık olarak işaretlendi.")