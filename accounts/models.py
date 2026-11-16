from django.contrib.auth.models import AbstractUser
from django.db import models


class Department(models.Model):
    """Institutional organizational unit (college, office, department)."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class User(AbstractUser):
    """System user with a single role used for role-based access control (RBAC)."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "System Administrator"
        FACILITIES = "FACILITIES", "Facilities Management"
        REGISTRAR = "REGISTRAR", "Registrar / Scheduler"
        PLANNER = "PLANNER", "Planning / Institutional Research"
        DEPT_HEAD = "DEPT_HEAD", "Department Head"
        VIEWER = "VIEWER", "Viewer (Faculty / Staff)"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="users"
    )
    must_change_password = models.BooleanField(
        default=False, help_text="Set when an administrator issues a temporary password."
    )

    def has_role(self, *roles):
        """True if the user holds any of the given roles. Superusers pass every check."""
        return self.is_superuser or self.role in roles

    @property
    def is_system_admin(self):
        return self.has_role(self.Role.ADMIN)
