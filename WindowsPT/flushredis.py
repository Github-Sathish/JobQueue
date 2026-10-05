from django.core.management.base import BaseCommand
from django.conf import settings
import redis


class Command(BaseCommand):
    help = "Flush the configured Redis database"

    def handle(self, *args, **options):
        r = redis.Redis.from_url(settings.CELERY_BROKER_URL)
        r.flushdb()
        self.stdout.write(
            self.style.SUCCESS("Redis database cleared.")
        )