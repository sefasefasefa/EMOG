from django.shortcuts import redirect

from .permissions import require_auth


@require_auth
def video_page(request):
    return redirect("/studio/?tab=media")
