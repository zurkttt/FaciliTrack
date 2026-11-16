"""Create alerts. Call these from views/engines when something needs a person's attention.

    notify(user, "Title", "Message", url="/risks/3/", level=Notification.Level.CRITICAL)
    notify_roles([User.Role.FACILITIES], ...)

Email is sent as well when settings.NOTIFY_BY_EMAIL is True and the user has an address.
In development the console email backend prints emails to the terminal instead of sending them.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction

from accounts.models import User

from .models import Notification

log = logging.getLogger(__name__)
Level = Notification.Level


def notify(users, title, message="", url="", level=Level.INFO, dedupe_key=""):
    """Notify one user or an iterable of users. Returns the number of alerts created."""
    if isinstance(users, User):
        users = [users]
    created = 0
    for user in {u.pk: u for u in users if u is not None and u.is_active}.values():
        if dedupe_key and Notification.objects.filter(user=user, dedupe_key=dedupe_key).exists():
            continue
        try:
            with transaction.atomic():
                Notification.objects.create(user=user, title=title[:200], message=message, url=url,
                                            level=level, dedupe_key=dedupe_key[:120])
        except IntegrityError:  # same key created concurrently
            continue
        created += 1
        if getattr(settings, "NOTIFY_BY_EMAIL", False) and user.email:
            _email(user, title, message, url)
    return created


def notify_roles(roles, title, message="", url="", level=Level.INFO, dedupe_key="", exclude=None):
    users = User.objects.filter(role__in=roles, is_active=True)
    if exclude is not None:
        users = users.exclude(pk=exclude.pk)
    return notify(users, title, message, url, level, dedupe_key)


def _email(user, title, message, url):
    link = f"{settings.SITE_URL.rstrip('/')}{url}" if url else settings.SITE_URL
    body = f"{message}\n\nOpen in FaciliTrack: {link}\n\nYou receive this because of your role in FaciliTrack."
    try:
        send_mail(f"[FaciliTrack] {title}", body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
    except Exception:  # an email failure must never break the action that triggered it
        log.exception("Could not email notification to user %s", user.pk)
