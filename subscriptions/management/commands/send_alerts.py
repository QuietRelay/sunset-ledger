"""
Cron-triggered command: compute action opportunities for every Tier-2+
agreement, compare against each confirmed subscription's lead time, and
send via the pending -> sent|failed lifecycle in docs/schema-spec.md
("Alert delivery lifecycle") -- a pending alert_log row is written before
sending to reserve the (subscription_id, event_key) dedup slot, then
updated to sent/failed after the real attempt. Never marks delivery
successful before the send actually happens.

Sends through subscriptions.services.email, not a provider-specific call
here -- switching providers is a settings change, not a change to this
command.

No-op scaffold: subscription and alert_log models do not exist yet. Runs
cleanly with zero output rather than erroring, so the scaffolding milestone
can invoke it before any domain model is implemented.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send due deadline/vote/agenda-monitoring alerts to confirmed subscribers."

    def handle(self, *args, **options):
        self.stdout.write(
            "send_alerts: no subscription/alert_log models yet -- nothing to do."
        )
