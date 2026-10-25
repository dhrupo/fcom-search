import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from crawler.config import Config as CrawlerConfig
from crawler.config import ConfigError as CrawlerConfigError
from crawler.config import load_config as load_crawler_config


class ConfigError(ValueError):
    pass


DEFAULT_SECRET_FILE = Path.home() / ".config" / "fcommerce-search" / "push.env"


@dataclass(frozen=True)
class Config:
    crawler: CrawlerConfig
    crawler_data_dir: Path
    state_db: Path
    site_url: str | None = None
    secret_file: Path = DEFAULT_SECRET_FILE


def _safe_site_url(url) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    # The push secret travels in a header, so plain http is only for a site on this machine.
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost"))


def load_config(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e

    if not isinstance(raw.get("crawler_config"), str) or not raw["crawler_config"]:
        raise ConfigError('crawler_config must name the crawler\'s crawler.toml, e.g. crawler_config = "crawler.toml"')
    crawler_path = Path(raw["crawler_config"])
    if not crawler_path.is_absolute():
        crawler_path = path.parent / crawler_path
    try:
        crawler = load_crawler_config(crawler_path)
    except CrawlerConfigError as e:
        raise ConfigError(str(e)) from e
    except UnicodeDecodeError as e:
        raise ConfigError(f"cannot read {crawler_path}: {e}") from e

    # Same rule as the crawler CLI: a relative data_dir is relative to its crawler.toml.
    data_dir = crawler.data_dir if crawler.data_dir.is_absolute() else crawler_path.parent / crawler.data_dir
    site = raw.get("site") or {}
    if not isinstance(site, dict):
        raise ConfigError('site must be a table, e.g. [site] url = "https://fcommerce-search.pages.dev"')
    url = site.get("url")
    if url is not None and not _safe_site_url(url):
        raise ConfigError('site.url must be https://, or http:// only for 127.0.0.1/localhost, '
                          'e.g. "https://fcommerce-search.pages.dev"')
    if "secret_file" in site and not (isinstance(site["secret_file"], str) and site["secret_file"]):
        raise ConfigError('site.secret_file must be a path, e.g. "~/.config/fcommerce-search/push.env"')
    secret_file = Path(site["secret_file"]).expanduser() if site.get("secret_file") else DEFAULT_SECRET_FILE
    state_db = raw.get("state_db", "data/fcommerce-search.sqlite3")
    if not isinstance(state_db, str) or not state_db:
        raise ConfigError('state_db must be a path, e.g. state_db = "data/fcommerce-search.sqlite3"')
    state_db = Path(state_db).expanduser()
    if not state_db.is_absolute():
        state_db = path.parent / state_db
    return Config(crawler=crawler, crawler_data_dir=data_dir, state_db=state_db, site_url=url,
                  secret_file=secret_file)
