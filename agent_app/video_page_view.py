from django.shortcuts import render

from .permissions import current_user, require_auth
from .models import VideoJob


@require_auth
def video_page(request):
    user = current_user(request)
    jobs = []
    try:
        jobs = list(VideoJob.objects.filter(user=user).order_by("-created_at")[:20])
    except Exception:
        jobs = []
    return render(request, "agent_app/video.html", {
        "video_user_id": str(getattr(user, "id", "")),
        "video_jobs": jobs,
    })
