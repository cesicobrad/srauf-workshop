"""Set up bots from the command line.

    python -m chatbot.manage add-bot --id acme --name Ana --business "Acme Physio" \
        --email info@acme.example --origins https://acme.example,https://www.acme.example
    python -m chatbot.manage add-docs --id acme docs/*.md docs/prices.pdf
    python -m chatbot.manage list
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .config import Settings
from .ingest import IngestError
from .service import ChatService


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m chatbot.manage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-bot", help="create or update a bot")
    a.add_argument("--id", required=True, help="short id used in data-bot, e.g. acme")
    a.add_argument("--name", required=True, help="assistant name shown in the widget")
    a.add_argument("--business", required=True)
    a.add_argument("--email", default="", help="where leads go")
    a.add_argument("--origins", default="", help="comma-separated sites allowed to embed it; empty = any")
    a.add_argument("--cap", type=float, default=None, help="monthly spend cap in USD")
    a.add_argument("--color", default="#1f6f5c")
    d = sub.add_parser("add-docs", help="upload files to a bot")
    d.add_argument("--id", required=True)
    d.add_argument("files", nargs="+")
    sub.add_parser("list", help="list bots and their documents")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    svc = ChatService(settings) if args.cmd == "add-docs" else ChatService(Settings(**{**settings.__dict__, "embeddings": "off", "mode": "demo"}))
    if args.cmd == "add-bot":
        if not re.fullmatch(r"[a-z0-9-]{2,40}", args.id):
            print("id: use 2-40 lowercase letters, digits or dashes")
            return 2
        cap = args.cap if args.cap is not None else settings.default_monthly_cap_usd
        svc.db.upsert_bot(args.id, args.name, args.business, args.email, args.origins, cap, args.color)
        print(f'Bot "{args.id}" saved. Embed: <script src="{settings.public_url or "https://YOUR-SERVER"}/widget.js" data-bot="{args.id}" defer></script>')
    elif args.cmd == "add-docs":
        if not svc.db.bot(args.id):
            print(f"No bot {args.id!r}; run add-bot first.")
            return 2
        for f in args.files:
            try:
                info = svc.add_upload(args.id, Path(f).name, Path(f).read_bytes())
                print(f"{f}: {info['title']!r}, {info['chunks']} sections")
            except (IngestError, OSError) as e:
                print(f"{f}: {e}")
    else:
        for bot in svc.db.bots():
            docs = svc.db.documents(bot.id)
            print(f"{bot.id}: {bot.name} ({bot.business}), cap ${bot.monthly_cap_usd}, {len(docs)} documents")
            for doc in docs:
                print(f"    {doc['title']} [{doc['kind']}, {doc['n_chunks']} sections]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
