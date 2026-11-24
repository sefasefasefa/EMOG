"""
Approval Gates URL Configuration
"""
from django.urls import path
from . import approval_views

urlpatterns = [
    path('request/', approval_views.approval_request, name='approval_request'),
    path('<str:request_id>/', approval_views.approval_status, name='approval_status'),
    path('<str:request_id>/approve/', approval_views.approval_approve, name='approval_approve'),
    path('<str:request_id>/deny/', approval_views.approval_deny, name='approval_deny'),
    path('<str:request_id>/cancel/', approval_views.approval_cancel, name='approval_cancel'),
    path('', approval_views.approval_list, name='approval_list'),
    path('cleanup/', approval_views.approval_cleanup, name='approval_cleanup'),
    path('execute/', approval_views.approval_execute, name='approval_execute'),
]