import os

from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, CommandError, call_command


class Command(BaseCommand):
    help = "Apply migrations and create the initial administrator without changing existing passwords."

    def handle(self, *args, **options):
        call_command("migrate", interactive=False)
        username = os.getenv("DJANGO_SUPERUSER_USERNAME", "admin")
        users = get_user_model().objects
        existing = users.filter(username=username).first()
        if existing:
            if not existing.is_superuser or not existing.is_active:
                raise CommandError("Имя первого администратора занято неактивной или обычной учётной записью")
            self.stdout.write("Администратор уже существует; пароль сохранён.")
            return
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD")
        if not password or len(password) < 16:
            raise CommandError("Задайте DJANGO_SUPERUSER_PASSWORD длиной не менее 16 символов")
        users.create_superuser(username=username, password=password)
        self.stdout.write("Первый администратор создан.")
