from dataclasses import dataclass
from typing import Dict


@dataclass
class TokenBudget:
    model_context_limit: int
    requested_output_tokens: int
    safety_margin: int
    provider_overhead_reserve: int
    usable_input: int
    category_budgets: Dict[str, int]


class BudgetCalculator:
    SAFETY_MARGIN_DEFAULT = 1000
    PROVIDER_OVERHEAD_DEFAULT = 500

    @classmethod
    def calculate_budget(
        cls,
        model_context_limit: int = 128000,
        requested_output_tokens: int = 4000,
        safety_margin: int = None,
        provider_overhead: int = None,
    ) -> TokenBudget:
        margin = safety_margin if safety_margin is not None else cls.SAFETY_MARGIN_DEFAULT
        overhead = provider_overhead if provider_overhead is not None else cls.PROVIDER_OVERHEAD_DEFAULT

        usable_input = model_context_limit - requested_output_tokens - margin - overhead
        if usable_input <= 0:
            raise ValueError(
                f"Requested output ({requested_output_tokens}) + margins ({margin + overhead}) "
                f"exceeds model context limit ({model_context_limit})"
            )

        # Category quota percentages
        # 1. Constraints & Bible: 15%
        # 2. Local Chapter Contract & Scene Plan: 15%
        # 3. Canonical Story State & Entities: 25%
        # 4. Retrieved Older Evidence: 25%
        # 5. Recent Summaries & Prose: 20%
        category_budgets = {
            "constraints": int(usable_input * 0.15),
            "contract": int(usable_input * 0.15),
            "state": int(usable_input * 0.25),
            "retrieval": int(usable_input * 0.25),
            "recent_context": int(usable_input * 0.20),
        }

        return TokenBudget(
            model_context_limit=model_context_limit,
            requested_output_tokens=requested_output_tokens,
            safety_margin=margin,
            provider_overhead_reserve=overhead,
            usable_input=usable_input,
            category_budgets=category_budgets,
        )

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        # 1 token ~= 4 characters or 0.75 words; take conservative upper bound
        char_tokens = len(text) // 3 + 1
        word_tokens = int(len(text.split()) * 1.3) + 1
        return max(char_tokens, word_tokens)
