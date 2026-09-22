import pytest
from taletomo.context.budget import BudgetCalculator


def test_budget_calculation_valid():
    budget = BudgetCalculator.calculate_budget(
        model_context_limit=128000,
        requested_output_tokens=4000,
        safety_margin=1000,
        provider_overhead=500,
    )
    # usable_input = 128000 - 4000 - 1000 - 500 = 122500
    assert budget.usable_input == 122500
    assert budget.category_budgets["constraints"] == int(122500 * 0.15)
    assert budget.category_budgets["contract"] == int(122500 * 0.15)
    assert budget.category_budgets["state"] == int(122500 * 0.25)
    assert budget.category_budgets["retrieval"] == int(122500 * 0.25)
    assert budget.category_budgets["recent_context"] == int(122500 * 0.20)


def test_budget_calculation_exceeds_limit():
    with pytest.raises(ValueError, match="exceeds model context limit"):
        BudgetCalculator.calculate_budget(
            model_context_limit=4000,
            requested_output_tokens=3500,
            safety_margin=1000,
            provider_overhead=500,
        )


def test_estimate_tokens():
    text = "The alchemist placed the azure tincture onto the silver pedestal."
    tokens = BudgetCalculator.estimate_tokens(text)
    assert tokens > 0
    assert tokens < 50
