"""
Cron-triggered command: retry Internet Archive (Save Page Now) mirroring
for documents whose wayback_status is `pending` or `failed`, with bounded
attempts and backoff. Never blocks or reverses fact publication -- a
document's local archived_copy_path in object storage is what a Tier-2
fact depends on; wayback_status is purely supplementary redundancy. See
docs/schema-spec.md, "Source archival lifecycle."

No-op scaffold: the document model does not exist yet.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Retry Internet Archive mirroring for documents pending or failed archival."

    def handle(self, *args, **options):
        self.stdout.write(
            "archive_sources: no document model yet -- nothing to archive."
        )
