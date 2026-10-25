import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .offers import OffersError, build_offers
from .push import PushError, push_offers


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fcommerce-search", description="Turn crawled posts into offers for the site")
    p.add_argument("--config", default="fcommerce-search.toml")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("push", help="rebuild offers from the crawler's store and send the changes to the site")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = load_config(Path(args.config))
        records = build_offers(config)
        result = push_offers(config)
    except (ConfigError, OffersError, PushError, sqlite3.Error) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{len(records)} offers; pushed {result.sent}, expired {result.expired}")
    return 0
