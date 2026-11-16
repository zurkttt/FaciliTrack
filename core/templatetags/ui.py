from django import template
from django.utils.html import format_html

register = template.Library()


@register.filter
def plate(facility, size=""):
    """A room code drawn as a door plate: {{ facility|plate }} or {{ facility|plate:"lg" }}.

    Teal for rooms in service, amber for rooms under maintenance or closed, gray when deactivated.
    Also accepts a plain code string.
    """
    if facility in (None, ""):
        return ""
    code = getattr(facility, "facility_code", facility)
    variant, note = "", ""
    if getattr(facility, "is_active", True) is False:
        variant, note = "plate-off", "Deactivated"
    elif getattr(facility, "status", "AVAILABLE") != "AVAILABLE":
        variant, note = "plate-amber", facility.get_status_display()
    size_class = f"plate-{size}" if size else ""
    if note:
        return format_html('<span class="plate {} {}" title="{}">{}<span class="visually-hidden"> ({})</span></span>',
                           variant, size_class, note, code, note)
    return format_html('<span class="plate {}">{}</span>', size_class, code)


@register.simple_tag(takes_context=True)
def qs_replace(context, key, value):
    """Current query string with one parameter replaced, e.g. ?{% qs_replace 'group' 'building' %}."""
    params = context["request"].GET.copy()
    params[key] = value
    params.pop("page", None)
    params.pop("export", None)
    return params.urlencode()


@register.filter
def initial(user):
    """First letter for the avatar circle."""
    name = (user.first_name or user.username or "?").strip()
    return name[:1].upper()
