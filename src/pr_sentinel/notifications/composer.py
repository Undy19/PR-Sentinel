"""Notification composer: formats the final Telegram message (MarkdownV2)."""

from __future__ import annotations

from typing import ClassVar

from pr_sentinel.analyzer.risk import RiskAssessment
from pr_sentinel.graph.expertise import Reviewer

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

    Output layout (MarkdownV2); ``language`` selects ``"ru"`` (default)
    or ``"en"`` template labels::

        🔀 PR: {title}
        📊 Риск: {emoji} {level}
        💡 {reason 1} • {reason 2}
        👥 Ревьюеры: @login1, @login2
        🔗 {url}

    Only the template labels are localized; LLM-generated reasons are
    passed through untouched.
    """

    RISK_EMOJI: ClassVar[dict[str, str]] = {
        "LOW": "🟢",
        "MED": "🟡",
        "HIGH": "🟠",
        "CRITICAL": "🔴",
    }

    # Localized risk-level labels; ``MEDIUM`` is an alias for ``MED``
    # (the level value used by :mod:`pr_sentinel.analyzer.risk`).
    _LEVEL_LABELS: ClassVar[dict[str, dict[str, str]]] = {
        "ru": {
            "LOW": "Низкий",
            "MED": "Средний",
            "MEDIUM": "Средний",
            "HIGH": "Высокий",
            "CRITICAL": "Критический",
        },
        "en": {
            "LOW": "Low",
            "MED": "Medium",
            "MEDIUM": "Medium",
            "HIGH": "High",
            "CRITICAL": "Critical",
        },
    }

    _RISK_LINE: ClassVar[dict[str, str]] = {
        "ru": "📊 Риск: {emoji} {level}",
        "en": "📊 Risk: {emoji} {level}",
    }

    _REVIEWERS_LINE: ClassVar[dict[str, str]] = {
        "ru": "👥 Ревьюеры: {reviewers}",
        "en": "👥 Reviewers: {reviewers}",
    }

    _NO_REVIEWERS: ClassVar[dict[str, str]] = {"ru": "нет", "en": "none"}

    def compose(
        self,
        pr_title: str,
        pr_url: str,
        risk: RiskAssessment,
        reviewers: list[Reviewer],
        language: str = "ru",
    ) -> str:
        """Build the formatted message text for a reviewed PR.

        Args:
            pr_title: PR title.
            pr_url: Canonical PR URL.
            risk: LLM risk assessment for the PR.
            reviewers: Recommended reviewers (may be empty).
            language: Template language, ``"ru"`` (default) or ``"en"``;
                unknown values fall back to ``"en"``.

        Returns:
            The complete MarkdownV2 message string.
        """
        lang = language.strip().lower()
        if lang not in self._LEVEL_LABELS:
            lang = "en"
        emoji = self.RISK_EMOJI.get(risk.level, "🟡")
        level_label = self._LEVEL_LABELS[lang].get(risk.level, risk.level)
        lines = [
            f"🔀 PR: {_escape_md_v2(pr_title)}",
            self._RISK_LINE[lang].format(emoji=emoji, level=level_label),
        ]
        if risk.reasons:
            lines.append("💡 " + " • ".join(_escape_md_v2(r) for r in risk.reasons[:2]))
        if reviewers:
            reviewer_part = ", ".join(f"@{_escape_md_v2(r.login)}" for r in reviewers)
        else:
            reviewer_part = self._NO_REVIEWERS[lang]
        lines.append(self._REVIEWERS_LINE[lang].format(reviewers=reviewer_part))
        lines.append(f"🔗 {_escape_md_v2(pr_url)}")
        return "\n".join(lines)
