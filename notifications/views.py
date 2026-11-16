from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def notification_list(request):
    qs = request.user.notifications.all()
    if request.GET.get("unread"):
        qs = qs.filter(read_at__isnull=True)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "notifications/list.html", {"page_obj": page, "items": page.object_list,
                                                        "unread_only": bool(request.GET.get("unread"))})


@require_POST
@login_required
def open_notification(request, pk):
    """Mark as read, then follow its link (only site-relative links are followed)."""
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    if n.read_at is None:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    if n.url and url_has_allowed_host_and_scheme(n.url, allowed_hosts=None) and n.url.startswith("/"):
        return redirect(n.url)
    return redirect("notifications:list")


@require_POST
@login_required
def mark_all_read(request):
    count = request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    messages.success(request, f"Marked {count} notification{'s' if count != 1 else ''} as read.")
    return redirect("notifications:list")
