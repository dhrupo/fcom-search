import argparse
import fcntl
import os
import sqlite3
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .live import LiveSearch
from .offers import OffersError, build_offers
from .places import Places
from .push import PushError, push_offers
from .server import make_server

NAMES = Path.home() / ".config" / "fcommerce-search" / "scrub_map.txt"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fcommerce-search", description="Turn crawled posts into offers for the site")
    p.add_argument("--config", default="fcommerce-search.toml")
    sub = p.add_subparsers(dest="command", required=True)
    push = sub.add_parser("push", help="rebuild offers from the crawler's store and send the changes to the site")
    push.add_argument("--allow-empty", action="store_true",
                      help="expire every offer when the crawler store really has none")
    serve = sub.add_parser("serve", help="run the live search page on this machine")
    serve.add_argument("--port", type=int, default=8799)
    serve.add_argument("--web-dir", default=str(Path(__file__).resolve().parents[3] / "web" / ".output" / "public"))
    return p


def private_names() -> tuple[str, ...]:
    path = Path(os.environ.get("FCS_SCRUB_MAP") or NAMES)
    try:
        return tuple(n for n in path.read_text(encoding="utf-8").splitlines() if n.strip())
    except OSError:
        return ()


def serve(config, port: int, web_dir: Path) -> int:
    places = Places(config.state_db)
    server = make_server(port, LiveSearch(config, places, names=private_names()), places, web_dir)
    print(f"live search on http://127.0.0.1:{server.server_port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(Path(args.config))
        if args.command == "serve":
            return serve(config, args.port, Path(args.web_dir))
        config.state_db.parent.mkdir(parents=True, exist_ok=True)
        with open(config.state_db.with_suffix(".lock"), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PushError("another push is running; try again when it ends") from None
            records = build_offers(config, allow_empty=args.allow_empty)
            result = push_offers(config)
    except (ConfigError, OffersError, PushError, sqlite3.Error, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{len(records)} offers; pushed {result.sent}, expired {result.expired}")
    return 0
