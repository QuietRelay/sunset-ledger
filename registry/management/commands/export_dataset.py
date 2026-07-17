"""
Scheduled command: dump the authoritative (document-backed and above)
records as CSV, JSON, and a SQLite file, and publish them to object
storage at both a timestamped and a stable "latest" path.

No-op scaffold: registry models do not exist yet.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Export the authoritative dataset as CSV, JSON, and SQLite to object storage."

    def handle(self, *args, **options):
        self.stdout.write(
            "export_dataset: no registry models yet -- nothing to export."
        )
