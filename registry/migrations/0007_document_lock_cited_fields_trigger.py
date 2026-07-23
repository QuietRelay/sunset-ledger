"""
Postgres-only trigger enforcing Document's cited-document-field lock at
the database level, independent of Django entirely -- a genuine direct-
SQL or different-application write would otherwise bypass both
Document.clean() (only runs on save()) and DocumentQuerySet's
update()/bulk_update() guards (only run through this Django codebase).
No-ops on SQLite, which has no trigger/procedural-language concept in
the same sense; SQLite relies solely on the application-level guards.

Unconditional -- unlike the machine-field RLS policy in 0002, this fires
regardless of connected role (including `migrator`). There is no
legitimate scenario, even via migration, where a document's
archived_storage_key/content_sha256/file_size_bytes/mime_type should
change after a fact cites it, so there is no role-based exemption here.

wayback_* fields are untouched by this trigger -- they are expected to
change repeatedly via the future archive_sources retry command
regardless of citation state.
"""

from django.db import migrations

FUNCTION_NAME = "registry_document_lock_cited_fields"
TRIGGER_NAME = "document_lock_cited_fields_trigger"
TABLE = "registry_document"

# One statement per schema_editor.execute() call -- psycopg3's default
# extended query protocol does not support multiple statements in a
# single execute(), unlike psycopg2's simple protocol. Migration 0002
# (already proven working in CI) follows this same one-call-per-statement
# pattern; combining CREATE FUNCTION and CREATE TRIGGER into one string
# passed to a single execute() is what actually broke the first version
# of this migration.
#
# The literal `%` in the RAISE EXCEPTION message (PL/pgSQL's own
# placeholder syntax for OLD.id) must be escaped as `%%` here: Django's
# schema_editor.execute() passes this string through psycopg's own
# parameterized-query machinery even though no params are supplied, so an
# unescaped `%` is read as an incomplete Python-style placeholder before
# the SQL ever reaches Postgres (psycopg.ProgrammingError: incomplete
# placeholder: '%'), not as a Postgres/PL/pgSQL syntax error.
CREATE_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {FUNCTION_NAME}() RETURNS TRIGGER AS $$
BEGIN
    IF (
        NEW.archived_storage_key IS DISTINCT FROM OLD.archived_storage_key OR
        NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256 OR
        NEW.file_size_bytes IS DISTINCT FROM OLD.file_size_bytes OR
        NEW.mime_type IS DISTINCT FROM OLD.mime_type
    ) THEN
        IF EXISTS (SELECT 1 FROM registry_fact WHERE primary_document_id = OLD.id)
           OR EXISTS (SELECT 1 FROM registry_factcorroboration WHERE document_id = OLD.id) THEN
            RAISE EXCEPTION
                'Document %% is cited by a fact -- archived_storage_key/content_sha256/'
                'file_size_bytes/mime_type cannot change. Create a new Document and link '
                'it via DocumentRelationship(corrected_version_of) instead.', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

CREATE_TRIGGER_SQL = f"""
CREATE TRIGGER {TRIGGER_NAME}
BEFORE UPDATE ON {TABLE}
FOR EACH ROW
EXECUTE FUNCTION {FUNCTION_NAME}();
"""

DROP_TRIGGER_SQL = f"DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON {TABLE};"
DROP_FUNCTION_SQL = f"DROP FUNCTION IF EXISTS {FUNCTION_NAME}();"


def create_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(CREATE_FUNCTION_SQL)
    schema_editor.execute(CREATE_TRIGGER_SQL)


def drop_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(DROP_TRIGGER_SQL)
    schema_editor.execute(DROP_FUNCTION_SQL)


class Migration(migrations.Migration):

    dependencies = [
        ("registry", "0006_remove_fact_fact_scope_period_order_and_more"),
    ]

    operations = [
        migrations.RunPython(create_trigger, drop_trigger),
    ]
