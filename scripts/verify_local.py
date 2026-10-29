"""Check local HTTP login using generated credentials without printing secrets."""
from pathlib import Path
import re

import httpx


def main():
    root = Path(__file__).resolve().parents[1]
    env = dict(line.split("=", 1) for line in (root / ".env").read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
    base = "http://127.0.0.1:" + env.get("WEB_PORT", "8000")
    with httpx.Client(base_url=base, follow_redirects=False, timeout=15, trust_env=False) as client:
        assert client.get("/health/").json() == {"status": "ok"}
        assert client.get("/").status_code == 302
        login = client.get("/login/")
        assert login.status_code == 200 and "<h1>Вход</h1>" in login.text
        static_path = re.search(r'href="([^\"]+app\.[^\"]*css)"', login.text).group(1)
        static = client.get(static_path)
        assert static.status_code == 200 and "text/css" in static.headers["content-type"]
        token = re.search(r'name="csrfmiddlewaretoken" value="([^\"]+)"', login.text).group(1)
        response = client.post("/login/", data={
            "username": env["DJANGO_SUPERUSER_USERNAME"],
            "password": env["DJANGO_SUPERUSER_PASSWORD"],
            "csrfmiddlewaretoken": token,
        })
        assert response.status_code == 302, "Вход не выполнен"
        dashboard = client.get("/")
        assert dashboard.status_code == 200 and "Рабочее пространство готово" in dashboard.text
        assert client.get("/admin/").status_code == 403
        assert client.get("/logout/").status_code == 405
    print("HTTP OK: health, anonymous protection, CSS, login, dashboard, admin, POST-only logout")


if __name__ == "__main__":
    main()
