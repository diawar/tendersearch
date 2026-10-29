import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from .client import SourceError, ZakupClient


def main():
    parser = argparse.ArgumentParser(description="Публичный поиск zakup.gov.kz")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Поиск лотов")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--pages", type=int, default=1)
    search.add_argument("--output", type=Path)
    documents = commands.add_parser("documents", help="Документы объявления")
    documents.add_argument("announcement_id", type=int)
    args = parser.parse_args()
    try:
        with ZakupClient() as client:
            if args.command == "search":
                lots, seen = [], set()
                last = None
                for page in client.pages(q=args.query, limit=args.limit, max_pages=args.pages):
                    last = page
                    for lot in page.results:
                        if lot["id"] not in seen:
                            lots.append(lot)
                            seen.add(lot["id"])
                result = {"results": lots, "source_count": last.count, "has_more": bool(last.next_url)}
            else:
                result = [asdict(doc) for doc in client.documents(args.announcement_id)]
        output = json.dumps(result, ensure_ascii=False, indent=2)
        if getattr(args, "output", None):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output)
    except (SourceError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
