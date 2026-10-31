"""Generate local secrets once. Never overwrite an existing configuration."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
env = root / ".env"
if env.exists():
    print(".env уже существует; настройки и пароли сохранены.")
else:
    password = secrets.token_urlsafe(24)
    env.write_text(
        f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\n"
        f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n"
        f"DJANGO_SUPERUSER_USERNAME=admin\nDJANGO_SUPERUSER_PASSWORD={password}\n"
        "DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]\nWEB_PORT=8000\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_BOT_USERNAME=\n",
        encoding="utf-8",
    )
    (root / "data").mkdir(exist_ok=True)
    (root / "data/first-login.txt").write_text(
        f"TenderSearch\nАдрес: http://localhost:8000\nПользователь: admin\nПароль: {password}\n"
        "Административный веб-интерфейс отключён; пароль меняется командой Django changepassword. Файл храните локально.\n",
        encoding="utf-8",
    )
    print("Настройки созданы. Данные первого входа: data/first-login.txt")
