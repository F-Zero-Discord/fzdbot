import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: str
    server_id: int

    db_user: str
    db_password: str
    db_name: str
    db_host: str = "localhost"
    db_port: int = 3306

    fzd_api_base_url: str
    fzd_api_key: str

    log_level: str = "INFO"
    error_alert_channel_id: int | None = 1186081056143708250
    # Server channels the registration flow points users at. Optional: the copy
    # falls back to a plain name when an id is not configured.
    rules_channel_id: int | None = 1216173713012166797
    help_channel_id: int | None = 1216179321497063444
    faq_channel_id: int | None = 1216189837502316565
    # Which Discord role each GGP8 event grants its registrants: a
    # `scheduled_event_id` to a role id, as JSON. Empty leaves the sync off, and
    # an event absent from it is never touched.
    ggp8_event_roles: dict[int, int] = {}
    event_role_sync_seconds: int = 300
    scoreboard_display_podium: bool = False
    scoreboard_lines_per_block: int = 8
    scoreboard_refresh_seconds: int = 10

    @field_validator(
        "error_alert_channel_id",
        "rules_channel_id",
        "help_channel_id",
        "faq_channel_id",
        mode="before",
    )
    @classmethod
    def empty_channel_id_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        normalized = value.upper()
        if normalized not in valid_levels:
            raise ValueError(f"Invalid log level: {value}")
        return normalized

    @property
    def db_config(self) -> dict[str, Any]:
        return {
            "user": self.db_user,
            "password": self.db_password,
            "host": self.db_host,
            "db": self.db_name,
            "port": self.db_port,
            "autocommit": False,
        }


_env_file = ".env"


def use_env(name: str) -> None:
    """Read `.env.<name>` in place of `.env`, not on top of it: a setting the
    named file leaves out fails validation rather than being taken from `.env`.
    pydantic-settings skips an env file that does not exist, so a mistyped
    name is refused here.
    """
    global _env_file
    path = Path(f".env.{name}")
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist in {Path.cwd()}")
    _env_file = str(path)
    get_settings.cache_clear()


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=_env_file)  # type: ignore


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
