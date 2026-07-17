# Sunset Ledger

Tracks when automated license plate reader (ALPR) contracts held by US
public bodies come up for renewal, and alerts subscribers early enough to
organize around the actual leverage point. Documents and dates only --
no camera maps, no routing. See [`docs/schema-spec.md`](docs/schema-spec.md)
for the frozen v1 data model and machine rules; this README only covers
running the project.

## Status

Scaffolding stage. Implemented so far: `jurisdiction`, `vendor`,
`vendor_alias`, `reviewer`, and the migration-managed `fact_field`
vocabulary (see `registry/models.py`). Not yet implemented: `agreement`,
`document`, `fact`, `submissions`, `subscriptions`, alert delivery.

## Local development

```
python -m venv .venv
.venv/Scripts/activate        # Windows; `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Runs against SQLite with no further configuration. Email prints to the
console instead of sending; file uploads go to a local `media/` directory
instead of object storage.

## Tests

```
pytest
```

## Production

Set `DJANGO_SETTINGS_MODULE=config.settings.prod`. Full, accurate list of
required variables (not a rounded-off count -- this is every one `prod.py`
actually reads):

| Variable | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | yes | Django signing key |
| `ALLOWED_HOSTS` | yes | comma-separated host list |
| `DATABASE_URL` | yes | Postgres connection string, **must use the `app_runtime` role** (see below) |
| `STORAGE_ACCESS_KEY_ID` | yes | S3-compatible object storage credential |
| `STORAGE_SECRET_ACCESS_KEY` | yes | S3-compatible object storage credential |
| `STORAGE_BUCKET_NAME` | yes | object storage bucket |
| `STORAGE_ENDPOINT_URL` | yes | e.g. Backblaze B2 / Cloudflare R2 endpoint |
| `STORAGE_REGION_NAME` | yes | object storage region |
| `EMAIL_HOST` | yes | SMTP host of any transactional provider |
| `EMAIL_PORT` | no (default 587) | SMTP port |
| `EMAIL_HOST_USER` | yes | SMTP auth user |
| `EMAIL_HOST_PASSWORD` | yes | SMTP auth password |
| `DEFAULT_FROM_EMAIL` | no (default set in code) | alert sender address |

No other code changes are required to move from local SQLite to
production Postgres.

### Database roles and row-level security (Postgres only)

The machine-field immutability rule (see `docs/schema-spec.md`,
"Machine-field database protection") requires **two distinct Postgres
roles per environment**, provisioned once, outside of Django migrations:

```sql
CREATE ROLE migrator LOGIN PASSWORD '...';
CREATE ROLE app_runtime LOGIN PASSWORD '...';
GRANT ALL PRIVILEGES ON DATABASE sunset_ledger TO migrator;
-- app_runtime is granted table-level access by the app's own migrations
-- (Django's migrate, run as migrator, creates the tables and RLS policies
-- that reference app_runtime by name).
```

Deploy sequence:

1. Run `manage.py migrate` with `DATABASE_URL` pointing at the **`migrator`**
   connection string (schema owner, unaffected by RLS).
2. Run the actual web process and all cron jobs (`send_alerts`,
   `export_dataset`, `archive_sources`) with `DATABASE_URL` pointing at the
   **`app_runtime`** connection string instead. This is the value that
   normally lives in the deployed environment's persistent config;
   `migrator` is used only for the one-off migrate step, never for
   standing processes.

## License

Open source from commit one -- license to be finalized before the first
public submission form ships.
