from django.urls import path, include, re_path
from django.views.generic import RedirectView

from .views import index, runs_page, omni_health, omni_models, nvidia_models
from .views import start_task, assistant_message, task_logs, task_stream
from .views import build_run_detail, search_providers, chat_history
from .views import queue_list, tasks_list, tasks_page, runs_list, user_presence, queue_clear, queue_clear_all
from .views import tools_file_tree, tools_read_file, tools_write_file, tools_run_command
from .views import preview_analyze_design, preview_visual_qa, preview_proxy, preview_auto, preview_static, preview_set_dev_port, preview_all_active, preview_assign_port, preview_free_ports
from .views import preview_start, preview_site
from .views import terms_page, privacy_page
from .views import projects_page, landing_page
from .github import (
    github_page, github_status, github_repositories, github_disconnect,
    github_device_start, github_device_poll, github_oauth_start, github_create,
)
from .pricing_page_view import pricing_page
from .whatsapp_page_view import whatsapp_page
from .video_page_view import video_page
from .whatsapp_views import WhatsAppPairView
from .neural_core import views as neural_views
from . import video_views
from . import image_views

from .auth_views import (
    login_page, register_page, oauth_login, oauth_callback,
    register, login, logout_view, profile_view, auth_status,
    verify_email, resend_verification_email, refresh_token, user_settings
)

