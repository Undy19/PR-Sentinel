"""Unit tests for src.notifications.composer (Telegram MarkdownV2 formatting)."""

from __future__ import annotations

from src.analyzer.risk import RiskAssessment
from src.graph.expertise import Reviewer
from src.notifications.composer import NotificationComposer


def _two_reviewers() -> list[Reviewer]:
    return [
        Reviewer(login="alice", name="Alice", files_touched=3, expertise_score=0.8),
        Reviewer(login="bob", name="Bob", files_touched=1, expertise_score=0.5),
    ]


def test_compose_low_risk() -> None:
    composer = NotificationComposer()
    risk = RiskAssessment(level="LOW", reasons=["Minor change", "Well tested"], confidence=0.9)

    out = composer.compose("Add feature", "https://example.com/pr/1", risk, _two_reviewers())

    expected = (
        "🟢 *LOW* — Add feature\n"
        "→ https://example\\.com/pr/1\n"
        "• Minor change\n"
        "• Well tested\n"
        "👥 Reviewers: @alice, @bob"
    )
    assert out == expected


def test_compose_critical_no_reviewers() -> None:
    composer = NotificationComposer()
    risk = RiskAssessment(level="CRITICAL", reasons=["Security issue"], confidence=0.95)

    out = composer.compose("Fix bug", "https://example.com/pr/2", risk, [])

    assert "🔴 *CRITICAL* — Fix bug" in out
    assert "👥 Reviewers: none" in out
    assert "none" in out


def test_compose_special_chars() -> None:
    composer = NotificationComposer()
    risk = RiskAssessment(level="MED", reasons=["Needs review"], confidence=0.7)

    out = composer.compose("test [feature] (v1.0)", "https://example.com/pr/3", risk, [])

    # MarkdownV2 specials in the title must be backslash-escaped.
    assert "test \\[feature\\] \\(v1\\.0\\)" in out
