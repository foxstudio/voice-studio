from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.voice_studio import AppSettings  # noqa: E402
from app.services import localization_ai_policy  # noqa: E402


def test_auto_policy_starts_cheap_and_escalates_only_quality_sensitive_steps(
    monkeypatch,
):
    monkeypatch.setattr(
        localization_ai_policy.llm_runtime,
        "resolve_profile",
        lambda profile_id=None: SimpleNamespace(
            profile_id=profile_id or "default-profile",
            model_id="gpt-mini",
            reasoning_effort=None,
        ),
    )
    settings = AppSettings(
        video_localization_ai_strategy="auto",
        video_localization_prompt_strategy="adaptive",
    )

    policy = localization_ai_policy.resolve_localization_ai_policy(
        settings
    )

    assert policy.route("document_understanding").reasoning_effort == "low"
    assert policy.route("document_understanding").may_escalate is False
    assert policy.route("spoken_script_creation").may_escalate is True
    assert (
        policy.route("spoken_script_creation").output_format
        == "markdown"
    )
    assert policy.route("spoken_script_finalization").may_escalate is True
    assert policy.route("fidelity_review").output_format == "json"
    assert policy.route("fidelity_review").reasoning_effort == "high"
    assert policy.route("naturalness_review").reasoning_effort == "low"


def test_quality_policy_honours_phase_profiles_and_markdown_creation(
    monkeypatch,
):
    monkeypatch.setattr(
        localization_ai_policy.llm_runtime,
        "resolve_profile",
        lambda profile_id=None: SimpleNamespace(
            profile_id=profile_id or "default-profile",
            model_id=f"model-for-{profile_id or 'default'}",
            reasoning_effort="high",
        ),
    )
    settings = AppSettings(
        video_localization_ai_strategy="quality",
        video_localization_creation_profile_id="creative-profile",
        video_localization_review_profile_id="review-profile",
    )

    policy = localization_ai_policy.resolve_localization_ai_policy(
        settings
    )

    creation = policy.route("spoken_script_creation")
    assert creation.profile_id == "creative-profile"
    assert creation.reasoning_effort == "high"
    assert creation.output_format == "markdown"
    assert creation.may_escalate is False
    assert (
        policy.route("naturalness_review").profile_id
        == "review-profile"
    )
    assert (
        policy.route("spoken_script_finalization").profile_id
        == "creative-profile"
    )
    assert (
        policy.route("spoken_script_finalization").output_format
        == "json"
    )


def test_auto_policy_honours_profile_reasoning_without_extra_escalation(
    monkeypatch,
):
    monkeypatch.setattr(
        localization_ai_policy.llm_runtime,
        "resolve_profile",
        lambda profile_id=None: SimpleNamespace(
            profile_id=profile_id or "default-profile",
            model_id="gpt-quality",
            reasoning_effort="high",
        ),
    )
    policy = localization_ai_policy.resolve_localization_ai_policy(
        AppSettings(video_localization_ai_strategy="auto")
    )

    assert policy.route("spoken_script_creation").reasoning_effort == "high"
    assert policy.route("spoken_script_creation").may_escalate is False


def test_new_settings_apply_to_next_policy_without_mutating_running_policy(
    monkeypatch,
):
    monkeypatch.setattr(
        localization_ai_policy.llm_runtime,
        "resolve_profile",
        lambda profile_id=None: SimpleNamespace(
            profile_id=profile_id or "default-profile",
            model_id=f"model-for-{profile_id or 'default'}",
            reasoning_effort="low",
        ),
    )
    first = localization_ai_policy.resolve_localization_ai_policy(
        AppSettings(
            video_localization_understanding_profile_id="understanding-a",
            video_localization_creation_profile_id="creation-a",
        )
    )
    second = localization_ai_policy.resolve_localization_ai_policy(
        AppSettings(
            video_localization_understanding_profile_id="understanding-b",
            video_localization_creation_profile_id="creation-b",
        )
    )

    assert first.route("document_understanding").profile_id == (
        "understanding-a"
    )
    assert first.route("spoken_script_creation").profile_id == "creation-a"
    assert second.route("document_understanding").profile_id == (
        "understanding-b"
    )
    assert second.route("spoken_script_creation").profile_id == "creation-b"
    assert first.route("spoken_script_creation").model_id == (
        "model-for-creation-a"
    )


def test_development_policy_resolves_only_required_phases(
    monkeypatch,
):
    resolved_profile_ids: list[str | None] = []

    def resolve_profile(profile_id=None):
        resolved_profile_ids.append(profile_id)
        if profile_id == "deleted-creation-profile":
            raise AssertionError(
                "未执行的创作阶段不应在全文理解单步中解析"
            )
        return SimpleNamespace(
            profile_id=profile_id or "default-profile",
            model_id="understanding-model",
            reasoning_effort="high",
        )

    monkeypatch.setattr(
        localization_ai_policy.llm_runtime,
        "resolve_profile",
        resolve_profile,
    )
    policy = localization_ai_policy.resolve_localization_ai_policy(
        AppSettings(
            video_localization_creation_profile_id=(
                "deleted-creation-profile"
            ),
        ),
        required_phases={"document_understanding"},
    )

    assert resolved_profile_ids == [None]
    assert [route.phase for route in policy.routes] == [
        "document_understanding"
    ]
