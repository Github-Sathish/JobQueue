from django.core.management.base import BaseCommand
from jobs.models import Job


class Command(BaseCommand):
    help = "Delete all jobs"

    def handle(self, *args, **kwargs):
        count = Job.objects.count()
        Job.objects.all().delete()
        self.stdout.write(
            self.style.SUCCESS(f"Deleted {count} jobs")
        )