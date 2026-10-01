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
    default_extraction_model = models.CharField(max_length=100, default="mock-extraction-v1")
    default_copilot_model = models.CharField(max_length=100, default="mock-copilot-v1")
    default_embedding_model = models.CharField(max_length=100, default="text-embedding-3-small")

    # Task-to-model mapping matrix, e.g. {"drafting": "gpt-4o", "extraction": "gpt-4o-mini", "copilot": "gpt-4o"}
    task_routing = models.JSONField(
        default=dict,
        blank=True,
        help_text="Task-to-model routing table",
    )

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
    monthly_cost_limit_usd = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("50.0000"))

    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "name"]

    def get_model_for_task(self, task: str) -> str:
        """Resolves the configured model name for a specific pipeline task."""
        if isinstance(self.task_routing, dict) and self.task_routing.get(task):
            return str(self.task_routing[task])
        fallbacks = {
            "drafting": self.default_drafting_model,
            "planning": self.default_planning_model,
            "critique": self.default_critique_model,
            "extraction": self.default_extraction_model or self.default_critique_model,
            "copilot": self.default_copilot_model or self.default_drafting_model,
            "embedding": self.default_embedding_model,
        }
        return fallbacks.get(task, self.default_drafting_model)

    def check_budget_limits(self, estimated_cost_usd: Decimal = Decimal("0.0"), user=None) -> tuple[bool, str]:
        """Validates that a new operation's estimated cost won't exceed daily or monthly spending limits."""
        target_user = user or self.user
        if not target_user:
            return True, ""

        from django.utils import timezone
        now = timezone.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Sum reconciled costs for today and this month
        day_qs = BudgetReservation.objects.filter(
            user=target_user,
            status=BudgetReservation.Status.RECONCILED,
            updated_at__gte=start_of_day,
        )
        month_qs = BudgetReservation.objects.filter(
            user=target_user,
            status=BudgetReservation.Status.RECONCILED,
            updated_at__gte=start_of_month,
        )

        spent_today = sum((r.confirmed_cost_usd for r in day_qs), Decimal("0.0000"))
        spent_month = sum((r.confirmed_cost_usd for r in month_qs), Decimal("0.0000"))

        if self.daily_cost_limit_usd > Decimal("0") and (spent_today + estimated_cost_usd) > self.daily_cost_limit_usd:
            return (
                False,
                f"Daily cost limit of ${self.daily_cost_limit_usd:.2f} reached "
                f"(spent today: ${spent_today:.4f}, estimated: ${estimated_cost_usd:.4f}).",
            )

        if self.monthly_cost_limit_usd > Decimal("0") and (spent_month + estimated_cost_usd) > self.monthly_cost_limit_usd:
            return (
                False,
                f"Monthly cost limit of ${self.monthly_cost_limit_usd:.2f} reached "
                f"(spent this month: ${spent_month:.4f}, estimated: ${estimated_cost_usd:.4f}).",
            )

        return True, ""

    def save(self, *args, **kwargs):
        if self.is_default and self.user_id:
            ProviderConfig.objects.filter(user_id=self.user_id, is_default=True).exclude(id=self.id).update(is_default=False)
        super().save(*args, **kwargs)

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
        try:
            return box.decrypt(self.encrypted_api_key, self.key_version)
        except Exception:
            return ""

    def get_masked_key(self) -> str:
        raw = self.get_api_key()
        return mask_secret(raw)

    def get_model_context_limit(self, model_name: str) -> int:
        profile = self.model_profiles.get(model_name, {}) if isinstance(self.model_profiles, dict) else {}
        val = profile.get("context_limit")
        try:
            return int(val) if val else 128000
        except (TypeError, ValueError):
            return 128000


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
        from decimal import ROUND_HALF_UP
        self.confirmed_tokens = max(0, int(tokens_used or 0))
        if not isinstance(cost_usd, Decimal):
            cost_usd = Decimal(str(cost_usd or "0.0000"))
        self.confirmed_cost_usd = cost_usd.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        self.status = self.Status.RECONCILED
        self.save(update_fields=["confirmed_tokens", "confirmed_cost_usd", "status", "updated_at"])

    def release(self):
        self.status = self.Status.RELEASED
        self.save(update_fields=["status", "updated_at"])
