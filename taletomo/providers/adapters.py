import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field
from taletomo.core.crypto import redact_secrets
from taletomo.providers.models import ProviderConfig, ProviderType
from taletomo.providers.security import validate_provider_endpoint

logger = logging.getLogger(__name__)


def safe_decimal(val, default="0.0") -> Decimal:
    if val is None or val == "":
        return Decimal(default)
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal(default)


class ProviderResponse(BaseModel):
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = Field(default_factory=lambda: Decimal("0.0000"))
    model: str = ""
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseProviderAdapter:
    def __init__(self, config: ProviderConfig):
        self.config = config

    def validate_credentials(self) -> Dict[str, Any]:
        raise NotImplementedError

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        max_tokens: int = 4000,
        temperature: float = 0.7,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> ProviderResponse:
        raise NotImplementedError


class FakeProviderAdapter(BaseProviderAdapter):
    """Deterministic fake provider for automated testing and local offline development."""

    def __init__(self, config: ProviderConfig, custom_script: Optional[Dict[str, str]] = None):
        super().__init__(config)
        self.custom_script = custom_script or {}
        self.simulate_failure: Optional[str] = None
        self.simulate_contradiction: bool = False

    def validate_credentials(self) -> Dict[str, Any]:
        return {
            "valid": True,
            "provider": "fake",
            "models": ["mock-planning-v1", "mock-drafting-v1", "mock-critique-v1"],
            "context_limits": {
                "mock-planning-v1": 250000,
                "mock-drafting-v1": 250000,
                "mock-critique-v1": 128000,
            },
        }

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        max_tokens: int = 4000,
        temperature: float = 0.7,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> ProviderResponse:
        selected_model = model or self.config.default_drafting_model

        if self.simulate_failure == "timeout":
            raise TimeoutError("Simulated provider connection timeout")
        elif self.simulate_failure == "rate_limit":
            raise RuntimeError("429 Too Many Requests: Rate limit exceeded")
        elif self.simulate_failure == "auth":
            raise PermissionError("401 Unauthorized: Invalid API Key")

        # Check custom script overrides
        for key, scripted_content in self.custom_script.items():
            if key in prompt:
                return ProviderResponse(
                    content=scripted_content,
                    prompt_tokens=len(prompt) // 4,
                    completion_tokens=len(scripted_content) // 4,
                    total_tokens=(len(prompt) + len(scripted_content)) // 4,
                    cost_usd=Decimal("0.0005"),
                    model=selected_model,
                )

        # Context-aware mock responses
        prompt_lower = prompt.lower()
        if "bible" in prompt_lower or "premise" in prompt_lower:
            content = json.dumps(
                {
                    "pitch": "A fallen royal alchemist must rebuild his shattered reputation while navigating political court treachery.",
                    "reader_promise": "Rich magic system, deep faction politics, and satisfying strategic payoffs.",
                    "world_setting": "The Grand Dominion of Altera during the Brass Reformation.",
                    "magic_rules": [
                        "Alchemy requires equal material sacrifice.",
                        "Direct manipulation of soul-essence is strictly lethal.",
                    ],
                }
            )
        elif "scene plan" in prompt_lower or "scenes" in prompt_lower:
            content = json.dumps(
                [
                    {
                        "scene_order": 1,
                        "objective": "Inspect the poisoned vial in the secret laboratory.",
                        "conflict": "The city guard conducts a surprise raid.",
                        "characters": ["Alaric", "Captain Vance"],
                        "estimated_words": 1200,
                    },
                    {
                        "scene_order": 2,
                        "objective": "Escape through the old aqueduct without being recognized.",
                        "conflict": "Alaric's injured left hand makes climbing treacherous.",
                        "characters": ["Alaric"],
                        "estimated_words": 1400,
                    },
                ]
            )
        elif "critique" in prompt_lower or "continuity" in prompt_lower:
            findings = []
            if self.simulate_contradiction:
                findings.append(
                    {
                        "category": "injury",
                        "severity": "blocker",
                        "claim": "Protagonist climbed with both hands fully intact.",
                        "conflicting_evidence": ["Fact #42: Alaric's left hand remains shattered and bandaged."],
                        "source_references": ["Chapter 1, Scene 2"],
                        "suggested_action": "Revise prose to depict one-handed climbing or assistance.",
                    }
                )
            content = json.dumps(findings)
        elif "extract" in prompt_lower:
            content = json.dumps(
                {
                    "events": [
                        {"summary": "Alaric escaped the city guard raid through the aqueduct.", "event_type": "plot_progress"}
                    ],
                    "claims": [
                        {"subject": "Alaric", "predicate": "current_location", "value": "Old Aqueduct", "scope": "world_truth"},
                        {"subject": "Captain Vance", "predicate": "investigating", "value": "Alaric's Laboratory", "scope": "world_truth"}
                    ],
                    "threads_updated": [
                        {"thread_title": "City Guard Investigation", "operation": "advance", "note": "Vance discovered the lab"}
                    ],
                }
            )
        else:
            # Default drafted chapter prose
            content = (
                "The rain pounded against the leaded glass of Alaric's study, streaking the dark panorama of the Grand Dominion. "
                "Beneath the flickering gaslamp, the vial of azure tincture glowed with an unsettling luminescence.\n\n"
                "Footsteps echoed on the cobblestones outside—rhythmic, heavy, and far too hurried for a midnight patrol. "
                "Alaric reached for his leather satchel with his good right hand, his shattered left wrist throbbing in the damp cold. "
                "'They shouldn't have arrived before dawn,' he muttered, blowing out the flame.\n\n"
                "When the front latch splintered, he was already slipping through the iron grate into the damp darkness of the aqueduct."
            )

        words = len(content.split())
        approx_tokens = int(words * 1.3)
        return ProviderResponse(
            content=content,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=approx_tokens,
            total_tokens=(len(prompt) // 4) + approx_tokens,
            cost_usd=Decimal("0.0012"),
            model=selected_model,
        )


class OpenAICompatibleAdapter(BaseProviderAdapter):
    """Production adapter for OpenAI or any compatible endpoint (vLLM, Ollama, DeepSeek, OpenRouter, etc.)."""

    def __init__(self, config: ProviderConfig, allowlist: Optional[List[str]] = None):
        super().__init__(config)
        self.allowlist = allowlist or []
        # Validate endpoint safety before making any calls
        validate_provider_endpoint(self.config.endpoint_url, self.allowlist)

    def _get_headers(self) -> Dict[str, str]:
        key = self.config.get_api_key()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def validate_credentials(self) -> Dict[str, Any]:
        validate_provider_endpoint(self.config.endpoint_url, self.allowlist)
        url = f"{self.config.endpoint_url.rstrip('/')}/models"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    model_ids = [m["id"] for m in data.get("data", [])] if "data" in data else []
                    return {"valid": True, "models": model_ids[:20]}
                return {"valid": False, "status_code": resp.status_code, "error": redact_secrets(resp.text)}
        except Exception as e:
            return {"valid": False, "error": redact_secrets(str(e))}

    def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        model: Optional[str] = None,
        max_tokens: int = 4000,
        temperature: float = 0.7,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> ProviderResponse:
        validate_provider_endpoint(self.config.endpoint_url, self.allowlist)
        url = f"{self.config.endpoint_url.rstrip('/')}/chat/completions"
        selected_model = model or self.config.default_drafting_model

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, headers=self._get_headers(), json=payload)
                if response.status_code != 200:
                    safe_err = redact_secrets(response.text)
                    raise RuntimeError(f"Provider error ({response.status_code}): {safe_err}")

                data = response.json()
                choices = data.get("choices", [])
                if not choices:
                    raise RuntimeError("Provider returned empty choices array.")
                choice = choices[0]
                message = choice.get("message", {})
                content = message.get("content") or ""
                usage = data.get("usage", {})
                p_tokens = int(usage.get("prompt_tokens") or 0)
                c_tokens = int(usage.get("completion_tokens") or 0)
                t_tokens = int(usage.get("total_tokens") or (p_tokens + c_tokens))

                # Calculate estimated cost if pricing profile is available
                profile = self.config.model_profiles.get(selected_model, {}) if isinstance(self.config.model_profiles, dict) else {}
                price_in = safe_decimal(profile.get("pricing_input_per_m"), "0.0")
                price_out = safe_decimal(profile.get("pricing_output_per_m"), "0.0")
                raw_cost = (Decimal(p_tokens) * price_in / Decimal(1000000)) + (
                    Decimal(c_tokens) * price_out / Decimal(1000000)
                )
                cost = raw_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

                return ProviderResponse(
                    content=content,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=t_tokens,
                    cost_usd=cost,
                    model=selected_model,
                    raw_metadata={"id": data.get("id")},
                )
        except httpx.TimeoutException as e:
            logger.error("Provider request timed out")
            raise TimeoutError("Provider request timed out after submission") from e
        except Exception as e:
            safe_msg = redact_secrets(str(e))
            logger.error(f"Provider call failed: {safe_msg}")
            raise


