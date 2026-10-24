"""Konfiguration aus Umgebungsvariablen (.env)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v is not None and v.strip() != "" else default


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return (_env(name, "true" if default else "false") or "").lower() in ("1", "true", "yes", "ja", "on")


def _list(name: str) -> list[str]:
    raw = _env(name, "") or ""
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


# OpenAI-kompatible Anbieter: Name -> (Basis-URL, Env-Variable für den Key)
OPENAI_COMPATIBLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "ollama": ("http://host.docker.internal:11434/v1", "OLLAMA_API_KEY"),
    "custom": (None, "CUSTOM_API_KEY"),
}
PROVIDERS = ["anthropic", *OPENAI_COMPATIBLE]


@dataclass
class Settings:
    # Quelle: miniflux | greader
    source: str = "miniflux"
    miniflux_url: str = ""
    miniflux_token: str = ""
    greader_url: str = ""
    greader_user: str = ""
    greader_password: str = ""
    greader_app_id: str = ""
    greader_app_key: str = ""
    greader_labels: list[str] = field(default_factory=list)
    select_from: str = "unread"   # unread | new
    unread_max_age_days: int = 30
    lookback_hours: int = 24
    max_entries: int = 400
    only_unread: bool = False
    category_ids: list[str] = field(default_factory=list)
    exclude_feed_ids: list[str] = field(default_factory=list)
    use_starred: bool = True

    # LLM
    scoring_provider: str = "anthropic"
    scoring_model: str = ""
    summary_provider: str = "anthropic"
    summary_model: str = ""
    scoring_batch_size: int = 40
    # Laya-Vorfilter
    prefilter: str = "none"  # none | laya | embeddings
    prefilter_keep: int = 40
    prefilter_min: float = 2.0
    prefilter_per_topic: int = 2   # Mindestplätze je Thema, bevor nach Punkten sortiert wird
    prefilter_explore: int = 5     # so viele zufällige Artikel unter der Schwelle gehen zur Kontrolle ans LLM
    learn_from_llm: bool = True    # Laya-Gewichte aus den LLM-Bewertungen lernen
    laya_model: str = "auto"
    laya_max_len: int = 1024
    laya_batch_size: int = 8
    # lokale Embeddings: gleiche Geschichten, Ähnlichkeit zu Sternen
    embeddings: bool = False
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dedup_similarity: float = 0.75
    group_topics: bool = True
    similar_min: float = 0.55
    # Rückmeldung / Lernen
    track_clicks: bool = True
    vote_links: bool = True      # 👍/👎 unter jedem Artikel
    read_signal: bool = False    # Lesestatus im Reader als Signal (nur ohne Wegsortieren sinnvoll)
    open_pixel: str = "off"      # off | log: unsichtbares Bild nur protokollieren (Messung)
    learn: bool = True
    feedback_days: int = 3
    star_poll_minutes: int = 15
    explore_slots: int = 1
    explore_min_regular: int = 4
    examples_in_prompt: int = 120
    summary_profile: str = "full"
    privacy: str = "standard"

    # Auswahl / Ausgabe
    top_n: int = 10
    min_score: int = 6
    min_items: int = 5
    max_per_topic: int = 3   # höchstens so viele Artikel je Thema, solange andere Themen mithalten
    floor_score: int = 3
    output_language: str = "Deutsch"
    feed_mode: str = "daily"  # daily | items
    feed_title: str = "Mein Tages-Digest"
    keep_days: int = 30

    # Zeitplan / Server
    digest_time: str = "06:30"
    tz: str = "Europe/Berlin"
    run_on_start: bool = False
    public_url: str = "http://localhost:8080"
    feed_token: str = ""
    http_port: int = 8080

    # Pfade
    data_dir: Path = Path("/data")
    config_dir: Path = Path("/config")

    @property
    def feed_path(self) -> str:
        return f"/{self.feed_token}/feed.xml" if self.feed_token else "/feed.xml"

    @property
    def feed_base(self) -> str:
        """Öffentliche Basis-URL inkl. geheimem Pfad, z. B. https://digest.example.org/<token>"""
        return self.public_url.rstrip("/") + (f"/{self.feed_token}" if self.feed_token else "")

    @property
    def feed_url(self) -> str:
        return self.public_url.rstrip("/") + self.feed_path

    @classmethod
    def from_env(cls) -> "Settings":
        # PRIVACY=strict ändert nur Standardwerte – was in der .env einzeln steht, gilt weiterhin
        privacy = (_env("PRIVACY", "standard") or "standard").lower()
        strict = privacy == "strict"
        s = cls(
            privacy=privacy,
            source=(_env("SOURCE", "miniflux") or "miniflux").lower(),
            greader_url=(_env("GREADER_URL") or "").rstrip("/"),
            greader_user=_env("GREADER_USER", "") or "",
            greader_password=_env("GREADER_PASSWORD", "") or "",
            greader_app_id=_env("GREADER_APP_ID", "") or "",
            greader_app_key=_env("GREADER_APP_KEY", "") or "",
            greader_labels=_list("GREADER_LABELS"),
            miniflux_url=(_env("MINIFLUX_URL") or "").rstrip("/"),
            miniflux_token=_env("MINIFLUX_API_TOKEN", "") or "",
            select_from=(_env("SELECT_FROM", "unread") or "unread").lower(),
            unread_max_age_days=_int("UNREAD_MAX_AGE_DAYS", 30),
            lookback_hours=_int("LOOKBACK_HOURS", 24),
            max_entries=_int("MAX_ENTRIES", 400),
            only_unread=_bool("ONLY_UNREAD", False),
            category_ids=_list("CATEGORY_IDS"),
            exclude_feed_ids=_list("EXCLUDE_FEED_IDS"),
            use_starred=_bool("USE_MINIFLUX_STARRED", not strict),
            scoring_provider=(_env("SCORING_PROVIDER", "anthropic") or "").lower(),
            scoring_model=_env("SCORING_MODEL", "") or "",
            summary_provider=(_env("SUMMARY_PROVIDER", "anthropic") or "").lower(),
            summary_model=_env("SUMMARY_MODEL", "") or "",
            scoring_batch_size=_int("SCORING_BATCH_SIZE", 40),
            prefilter=(_env("PREFILTER", "none") or "none").lower(),
            prefilter_keep=_int("PREFILTER_KEEP", 40),
            prefilter_min=float(_env("PREFILTER_MIN", "2.0") or 2.0),
            prefilter_per_topic=_int("PREFILTER_PER_TOPIC", 2),
            prefilter_explore=_int("PREFILTER_EXPLORE", 5),
            learn_from_llm=_bool("LEARN_FROM_LLM", True),
            laya_model=_env("LAYA_MODEL", "auto") or "auto",
            laya_max_len=_int("LAYA_MAX_LEN", 1024),
            laya_batch_size=_int("LAYA_BATCH_SIZE", 8),
            embeddings=_bool("EMBEDDINGS", False),
            embedding_model=_env("EMBEDDING_MODEL", cls.embedding_model) or cls.embedding_model,
            dedup_similarity=float(_env("DEDUP_SIMILARITY", "0.75") or 0.75),
            group_topics=_bool("GROUP_TOPICS", True),
            similar_min=float(_env("SIMILAR_MIN", "0.55") or 0.55),
            track_clicks=_bool("TRACK_CLICKS", True),
            vote_links=_bool("VOTE_LINKS", True),
            read_signal=_bool("READ_SIGNAL", False),
            open_pixel=(_env("OPEN_PIXEL", "off") or "off").lower(),
            learn=_bool("LEARN", True),
            feedback_days=_int("FEEDBACK_DAYS", 3),
            star_poll_minutes=_int("STAR_POLL_MINUTES", 15),
            explore_slots=_int("EXPLORE_SLOTS", 1),
            explore_min_regular=_int("EXPLORE_MIN_REGULAR", 4),
            examples_in_prompt=_int("EXAMPLES_IN_PROMPT", 0 if strict else 120),
            summary_profile=(_env("SUMMARY_PROFILE", "lean" if strict else "full") or "full").lower(),
            top_n=_int("TOP_N", 10),
            min_score=_int("MIN_SCORE", 6),
            min_items=_int("MIN_ITEMS", 5),
            max_per_topic=_int("MAX_PER_TOPIC", 3),
            floor_score=_int("FLOOR_SCORE", 3),
            output_language=_env("OUTPUT_LANGUAGE", "Deutsch") or "Deutsch",
            feed_mode=(_env("FEED_MODE", "daily") or "daily").lower(),
            feed_title=_env("FEED_TITLE", "Mein Tages-Digest") or "Mein Tages-Digest",
            keep_days=_int("KEEP_DAYS", 30),
            digest_time=_env("DIGEST_TIME", "06:30") or "06:30",
            tz=_env("TZ", "Europe/Berlin") or "Europe/Berlin",
            run_on_start=_bool("RUN_ON_START", False),
            public_url=(_env("PUBLIC_URL", "http://localhost:8080") or "").rstrip("/"),
            feed_token=_env("FEED_TOKEN", "") or "",
            http_port=_int("HTTP_PORT", 8080),
            data_dir=Path(_env("DATA_DIR", "/data") or "/data"),
            config_dir=Path(_env("CONFIG_DIR", "/config") or "/config"),
        )
        s._apply_articles(_env("ARTICLES", "") or "")
        s.validate()
        return s

    def _apply_articles(self, value: str) -> None:
        """ARTICLES=8-10: mindestens 8, höchstens 10 Artikel pro Digest, Zufallsfund eingerechnet.
        ARTICLES=10: immer bis zu 10. Ersetzt TOP_N und MIN_ITEMS."""
        value = value.replace(" ", "")
        if not value:
            return
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", value)
        if not m:
            raise SystemExit(f"ARTICLES={value!r} verstehe ich nicht – Beispiel: ARTICLES=8-10")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 1 <= lo <= hi:
            raise SystemExit(f"ARTICLES={value}: erst die kleinere, dann die größere Zahl, z. B. 8-10")
        self.top_n = hi
        # MIN_ITEMS zählt nur reguläre Artikel; der Zufallsfund kommt erst ab EXPLORE_MIN_REGULAR dazu
        regular = lo - self.explore_slots
        self.min_items = regular if regular >= self.explore_min_regular else lo

    def validate(self) -> None:
        errors = []
        if self.source == "miniflux":
            if not self.miniflux_url:
                errors.append("MINIFLUX_URL fehlt")
            if not self.miniflux_token:
                errors.append("MINIFLUX_API_TOKEN fehlt")
        elif self.source == "greader":
            for key, val in (("GREADER_URL", self.greader_url), ("GREADER_USER", self.greader_user),
                             ("GREADER_PASSWORD", self.greader_password)):
                if not val:
                    errors.append(f"{key} fehlt")
        else:
            errors.append("SOURCE muss 'miniflux' oder 'greader' sein")
        if self.privacy not in ("standard", "strict"):
            errors.append("PRIVACY muss 'standard' oder 'strict' sein")
        if self.summary_profile not in ("full", "lean"):
            errors.append("SUMMARY_PROFILE muss 'full' oder 'lean' sein")
        if self.select_from not in ("unread", "new"):
            errors.append("SELECT_FROM muss 'unread' oder 'new' sein")
        if self.prefilter not in ("none", "laya", "embeddings"):
            errors.append("PREFILTER muss 'none', 'laya' oder 'embeddings' sein")
        if self.scoring_provider == "none" and self.prefilter != "laya":
            errors.append("SCORING_PROVIDER=none geht nur zusammen mit PREFILTER=laya")
        for stage, prov, model in (("SCORING", self.scoring_provider, self.scoring_model),
                                   ("SUMMARY", self.summary_provider, self.summary_model)):
            if stage == "SCORING" and prov == "none":
                continue
            if prov not in PROVIDERS:
                errors.append(f"{stage}_PROVIDER '{prov}' unbekannt (möglich: {', '.join(PROVIDERS)})")
            if not model:
                errors.append(f"{stage}_MODEL fehlt")
        if self.feed_mode not in ("daily", "items"):
            errors.append("FEED_MODE muss 'daily' oder 'items' sein")
        if errors:
            raise SystemExit("Konfigurationsfehler:\n  - " + "\n  - ".join(errors))
