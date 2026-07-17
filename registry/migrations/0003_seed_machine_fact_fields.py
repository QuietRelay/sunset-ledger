"""
Data migration: seed the nine frozen machine fact_field rows (see
docs/schema-spec.md and registry.models.FactField.MACHINE_KEYS). This is
the *only* sanctioned place these rows are ever created -- guarded by
registry.services.system_fields.allow_system_field_mutation(), the same
mechanism that blocks every other write path, on both SQLite and Postgres.

Deliberately imports the live FactField model rather than
apps.get_model()'s historical snapshot: a historical model carries only
field definitions, not the custom save()/clean()/_guard() logic, so
seeding through it would silently skip the very validation this migration
exists to demonstrate. This is a conscious departure from the usual
"migrations use historical models" convention, acceptable here because
MACHINE_KEYS is frozen -- future changes to it are expected to be
deliberate, reviewed schema migrations, not incidental model drift.
"""

from django.db import migrations

from registry.models import FactField
from registry.services import system_fields

MACHINE_FIELDS = [
    dict(code="end_date", machine_key="end_date", value_type="date", is_period=False,
         description="Stated contract end date."),
    dict(code="start_date", machine_key="start_date", value_type="date", is_period=False,
         description="Stated contract start date."),
    dict(code="renewal_mechanism", machine_key="renewal_mechanism", value_type="text", is_period=False,
         description="auto_renew_unless_cancelled | affirmative_vote_required | unknown."),
    dict(code="non_renewal_notice_days", machine_key="non_renewal_notice_days", value_type="number",
         is_period=True, description="Notice required to prevent auto-renewal."),
    dict(code="termination_for_convenience_notice_days", machine_key="termination_for_convenience_notice_days",
         value_type="number", is_period=True,
         description="Notice required to terminate for convenience -- a distinct legal "
                      "mechanism from non-renewal notice; never used as its fallback."),
    dict(code="renewal_term_length_days", machine_key="renewal_term_length_days", value_type="number",
         is_period=True, description="Length of each renewal term."),
    dict(code="scheduled_vote_date", machine_key="scheduled_vote_date", value_type="date", is_period=False,
         description="A documented, agenda-confirmed vote date -- not an inferred estimate."),
    dict(code="cancellation_effective_date", machine_key="cancellation_effective_date", value_type="date",
         is_period=False, description="Effective date of a documented cancellation."),
    dict(code="termination_effective_date", machine_key="termination_effective_date", value_type="date",
         is_period=False, description="Effective date of a documented termination."),
]


def seed_machine_fields(apps, schema_editor):
    with system_fields.allow_system_field_mutation():
        for field in MACHINE_FIELDS:
            if FactField.objects.filter(machine_key=field["machine_key"]).exists():
                continue
            FactField.objects.create(
                code=field["code"],
                machine_key=field["machine_key"],
                category=FactField.Category.MACHINE,
                value_type=field["value_type"],
                is_period=field["is_period"],
                allows_multiple_concurrent=False,
                description=field["description"],
            )


def remove_machine_fields(apps, schema_editor):
    with system_fields.allow_system_field_mutation():
        for obj in FactField.objects.filter(
            machine_key__in=[f["machine_key"] for f in MACHINE_FIELDS]
        ):
            obj.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("registry", "0002_fact_field_rls"),
    ]

    operations = [
        migrations.RunPython(seed_machine_fields, remove_machine_fields),
    ]
