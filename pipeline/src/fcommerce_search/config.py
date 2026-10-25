import tomllib
from dataclasses import dataclass
from pathlib import Path

from crawler.config import Config as CrawlerConfig
from crawler.config import ConfigError as CrawlerConfigError
from crawler.config import load_config as load_crawler_config


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    crawler: CrawlerConfig
    crawler_data_dir: Path


def load_config(path: Path) -> Config:
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
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

    # Same rule as the crawler CLI: a relative data_dir is relative to its crawler.toml.
    data_dir = crawler.data_dir if crawler.data_dir.is_absolute() else crawler_path.parent / crawler.data_dir
    return Config(crawler=crawler, crawler_data_dir=data_dir)
