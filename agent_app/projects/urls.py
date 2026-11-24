from django.urls import path
from . import views

urlpatterns = [
    path('api/list/', views.api_list, name='projects_list'),
    path('api/create/', views.api_create, name='projects_create'),
    path('api/import/', views.api_import, name='projects_import'),
    path('api/<str:project_id>/integrations/whatsapp/', views.api_whatsapp_integration, name='projects_whatsapp_integration'),
    path('api/<str:project_id>/integrations/whatsapp/test/', views.api_whatsapp_test, name='projects_whatsapp_test'),
    path('api/<str:project_id>/', views.api_get_project, name='projects_get'),
    path('api/<str:project_id>/delete/', views.api_delete_project, name='projects_delete'),
    path('api/<str:project_id>/files/', views.api_file_list, name='projects_files'),
    path('api/<str:project_id>/files/read/', views.api_file_read, name='projects_file_read'),
    path('api/<str:project_id>/files/write/', views.api_file_write, name='projects_file_write'),
    path('api/<str:project_id>/files/delete/', views.api_file_delete, name='projects_file_delete'),
    path('api/<str:project_id>/terminal/', views.api_terminal, name='projects_terminal'),
    path('api/<str:project_id>/git/status/', views.api_git_status, name='projects_git_status'),
    path('api/<str:project_id>/git/commit/', views.api_git_commit, name='projects_git_commit'),
    path('api/<str:project_id>/git/push/', views.api_git_push, name='projects_git_push'),
    path('api/<str:project_id>/git/pull/', views.api_git_pull, name='projects_git_pull'),
    path('api/<str:project_id>/git/log/', views.api_git_log, name='projects_git_log'),
    path('api/<str:project_id>/files/', views.api_project_files, name='projects_files_alias'),
    path('api/<str:project_id>/files/write/', views.api_file_write, name='projects_file_write'),
    path('api/<str:project_id>/files/delete/', views.api_file_delete, name='projects_file_delete'),
    path('api/<str:project_id>/terminal/', views.api_terminal, name='projects_terminal'),
]