"""Shared Advisor prompt construction."""


def build_advisor_prompt(holdings: dict, metrics: dict, risk: dict) -> str:
    lines = [
        "You are a portfolio analyst. Given the figures below, write a "
        "concise 3-4 sentence briefing covering return, risk, "
        "diversification, and concentration. Be specific and neutral.\n"
    ]
    lines.append(
        "Holdings (weights): " + ", ".join(f"{t}={w}" for t, w in holdings.items())
    )
    lines.append("\nPer-ticker metrics:")
    for t, m in metrics.items():
        if "error" in m:
            lines.append(f"  {t}: {m['error']}")
            continue
        lines.append(
            f"  {t}: ann_return={m['annualized_return']:.1%}, "
            f"ann_vol={m['annualized_volatility']:.1%}, "
            f"sharpe={m['sharpe']}, max_drawdown={m['max_drawdown']:.1%}"
        )
    lines.append(
        "\nPortfolio: "
        f"ann_return={risk.get('portfolio_annualized_return', 0):.1%}, "
        f"ann_vol={risk.get('portfolio_annualized_volatility', 0):.1%}, "
        f"sharpe={risk.get('portfolio_sharpe')}, "
        f"HHI={risk.get('concentration_hhi')}, "
        f"diversification_ratio={risk.get('diversification_ratio')}"
    )
    return "\n".join(lines)
