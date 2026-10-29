"""Settings, read once from the environment (and `.env`)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# USD per million tokens (input, output), list prices for prompts up to 100K tokens.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    # Unknown models are priced like Opus so the spend cap errs on the safe side.
    price_in, price_out = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def _path(raw: str | None, default: Path) -> Path:
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    mode: str = "auto"                      # auto | claude | demo
    model: str = "claude-haiku-5-5"
    effort: str = "low"
    db_path: Path = ROOT / "data" / "chatbot.db"
    embeddings: str = "local"               # local (fastembed, multilingual) | off (keyword search only)
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Knowledge bases up to this many characters (about 10k tokens) go to Claude whole; bigger ones as
    # the top 8 search results. 0 = always search.
    full_context_chars: int = 40000
    admin_password: str = ""                # empty: admin only reachable from this machine
    public_url: str = ""                    # used in the embed snippet shown in admin
    default_monthly_cap_usd: float = 5.00
    # Rate limits for the public chat endpoint.
    per_ip_per_minute: int = 8
    per_ip_per_day: int = 120
    per_bot_per_minute: int = 60
    max_question_chars: int = 600
    # Optional e-mail for leads. Without SMTP_HOST, leads are stored in the database and written to the log.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    seed_demo: bool = True                  # create the demo bot on first start

    @property
    def has_credentials(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))

    @property
    def use_claude(self) -> bool:
        return self.mode == "claude" or (self.mode == "auto" and self.has_credentials)

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        e = os.getenv
        return cls(
            mode=e("CHATBOT_MODE", "auto").lower(),
            model=e("CHATBOT_MODEL", "claude-haiku-5-5"),
            effort=e("CHATBOT_EFFORT", "low"),
            db_path=_path(e("DB_PATH"), ROOT / "data" / "chatbot.db"),
            embeddings=e("EMBEDDINGS", "local").lower(),
            embedding_model=e("EMBEDDING_MODEL", cls.embedding_model),
            full_context_chars=int(e("FULL_CONTEXT_CHARS", "40000")),
            admin_password=e("ADMIN_PASSWORD", ""),
            public_url=e("PUBLIC_URL", "").rstrip("/"),
            default_monthly_cap_usd=float(e("MONTHLY_CAP_USD", "5")),
            per_ip_per_minute=int(e("RATE_PER_IP_PER_MINUTE", "8")),
            per_ip_per_day=int(e("RATE_PER_IP_PER_DAY", "120")),
            per_bot_per_minute=int(e("RATE_PER_BOT_PER_MINUTE", "60")),
            smtp_host=e("SMTP_HOST", ""),
            smtp_port=int(e("SMTP_PORT", "587")),
            smtp_user=e("SMTP_USER", ""),
            smtp_password=e("SMTP_PASSWORD", ""),
            smtp_from=e("SMTP_FROM", ""),
            seed_demo=_bool(e("SEED_DEMO"), True),
        )
