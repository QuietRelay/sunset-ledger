"""
Postgres-only trigger enforcing Fact's evidentiary-identity lock at the
database level, independent of Django entirely -- same rationale as
0007's Document trigger. No-ops on SQLite.

Unconditional, no role-based exemption: nothing legitimately changes a
fact's identity fields on an existing row, by any path, including a
direct psycopg session connected as `migrator`. This composes correctly
with the legitimate lifecycle transitions in
registry.services.fact_lifecycle (retract_fact, and supersede_fact's
post-save close-out on the fact being superseded) without needing any
bypass, because those functions only ever touch the disjoint mutable
field set (status, valid_until, retraction_reason, retracted_by,
retracted_at) -- this trigger does not inspect those columns at all, so
it never has anything to object to when they change.
"""

from django.db import migrations

FUNCTION_NAME = "registry_fact_lock_identity"
TRIGGER_NAME = "fact_lock_identity_trigger"
TABLE = "registry_fact"

IDENTITY_COLUMNS = (
    "agreement_id", "field_id", "qualifier_id",
    "value_text", "value_number", "value_date", "value_bool",
    "scope_period_start", "scope_period_end", "valid_from",
    "primary_document_id", "page_or_section_reference", "excerpt",
    "effective_date_basis", "created_by_id",
)

_CHANGED_CHECK = " OR\n        ".join(
    f"NEW.{col} IS DISTINCT FROM OLD.{col}" for col in IDENTITY_COLUMNS
)

# One statement per schema_editor.execute() call -- see 0007's comment:
# psycopg3's default extended query protocol does not support multiple
# statements in a single execute(), which is what broke the first
# version of both trigger migrations.
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
        {_CHANGED_CHECK}
    ) THEN
        RAISE EXCEPTION
            'Fact %% evidentiary identity cannot change once created (agreement, field, '
            'qualifier, typed value, scope_period, valid_from, primary_document, '
            'page_or_section_reference, excerpt, effective_date_basis, created_by) -- '
            'create a replacement fact via '
            'registry.services.fact_lifecycle.supersede_fact instead.', OLD.id;
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
        ("registry", "0007_document_lock_cited_fields_trigger"),
    ]

    operations = [
        migrations.RunPython(create_trigger, drop_trigger),
    ]
