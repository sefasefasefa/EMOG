"""
Database Isolation URL Configuration
"""
from django.urls import path
from . import db_views

urlpatterns = [
    path('query/', db_views.db_query, name='db_query'),
    path('transaction/', db_views.db_transaction, name='db_transaction'),
    path('schema/', db_views.db_schema, name='db_schema'),
    path('tables/', db_views.db_tables, name='db_tables'),
]