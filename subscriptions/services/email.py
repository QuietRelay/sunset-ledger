"""
Thin email-sending abstraction. Lives in `subscriptions`, not `registry` --
sending mail is a subscriber-communication concern, not part of the
authoritative-data domain, and `registry` must stay free of anything that
isn't schema, validation, or the deadline-computation engine.

Not wired up yet: subscriptions models (subscription, alert_log) do not
exist. This is the frozen interface shape only.
"""

from django.core.mail import send_mail
from django.conf import settings


def send(to: str, subject: str, body: str) -> None:
    """Send one email via whatever EMAIL_BACKEND is configured.

    Dev: console backend (prints instead of sending). Production: SMTP
    against any transactional provider -- switching providers is a
    settings change (EMAIL_HOST/EMAIL_HOST_USER/...), never a code change
    in this module or its callers.
    """
    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[to],
        fail_silently=False,
    )
