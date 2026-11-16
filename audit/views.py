from django.views.generic import ListView

from accounts.models import User
from accounts.permissions import RoleRequiredMixin

from .models import AuditEvent


class AuditLogView(RoleRequiredMixin, ListView):
    """Audit log viewer. Restricted to system administrators."""

    allowed_roles = [User.Role.ADMIN]
    model = AuditEvent
    template_name = "audit/audit_list.html"
    context_object_name = "events"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset().select_related("user")
        action = self.request.GET.get("action")
        q = self.request.GET.get("q", "").strip()
        if action:
            qs = qs.filter(action=action)
        if q:
            qs = qs.filter(username__icontains=q) | qs.filter(entity_repr__icontains=q)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["actions"] = AuditEvent.Action.choices
        ctx["selected_action"] = self.request.GET.get("action", "")
        ctx["q"] = self.request.GET.get("q", "")
        return ctx
