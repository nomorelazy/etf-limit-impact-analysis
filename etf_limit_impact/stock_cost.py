"""Read-only cost contributions and display filtering for per-stock reports."""

from decimal import Decimal

ZERO = Decimal(0)


def decimal(value):
    if value is None or value == "":
        return None
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite stock cost amount")
    return result


def cost_metrics(pnl, fees, shares, basket_value):
    """Positive cost impact is adverse, including reduced redemption proceeds.

    Fees are already in pnl: add them back only to classify fee-only rows.
    Never add these explanatory amounts to account cash or total trade PnL.
    """
    pnl, fees, shares, basket = map(decimal, (pnl, fees, shares, basket_value))
    if fees is not None and fees < ZERO:
        raise ValueError("Stock fees must be nonnegative")
    gross = pnl + fees if pnl is not None and fees is not None else None
    impact = -pnl if pnl is not None else None
    return dict(stock_fees=fees, gross_price_pnl=gross,
                # Compare exact amounts before display rounding. Unknown is not zero.
                fee_only=gross == ZERO and fees is not None and fees > ZERO,
                etf_cost_impact=impact,
                etf_cost_impact_per_share=(impact / shares if impact is not None and shares is not None and shares > ZERO else None),
                etf_cost_impact_ratio=(impact / basket if impact is not None and basket is not None and basket > ZERO else None))