urlpatterns = [
    path('', landing_page, name='home'),
    path('pricing/', pricing_page, name='pricing'),
    path('terms/', terms_page, name='terms'),
    path('privacy/', privacy_page, name='privacy'),
    path('chat/', index, name='chat'),
    path('runs/', runs_page, name='runs_page'),
    path('omni/health/', omni_health, name='omni_health'),
    path('omni/models/', omni_models, name='omni_models'),
    path('nvidia/models/', nvidia_models, name='nvidia_models'),
    path('run_task/', start_task, name='run_task'),
    path('assistant/message/', assistant_message, name='assistant_message'),
    path('task/logs/<str:run_id>/', task_logs, name='task_logs'),
    path('task/stream/<str:run_id>/', task_stream, name='task_stream'),

    # Otonom proje uretimi (buildkit)
    path('build/run/<str:run_id>/', build_run_detail, name='build_run_detail'),
    path('chat/history/', chat_history, name='chat_history'),
    path('build/providers/', search_providers, name='search_providers'),

    # Kuyruk / görev / koşu listeleri
    path('queue/list/', queue_list, name='queue_list'),
    path('queue/clear/', queue_clear, name='queue_clear'),
    path('queue/clear-all/', queue_clear_all, name='queue_clear_all'),
    path('tasks/list/', tasks_list, name='tasks_list'),
    path('tasks/', tasks_page, name='tasks_page'),
    path('runs/list/', runs_list, name='runs_list'),
    path('user/presence/', user_presence, name='user_presence'),

    # Tools paneli (dosya ağacı / oku / yaz)
    path('tools/file-tree/', tools_file_tree, name='tools_file_tree'),
    path('tools/read-file/', tools_read_file, name='tools_read_file'),
    path('tools/write-file/', tools_write_file, name='tools_write_file'),
    path('tools/run-command/', tools_run_command, name='tools_run_command'),

    # Preview yardımcıları (legacy - deprecated)
    path('preview/auto/', preview_auto, name='preview_auto'),
    path('preview/start/', preview_start, name='preview_start'),
    re_path(r'^preview/site/(?P<project_id>[^/]+)/(?P<asset_path>.*)$', preview_site, name='preview_site'),
    path('preview/analyze-design/', preview_analyze_design, name='preview_analyze_design'),
    path('preview/visual-qa/', preview_visual_qa, name='preview_visual_qa'),
    path('preview/proxy/', preview_proxy, name='preview_proxy'),
    path('preview/static/', preview_static, name='preview_static'),
    path('preview/set-dev-port/', preview_set_dev_port, name='preview_set_dev_port'),
    path('preview/assign-port/', preview_assign_port, name='preview_assign_port'),
    path('preview/free-ports/', preview_free_ports, name='preview_free_ports'),

    # GitHub entegrasyonu
    path('github/', github_page, name='github_page'),
    path('github/status/', github_status, name='github_status'),
    path('github/repos/', github_repositories, name='github_repositories'),
    path('github/create/', github_create, name='github_create'),
    path('github/disconnect/', github_disconnect, name='github_disconnect'),
    path('github/device/start/', github_device_start, name='github_device_start'),
    path('github/device/poll/', github_device_poll, name='github_device_poll'),
    path('github/oauth/start/', github_oauth_start, name='github_oauth_start'),

    # WhatsApp kısayolları (chat paneli bunları çağırıyor)
    path('whatsapp/status/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppStatusView']).WhatsAppStatusView.as_view(), name='whatsapp_status_short'),
    path('whatsapp/pair/', WhatsAppPairView.as_view(), name='whatsapp_pair_short'),
    path('whatsapp/connect/', __import__('agent_app.whatsapp', fromlist=['whatsapp_connect']).whatsapp_connect, name='whatsapp_connect_short'),
    path('whatsapp/disconnect/', __import__('agent_app.whatsapp', fromlist=['whatsapp_disconnect']).whatsapp_disconnect, name='whatsapp_disconnect_short'),
    path('whatsapp/send/', __import__('agent_app.whatsapp', fromlist=['whatsapp_send']).whatsapp_send, name='whatsapp_send_short'),

    path('projects/', projects_page, name='projects_page'),

    # NEURAL CORE (bilişsel çekirdek)
    path('neural/', neural_views.neural_page, name='neural_page'),
    path('api/neural/status/', neural_views.neural_status, name='neural_status'),
    path('api/neural/runs/', neural_views.neural_runs_list, name='neural_runs'),
    path('api/neural/run/', neural_views.neural_run_create, name='neural_run_create'),
    path('api/neural/run/<str:run_id>/', neural_views.neural_run_detail, name='neural_run_detail'),
    path('api/neural/preferences/', neural_views.neural_preferences_api, name='neural_preferences'),
    path('api/neural/proposals/', neural_views.neural_proposals_api, name='neural_proposals'),
    path(
        'api/neural/proposals/<str:proposal_id>/<str:action>/',
        neural_views.neural_proposal_decide,
        name='neural_proposal_decide',
    ),
    path('api/neural/tools/', neural_views.neural_tools_api, name='neural_tools'),
    path('api/neural/tools/<str:action>/', neural_views.neural_tool_action, name='neural_tool_action'),
    path(
        'api/neural/tools/approvals/<str:approval_id>/decide/',
        neural_views.neural_tool_decide,
        name='neural_tool_decide',
    ),
    path('api/neural/goals/', neural_views.neural_goals_api, name='neural_goals'),
    path(
        'api/neural/goals/<str:goal_id>/toggle/',
        neural_views.neural_goal_toggle,
        name='neural_goal_toggle',
    ),

    # Video generation API
    path('api/video/generate/', video_views.video_generate, name='video_generate'),
    path('api/video/upload/', video_views.video_upload, name='video_upload'),
    path('api/video/<str:job_id>/download/', video_views.video_download, name='video_download'),
    path('api/video/<str:job_id>/edit/', video_views.video_edit, name='video_edit'),
    path('api/video/<str:job_id>/', video_views.video_detail, name='video_detail'),
    path('api/video/', video_views.video_list, name='video_list'),

    # Image generation API
    path('api/image/generate/', image_views.image_generate, name='image_generate'),
    path('api/image/<str:job_id>/download/', image_views.image_download, name='image_download'),
    path('api/image/<str:job_id>/', image_views.image_detail, name='image_detail'),
    path('api/image/', image_views.image_list, name='image_list'),
    
    # Auth routes
    # Eski Django varsayılanıyla gelen bağlantılar için uyumluluk yönlendirmesi.
    path(
        'accounts/login/',
        RedirectView.as_view(pattern_name='login_page', permanent=False, query_string=True),
        name='accounts_login',
    ),
    path('auth/login/', login_page, name='login_page'),
    path('auth/register/', register_page, name='register_page'),
    path('auth/oauth/login/<str:provider>/', oauth_login, name='oauth_login'),
    path('auth/oauth/callback/<str:provider>/', oauth_callback, name='oauth_callback'),
    path('auth/register/api/', register, name='register_api'),
    path('auth/login/api/', login, name='login_api'),
    path('auth/logout/', logout_view, name='logout'),
    path('auth/profile/', profile_view, name='profile'),
    path('auth/status/', auth_status, name='auth_status'),
    path('auth/settings/', user_settings, name='user_settings'),
    path('auth/refresh/', refresh_token, name='refresh_token'),
    path('auth/verify-email/', verify_email, name='verify_email'),
    path('auth/resend-verification/', resend_verification_email, name='resend_verification_email'),
    
    # Sandbox/Quarantine Workspace API (includes Conversation Time Travel)
    path('sandbox/', include('agent_app.sandbox.urls')),
    
    # WhatsApp Integration API
    path('api/whatsapp/status/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppStatusView']).WhatsAppStatusView.as_view(), name='whatsapp_status'),
    path('api/whatsapp/qr/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppQRView']).WhatsAppQRView.as_view(), name='whatsapp_qr'),
    path('api/whatsapp/init/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppInitView']).WhatsAppInitView.as_view(), name='whatsapp_init'),
    path('api/whatsapp/disconnect/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppDisconnectView']).WhatsAppDisconnectView.as_view(), name='whatsapp_disconnect'),
    path('api/whatsapp/send/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppSendView']).WhatsAppSendView.as_view(), name='whatsapp_send'),
    path('api/whatsapp/chats/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppChatsView']).WhatsAppChatsView.as_view(), name='whatsapp_chats'),
    path('api/whatsapp/contacts/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppContactsView']).WhatsAppContactsView.as_view(), name='whatsapp_contacts'),
    path('api/whatsapp/messages/<str:chat_id>/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppMessagesView']).WhatsAppMessagesView.as_view(), name='whatsapp_messages'),
    path('api/whatsapp/chat/<str:chat_id>/<str:action>/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppChatActionsView']).WhatsAppChatActionsView.as_view(), name='whatsapp_chat_actions'),
    path('api/whatsapp/webhook/', __import__('agent_app.whatsapp_views', fromlist=['WhatsAppWebhookView']).WhatsAppWebhookView.as_view(), name='whatsapp_webhook'),
    
    # Video generation Page
    path('video/', video_page, name='video_page'),

    # WhatsApp Page
    path('whatsapp/', whatsapp_page, name='whatsapp_page'),
]