class ProviderGateway:
    @staticmethod
    def get_adapter(
        config: Optional[ProviderConfig] = None,
        user=None,
        project=None,
    ) -> BaseProviderAdapter:
        target_user = user or (project.owner if project else None)
        if config is not None and target_user and config.user_id != target_user.pk:
            raise PermissionError("Provider configuration does not belong to the requesting user.")

        if config is None:
            if target_user:
                user_config = (
                    ProviderConfig.objects.filter(user=target_user, is_default=True, is_active=True).first()
                    or ProviderConfig.objects.filter(user=target_user, is_active=True).first()
                )
                if user_config:
                    return ProviderGateway.get_adapter(user_config, user=target_user)

            # Safe Fallback: isolated in-memory mock adapter, never another user's provider
            fake_config = ProviderConfig(
                name="Fallback Mock",
                provider_type=ProviderType.FAKE,
                default_drafting_model="mock-drafting-v1",
            )
            return FakeProviderAdapter(fake_config)

        if config.provider_type == ProviderType.FAKE:
            return FakeProviderAdapter(config)
        elif config.provider_type in (ProviderType.OPENAI_COMPATIBLE, ProviderType.NATIVE):
            return OpenAICompatibleAdapter(config)
        raise ValueError(f"Unsupported provider type: {config.provider_type}")
