"""Notification composer: formats the final Telegram message (MarkdownV2)."""

from __future__ import annotations

from typing import ClassVar

from src.analyzer.risk import RiskAssessment
from src.graph.expertise import Reviewer

# Characters Telegram MarkdownV2 requires to be escaped in plain text.
_MD_V2_SPECIALS = frozenset(r"_*[]()~`>#+-=|{}.!")


def _escape_md_v2(text: str) -> str:
    """Escape MarkdownV2 special characters in a plain-text fragment."""
    out: list[str] = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch in _MD_V2_SPECIALS:
            out.append(f"\\{ch}")
        else:
            out.append(ch)
    return "".join(out)


class NotificationComposer:
    """Composes the final Telegram message for a reviewed PR.

    Output layout (MarkdownV2, single ``*`` for bold)::

        {emoji} *{level}* — {title}
        → {url}
        • reason 1
        • reason 2
        👥 Reviewers: @login1, @login2
    """

    RISK_EMOJI: ClassVar[dict[str, str]] = {"LOW": "🟢", "MED": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}

    def compose(
        self,
        pr_title: str,
        pr_url: str,
        risk: RiskAssessment,
        reviewers: list[Reviewer],
    ) -> str:
        """Build the formatted message text for a reviewed PR.

        Args:
            pr_title: PR title.
            pr_url: Canonical PR URL.
            risk: LLM risk assessment for the PR.
            reviewers: Recommended reviewers (may be empty).

        Returns:
            The complete MarkdownV2 message string.
        """
        emoji = self.RISK_EMOJI.get(risk.level, "🟡")
        lines = [
            f"{emoji} *{risk.level}* — {_escape_md_v2(pr_title)}",
            f"→ {_escape_md_v2(pr_url)}",
        ]
        lines.extend(f"• {_escape_md_v2(reason)}" for reason in risk.reasons[:2])
        if reviewers:
            reviewer_part = ", ".join(f"@{_escape_md_v2(r.login)}" for r in reviewers)
        else:
            reviewer_part = "none"
        lines.append(f"👥 Reviewers: {reviewer_part}")
        return "\n".join(lines)
