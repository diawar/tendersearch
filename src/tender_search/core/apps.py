from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "tender_search.core"
    verbose_name = "Закупки и поиски"

    def ready(self):
        from . import signals
