"""End-to-end checks against the running local stack and live public source."""
from pathlib import Path
import json
import re
import time

import httpx


def csrf(html):
    return re.search(r'name="csrfmiddlewaretoken" value="([^\"]+)"', html).group(1)


def main():
    root = Path(__file__).resolve().parents[1]
    env = dict(line.split("=", 1) for line in (root / ".env").read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
    report = []
    with httpx.Client(base_url="http://127.0.0.1:"+env.get("WEB_PORT", "8000"), timeout=20, trust_env=False) as client:
        login = client.get("/login/")
        response = client.post("/login/", data={"csrfmiddlewaretoken": csrf(login.text),
            "username": env["DJANGO_SUPERUSER_USERNAME"], "password": env["DJANGO_SUPERUSER_PASSWORD"]})
        assert response.status_code == 302
        for terms, mode, kind in [("телефон; смартфон", "any", "word"), ("телефон; сотовой", "all", "word")]:
            form = client.get("/search/")
            response = client.post("/search/", data={"csrfmiddlewaretoken": csrf(form.text), "terms": terms,
                "mode": mode, "kind": kind, "pages": "1", "number_kind": "lot_number"})
            assert response.status_code == 302
            path = response.headers["location"]
            end = time.monotonic()+150
            while True:
                page = client.get(path)
                assert page.status_code == 200
                if "Поиск выполняется" not in page.text:
                    break
                if time.monotonic() > end:
                    raise AssertionError("Поиск не завершён вовремя: "+path)
                time.sleep(2)
            assert "Не удалось завершить поиск" not in page.text
            matches = re.search(r"Найдено (\d+)", page.text)
            assert matches and int(matches.group(1)) > 0, "Нет результатов: "+path
            lot_id = re.search(r'href="/lots/(\d+)/"', page.text).group(1)
            report.append({"terms": terms, "mode": mode, "url": path, "count": int(matches.group(1)), "lot_id": int(lot_id)})
            print(f"Search {mode}: {matches.group(1)} matches", flush=True)

        lot_path = f"/lots/{lot_id}/"
        detail = client.get(lot_path)
        end = time.monotonic()+90
        while "Обновляем сведения" in detail.text and time.monotonic() < end:
            time.sleep(2)
            detail = client.get(lot_path)
        assert "Карточка проверена" in detail.text, "Карточка не обновлена"
        token = csrf(detail.text)
        for _ in range(2):
            response = client.post(f"/lots/{lot_id}/favorite/", data={"csrfmiddlewaretoken": token, "action": "add"}, headers={"HX-Request": "true"})
            assert response.status_code == 200 and 'favorite-icon selected' in response.text
        favorites = client.get("/favorites/")
        assert lot_path in favorites.text
        # Remove only the test favorite if it did not exist before the test.
        if 'value="remove"' not in detail.text:
            response = client.post(f"/lots/{lot_id}/favorite/", data={"csrfmiddlewaretoken": token, "action": "remove"}, headers={"HX-Request": "true"})
            assert response.status_code == 200
        print("Card refresh and idempotent favorite: OK", flush=True)
    (root / "data/search-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
