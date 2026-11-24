"""
Security Patch Pipeline URL Configuration
"""
from django.urls import path
from . import patch_views

urlpatterns = [
    path('', patch_views.patch_list, name='patch_list'),
    path('submit/', patch_views.patch_submit, name='patch_submit'),
    path('<str:patch_id>/', patch_views.patch_status, name='patch_status'),
    path('<str:patch_id>/results/', patch_views.patch_test_results, name='patch_test_results'),
    path('<str:patch_id>/deploy/', patch_views.patch_deploy, name='patch_deploy'),
    path('<str:patch_id>/rollback/', patch_views.patch_rollback, name='patch_rollback'),
]