"""Einstellungen: Profile (PRESET), ARTICLES, Datenschutz-Voreinstellung."""
import pytest

from app.config import PRESETS, Settings


def test_defaults(settings):
    assert settings.prefilter == "none" and not settings.embeddings and settings.preset == ""


@pytest.mark.parametrize("name", list(PRESETS))
def test_presets_apply(env, name):
    env.setenv("PRESET", name)
    s = Settings.from_env()
    assert s.preset == name
    assert s.prefilter == PRESETS[name]["PREFILTER"]


def test_explicit_setting_beats_preset(env):
    env.setenv("PRESET", "laya")
    env.setenv("LAYA_MODEL", "auto")
    s = Settings.from_env()
    assert s.prefilter == "laya" and s.laya_model == "auto"


def test_preset_does_not_leak(env):
    env.setenv("PRESET", "api-only")
    Settings.from_env()
    env.delenv("PRESET")
    assert Settings.from_env().max_entries == 400


def test_unknown_preset(env):
    env.setenv("PRESET", "turbo")
    with pytest.raises(SystemExit, match="turbo"):
        Settings.from_env()


@pytest.mark.parametrize("value,top,min_items", [("8-10", 10, 7), ("10", 10, 9), ("3-5", 5, 3)])
def test_articles(env, value, top, min_items):
    env.setenv("ARTICLES", value)
    s = Settings.from_env()
    assert (s.top_n, s.min_items) == (top, min_items)


@pytest.mark.parametrize("value", ["10-8", "zehn", "0-3"])
def test_articles_invalid(env, value):
    env.setenv("ARTICLES", value)
    with pytest.raises(SystemExit):
        Settings.from_env()


def test_privacy_strict_changes_defaults_only(env):
    env.setenv("PRIVACY", "strict")
    s = Settings.from_env()
    assert s.examples_in_prompt == 0 and s.summary_profile == "lean" and not s.use_starred
    env.setenv("EXAMPLES_IN_PROMPT", "20")
    assert Settings.from_env().examples_in_prompt == 20
