"""Vigline CLI: init-db, serve."""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import load_settings
from .db import get_connection, run_migrations


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    db_path = args.db or str(settings.db_path)
    conn = get_connection(db_path)
    try:
        applied = run_migrations(conn)
    finally:
        conn.close()
    print(f"database: {db_path}")
    print(f"migrations applied: {applied or 'none (already current)'}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = load_settings()
    if args.db:
        import os

        os.environ["VIGLINE_DB_PATH"] = args.db
    print(f"Vigline {__version__} — serving (milestone 1: promo command center)")
    print(f"database: {args.db or settings.db_path}")
    uvicorn.run(
        "vigline.api.app:app",
        host=args.host,
        port=args.port,
        reload=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vigline", description="Vigline CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="Create/migrate the SQLite database")
    p_init.add_argument("--db", default=None, help="Database path override")
    p_init.set_defaults(func=cmd_init_db)

    p_serve = sub.add_parser("serve", help="Run the FastAPI server")
    p_serve.add_argument("--db", default=None)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
