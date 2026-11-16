from django.db import models


class SystemSetting(models.Model):
    """Configurable values (risk thresholds, defaults) managed by administrators."""

    key = models.CharField(max_length=80, unique=True)
    value = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} = {self.value}"

    @classmethod
    def get(cls, key, default=None, cast=str):
        try:
            return cast(cls.objects.get(key=key).value)
        except (cls.DoesNotExist, ValueError, TypeError):
            return default
