from django.urls import path

from .views import track_client

urlpatterns = [
    path("track/", track_client, name="audit_track_client"),
]