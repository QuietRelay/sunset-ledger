"""
Postgres-only row-level security for registry_factfield. No-ops entirely
on SQLite (checked via schema_editor.connection.vendor), which has no
role/RLS concept -- SQLite relies solely on the application-level guard in
registry.services.system_fields. See docs/schema-spec.md, "Machine-field
database protection" for the full design.

Assumes an `app_runtime` role already exists in the target database
(provisioned once per environment/CI run, outside of migrations -- see
README.md). RLS is *not* FORCEd, so the schema owner (the `migrator` role
that runs `manage.py migrate`) is unaffected and needs no special grant;
only a distinct, non-owning `app_runtime` role is subject to the policies
below.
"""

from django.db import migrations

TABLE = "registry_factfield"


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;")
    schema_editor.execute(
        f"""
        CREATE POLICY read_all_fact_field ON {TABLE}
        FOR SELECT USING (true);
        """
    )
    schema_editor.execute(
        f"""
        CREATE POLICY app_runtime_insert_descriptive_only ON {TABLE}
        FOR INSERT TO app_runtime
        WITH CHECK (category = 'descriptive');
        """
    )
    schema_editor.execute(
        f"""
        CREATE POLICY app_runtime_update_descriptive_only ON {TABLE}
        FOR UPDATE TO app_runtime
        USING (category = 'descriptive')
        WITH CHECK (category = 'descriptive');
        """
    )
    schema_editor.execute(
        f"""
        CREATE POLICY app_runtime_delete_descriptive_only ON {TABLE}
        FOR DELETE TO app_runtime
        USING (category = 'descriptive');
        """
    )


def disable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for policy in (
        "read_all_fact_field",
        "app_runtime_insert_descriptive_only",
        "app_runtime_update_descriptive_only",
        "app_runtime_delete_descriptive_only",
    ):
        schema_editor.execute(f"DROP POLICY IF EXISTS {policy} ON {TABLE};")
    schema_editor.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY;")


class Migration(migrations.Migration):

    dependencies = [
        ("registry", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(enable_rls, disable_rls),
    ]
