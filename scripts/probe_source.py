"""Explicit, bounded live integration check. Run from project root."""

from datetime import datetime, timezone
import json
from pathlib import Path

from tender_search.client import ZakupClient, SourceError, utc_datetime


def main():
    output = Path("data/probe")
    output.mkdir(parents=True, exist_ok=True)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "queries": [], "filters": [], "announcements": []}
    with ZakupClient() as api:
        all_lots = []
        for word in ("телефон", "бумага", "кабель"):
            pages = list(api.pages(q=word, limit=5, max_pages=2))
            lots = [lot for page in pages for lot in page.results]
            ids = [lot["id"] for lot in lots]
            assert len(ids) == len(set(ids)), "Дубли между страницами"
            assert len(pages) == 2
            report["queries"].append({"q": word, "count": pages[0].count, "pages": len(pages),
                                      "ids": ids, "names": [row.get("name_ru") for row in lots]})
            all_lots.extend(lots)
            print(f"Поиск {word}: {len(lots)} лотов на двух страницах", flush=True)

        for params, predicate in [
            ({"total_price__gte": 10000, "total_price__lte": 100000}, lambda row: 10000 <= row["total_price"] <= 100000),
            ({"status_id": 9}, lambda row: row["status"]["id"] == 9),
            ({"purchase_method_id": 12}, lambda row: row["purchase_method_id"] == 12),
            ({"offer_end_date__gte": "2026-10-28", "offer_end_date__lte": "2026-10-30"},
             lambda row: "2026-10-28" <= row["offer_end_date"][:10] <= "2026-10-30"),
        ]:
            try:
                page = api.search(q="телефон", limit=5, **params)
                report["filters"].append({"params": params, "returned": len(page.results),
                                          "matches": all(predicate(row) for row in page.results),
                                          "ids": [row["id"] for row in page.results]})
            except (SourceError, KeyError, TypeError) as exc:
                report["filters"].append({"params": params, "error": str(exc)})

        first = all_lots[0]
        additional = []
        if first.get("customers"):
            customer = first["customers"][0]["id"]
            additional.append(({"customer_id": customer},
                               lambda row: customer in [c["id"] for c in row.get("customers", [])]))
        if first.get("delivery_addresses"):
            region = first["delivery_addresses"][0]["id"]
            additional.append(({"delivery_address_id__in": region},
                               lambda row: region in [r["id"] for r in row.get("delivery_addresses", [])]))
        additional.append(({"lot_number": first["lot_number"]},
                           lambda row: row.get("lot_number") == first["lot_number"]))
        for params, predicate in additional:
            try:
                page = api.search(limit=5, **params)
                report["filters"].append({"params": params, "returned": len(page.results),
                                          "matches": bool(page.results) and all(predicate(row) for row in page.results)})
            except (SourceError, KeyError, TypeError) as exc:
                report["filters"].append({"params": params, "error": str(exc)})

        report["sorting"] = []
        for ordering in ("id", "-id"):
            try:
                rows = api.search(q="телефон", limit=5, ordering=ordering).results
                ids = [row["id"] for row in rows]
                report["sorting"].append({"ordering": ordering, "ids": ids,
                                           "matches": bool(ids) and ids == sorted(ids, reverse=ordering.startswith("-"))})
            except SourceError as exc:
                report["sorting"].append({"ordering": ordering, "error": str(exc)})

        report["references"] = []
        for name in ("lot-statuses", "purchase-methods", "systems", "katos"):
            try:
                page = api.reference(name, limit=2)
                report["references"].append({"name": name, "count": page.count, "has_more": bool(page.next_url),
                                             "returned": len(page.results), "fields": list(page.results[0]) if page.results else []})
            except SourceError as exc:
                report["references"].append({"name": name, "error": str(exc)})

        downloaded_pdf = False
        checked = set()
        for lot in all_lots:
            aid = lot["announcement_id"]
            if aid in checked:
                continue
            checked.add(aid)
            card = api.announcement(aid)
            detail_lots = api.announcement_lots(aid)
            docs = api.documents(aid)
            utc_datetime(card.get("offer_end_date"))
            record = {"id": aid, "external_id": card.get("external_id"),
                      "number": card.get("announcement_number"), "lot_count": len(detail_lots),
                      "list_status": lot.get("status"), "card_status": card.get("status"),
                      "documents": [{"name": d.name, "lot_id": d.lot_id, "group": d.group_name} for d in docs]}
            report["announcements"].append(record)
            print(f"Объявление {aid}: {len(detail_lots)} лотов, {len(docs)} файлов", flush=True)
            if not downloaded_pdf:
                for doc in docs:
                    if not doc.name.lower().endswith(".pdf"):
                        continue
                    try:
                        downloaded = api.download(doc, output / "files")
                        from pypdf import PdfReader
                        reader = PdfReader(downloaded["path"])
                        if len(reader.pages) > 200:
                            continue
                        text_pages = [page.extract_text() or "" for page in reader.pages]
                        text = "\n".join(text_pages)
                        if not text.strip():
                            continue
                        record["download_check"] = {**downloaded, "pages": len(text_pages),
                                                     "text_characters": len(text), "readable": True}
                        # No raw document text or personal details in the shareable report.
                        downloaded_pdf = True
                        break
                    except Exception as exc:
                        record.setdefault("download_errors", []).append(type(exc).__name__ + ": " + str(exc))
            if len(checked) >= 3 and downloaded_pdf:
                break
            if len(checked) >= 6:
                break
        report["acceptance"] = {"three_queries": len(report["queries"]) == 3,
                                "three_announcements": len(checked) >= 3, "readable_pdf": downloaded_pdf}
    path = output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Отчёт: {path}", flush=True)
    return 0 if all(report["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
