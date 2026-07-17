"""
Production: Postgres via DATABASE_URL, S3-compatible object storage,
SMTP email. Every value is sourced from the environment -- no secrets in
source control. See .env.example for the full list of required variables.
"""

import os

import dj_database_url

from .base import *  # noqa: F401,F403

SECRET_KEY = os.environ["SECRET_KEY"]

DEBUG = False

ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h]

DATABASES = {
    "default": dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=600),
}
# DATABASE_URL's user must be `app_runtime` (row-level-security-restricted --
# see docs/schema-spec.md, "Machine-field database protection"). `manage.py
# migrate` must instead be run with DATABASE_URL temporarily set to a
# connection string using the `migrator` role, which owns the schema and is
# unaffected by RLS. There is no separate Django setting for this -- it is
# an operational distinction in which env var value is exported before each
# command runs. See README.md for the exact deploy sequence.

# --- Object storage (S3-compatible: Backblaze B2, Cloudflare R2, or AWS S3) ---
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
AWS_ACCESS_KEY_ID = os.environ.get("STORAGE_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("STORAGE_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.environ.get("STORAGE_BUCKET_NAME")
AWS_S3_ENDPOINT_URL = os.environ.get("STORAGE_ENDPOINT_URL")  # e.g. B2/R2 endpoint
AWS_S3_REGION_NAME = os.environ.get("STORAGE_REGION_NAME")
AWS_DEFAULT_ACL = None  # bucket policy controls public read, not per-object ACLs
AWS_QUERYSTRING_AUTH = False

# --- Email (any SMTP-speaking transactional provider, e.g. Postmark/SES) ---
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "alerts@example.org")

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
