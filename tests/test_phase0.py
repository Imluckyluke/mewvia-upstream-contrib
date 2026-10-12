"""Phase-0 acceptance tests: config, feature registry, dispatcher wiring, texts.

No Telegram connection and no database are required.
"""

from __future__ import annotations

import pytest
from aiogram import Dispatcher
from app.config import Settings


def test_admin_ids_parsed_from_csv(settings: Settings) -> None:
    assert settings.admin_ids == [1, 2]
    assert settings.is_admin(1)
    assert not settings.is_admin(999)
    assert not settings.is_admin(None)


def test_bot_username_strips_at() -> None:
    s = Settings(bot_token="x", bot_username="@MewviaBot")
    assert s.bot_username == "MewviaBot"


def test_database_url_uses_asyncpg(settings: Settings) -> None:
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.postgres_db in settings.database_url


def test_public_api_file_limits(settings: Settings) -> None:
    """Without a local server, the 20MB/50MB caps must be reported."""
    assert not settings.uses_local_api
    assert settings.download_limit == 20 * 1024 * 1024
    assert settings.upload_limit == 50 * 1024 * 1024


def test_local_api_lifts_file_limits() -> None:
    s = Settings(
        bot_token="x",
        telegram_api_url="http://telegram-bot-api:8081",
    )
    assert s.uses_local_api
    assert s.download_limit > 100 * 1024 * 1024
    assert s.upload_limit == s.download_limit


def test_features_load_and_are_unique() -> None:
    from app.features import load_features

    features = load_features()
    assert features, "no features registered"
    names = [f.name for f in features]
    assert len(names) == len(set(names))
    assert "basic" in names


def test_load_features_is_idempotent() -> None:
    """Called on every dispatcher build; must not double-register."""
    from app.features import load_features

    first = load_features()
    second = load_features()
    assert [f.name for f in first] == [f.name for f in second]


def test_dispatcher_includes_every_feature_router(app_dispatcher: Dispatcher) -> None:
    """Registering a feature must be enough for its handlers to run."""
    from app.features import load_features

    attached = {r.name for r in app_dispatcher.sub_routers}
    for feature in load_features():
        assert feature.router.name in attached


def test_registry_rejects_duplicate_names() -> None:
    from aiogram import Router
    from app.features.registry import Feature, register

    dup = Feature(name="basic", router=Router(name="dup"))
    with pytest.raises(ValueError, match="already registered"):
        register(dup)


def test_texts_have_no_missing_keys() -> None:
    from app.ui.texts import t

    assert "Mewvia" in t("start.greeting", name="X")
    assert t("common.back")
    assert t("⟨nope⟩") == "⟨⟨nope⟩⟩"  # missing keys are visible, not silent


def test_error_text_falls_back_for_unknown_code() -> None:
    from app.core.errors import ConversionTimeoutError
    from app.ui.texts import error_text

    assert error_text(ConversionTimeoutError.code) != error_text("no_such_code")
    assert error_text("no_such_code") == error_text("unknown")


def test_error_text_formats_placeholders() -> None:
    from app.ui.texts import error_text

    assert "20.0 MB" in error_text("file_too_large", limit="20.0 MB")


def test_human_bytes_and_duration() -> None:
    from app.core.units import fa_digits, human_bytes, human_duration

    assert human_bytes(512) == "512 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(20 * 1024 * 1024) == "20.0 MB"
    assert human_duration(95.4) == "1:35"
    assert human_duration(3725) == "1:02:05"
    assert fa_digits("1/7") == "۱/۷"


def test_preflight_flags_missing_token_and_admins() -> None:
    """A forgotten token must produce advice, not a traceback."""
    from app.__main__ import _preflight
    from app.config import Settings

    problems = _preflight(Settings(bot_token="not-a-token", admin_ids=[]))
    assert any("BOT_TOKEN" in p for p in problems)
    assert any("ADMIN_IDS" in p for p in problems)


def test_preflight_passes_on_wellformed_config() -> None:
    from app.__main__ import _preflight
    from app.config import Settings

    good = Settings(
        bot_token="123456789:AAtestFAKEtokenFAKEtokenFAKEtoken12",
        admin_ids=[1],
        postgres_password="x",
    )
    assert _preflight(good) == []
