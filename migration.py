"""CLI wrapper around Alembic. Reads DATABASE_URL from .env automatically."""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from alembic import command
from alembic.config import Config as AlembicConfig

load_dotenv()


def cfg() -> AlembicConfig:
    root = Path(__file__).resolve().parent
    (root / "migrations" / "versions").mkdir(parents=True, exist_ok=True)
    return AlembicConfig(str(root / "alembic.ini"))


def main() -> int:
    parser = argparse.ArgumentParser(prog="migration")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    rev = sub.add_parser("revision")
    rev.add_argument("-m", "--message", required=True)

    up = sub.add_parser("upgrade")
    up.add_argument("revision", nargs="?", default="head")

    down = sub.add_parser("downgrade")
    down.add_argument("revision", nargs="?", default="-1")

    sub.add_parser("current")
    sub.add_parser("history")

    args = parser.parse_args()
    c = cfg()

    if args.cmd == "init":
        print("migrations/ already initialised")
    elif args.cmd == "revision":
        command.revision(c, message=args.message, autogenerate=True)
    elif args.cmd == "upgrade":
        command.upgrade(c, args.revision)
    elif args.cmd == "downgrade":
        command.downgrade(c, args.revision)
    elif args.cmd == "current":
        command.current(c)
    elif args.cmd == "history":
        command.history(c)

    return 0


if __name__ == "__main__":
    sys.exit(main())
