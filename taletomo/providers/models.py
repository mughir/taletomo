from decimal import Decimal
from django.conf import settings
from django.db import models
from taletomo.core.crypto import get_secret_box, mask_secret
from taletomo.core.models import UUIDModel


class ProviderType(models.TextChoices):
    FAKE = "fake", "Fake / Mock Provider (Testing)"
    OPENAI_COMPATIBLE = "openai_compatible", "OpenAI Compatible Endpoint"
    NATIVE = "native", "Native Provider"


class ProviderConfig(UUIDModel):
    """Configuration and credentials for an AI provider."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="provider_configs",
    )
    name = models.CharField(max_length=100, default="Default Provider")
    provider_type = models.CharField(
        max_length=30, choices=ProviderType.choices, default=ProviderType.FAKE
    )
    endpoint_url = models.CharField(
        max_length=500,
        blank=True,
        default="https://api.openai.com/v1",
        help_text="Base URL for the provider endpoint",
    )
    encrypted_api_key = models.TextField(blank=True, default="")
    key_version = models.CharField(max_length=20, default="v1")

    # Model profiles per operational role
    default_planning_model = models.CharField(max_length=100, default="mock-planning-v1")
    default_drafting_model = models.CharField(max_length=100, default="mock-drafting-v1")
    default_critique_model = models.CharField(max_length=100, default="mock-critique-v1")

    # Detailed capability profiles: { model_name: { context_limit: int, max_output: int, pricing: {...} } }
    model_profiles = models.JSONField(
        default=dict,
        blank=True,
        help_text="Verified or asserted model capabilities and pricing",
    )

    # Cost and token limits
    per_job_token_limit = models.PositiveIntegerField(default=128000)
    daily_token_limit = models.PositiveIntegerField(default=1000000)
    daily_cost_limit_usd = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("20.0000"))

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_provider_type_display()})"

    def set_api_key(self, raw_key: str):
        if not raw_key:
            self.encrypted_api_key = ""
            return
        box = get_secret_box()
        ciphertext, ver = box.encrypt(raw_key.strip())
        self.encrypted_api_key = ciphertext
        self.key_version = ver

    def get_api_key(self) -> str:
        if not self.encrypted_api_key:
            return ""
        box = get_secret_box()
        return box.decrypt(self.encrypted_api_key, self.key_version)

    def get_masked_key(self) -> str:
        raw = self.get_api_key()
        return mask_secret(raw)

    def get_model_context_limit(self, model_name: str) -> int:
        profile = self.model_profiles.get(model_name, {})
        return profile.get("context_limit", 128000)


class BudgetReservation(UUIDModel):
    """Atomic token and cost allowance reserved before starting an AI operation."""

    class Status(models.TextChoices):
        RESERVED = "reserved", "Reserved"
        RECONCILED = "reconciled", "Reconciled"
        RELEASED = "released", "Released"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="budget_reservations",
    )
    project_id = models.UUIDField(db_index=True)
    job_id = models.UUIDField(db_index=True)
    reserved_tokens = models.PositiveIntegerField()
    reserved_cost_usd = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0.0000"))
    confirmed_tokens = models.PositiveIntegerField(default=0)
    confirmed_cost_usd = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0.0000"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RESERVED)

    class Meta:
        indexes = [
            models.Index(fields=["project_id", "status"]),
            models.Index(fields=["job_id"]),
        ]

    def reconcile(self, tokens_used: int, cost_usd: Decimal):
        self.confirmed_tokens = tokens_used
        self.confirmed_cost_usd = cost_usd
        self.status = self.Status.RECONCILED
        self.save(update_fields=["confirmed_tokens", "confirmed_cost_usd", "status", "updated_at"])

    def release(self):
        self.status = self.Status.RELEASED
        self.save(update_fields=["status", "updated_at"])
