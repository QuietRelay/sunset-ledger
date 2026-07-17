# Sunset Ledger -- frozen v1 schema and machine-rules specification

This document is the frozen output of the design phase. It is the source of
truth for implementation; if code and this document disagree, this document
wins until it is deliberately revised (and revisions are themselves logged,
consistent with the project's own provenance ethos).

## Mission

Track when ALPR (automated license plate reader) contracts held by US public
bodies come up for renewal, and alert subscribers early enough to organize
around the actual leverage point -- the notice-to-cancel deadline, not the
contract's stated end date. Documents and dates only. No camera maps, no
routing, no accounts/profiles/forums. Provenance is the entire product: no
record without a source document, no editorializing inside a record.

## Entity list (frozen)

```
jurisdiction, jurisdiction_history
vendor, vendor_alias
agreement, agreement_relationship, agreement_status_override
document, document_agreement, document_relationship, document_history
fact_field, fact_field_qualifier, fact, fact_corroboration
fact_dispute, fact_dispute_member
verification, verification_fact, required_fact_set
reviewer
submission, submission_document, submission_claim
subscription, alert_log
```

## Core layering

1. **agreement** -- one distinct procurement/contractual instrument (not
   "this jurisdiction's relationship with this vendor"). Master agreements
   and participating agreements are linked via `parent_agreement_id`;
   non-hierarchical relationships (re-procurement, related department
   arrangements) via `agreement_relationship`.
2. **document** -- an immutable archived artifact (contract, amendment,
   extension, purchase order, council approval, agenda packet, minutes,
   invoice, records response, news article). Carries `content_sha256`,
   archival metadata, and three distinct dates: `vote_or_approval_date`,
   `execution_date`, `stated_effective_date`.
3. **fact** -- the atomic material term. Never overwritten -- superseded.
   Carries `field_id` (see machine vs. descriptive below), an optional
   `qualifier_id` and `scope_period_start/end` (for legitimately concurrent
   values like year-specific pricing or phased camera counts),
   `valid_from`/`valid_until` (real-world legal effect) separate from
   `recorded_at` (system time), `effective_date_basis`, `primary_document_id`,
   `page_or_section_reference`, `excerpt`, and `status`
   (`active | disputed | retracted | proposed_option`).
4. **verification** -- a review event covering a specific set of facts
   (`verification_fact` join), not the agreement as a whole.

## Vendor alias normalization

`vendor_alias` uniqueness is enforced against a `normalized_alias` value
computed in **Python**, not a database `LOWER()`/`Lower()` function:
Unicode NFKC normalization, then whitespace trimming, then `casefold()`
(stronger than `lower()` -- Unicode-aware). This is a correctness
requirement, not a style preference: SQLite's built-in `LOWER()` is
ASCII-only, and Postgres's `lower()` does not perform full Unicode case
folding either -- both would silently under-normalize (e.g. failing to
recognize that a precomposed accented character and its decomposed
combining-character equivalent are the same alias), which is exactly the
kind of quiet data-quality gap this project can't afford in something
feeding vendor identification.

## Jurisdiction identity

The `(name, state, type)` unique constraint on `jurisdiction` is a **v1
duplicate-prevention heuristic** for the moderation workflow -- it exists
to catch "did someone already add this city," not to assert that those
three fields are a universally correct canonical identity for a governing
body. Two genuinely distinct external identifiers are preserved
separately: `fips_code` (external -- Census/GNIS, when one exists) and
`slug` (this project's own internal canonical identity, assigned once at
creation from name+state and never recomputed on a later rename, so a
renamed jurisdiction doesn't silently invalidate anything referencing it
by slug).

## Machine vs. descriptive fields

One `fact_field` table. `category = machine` rows are the fixed, frozen set
below; they are inserted **only** by migration, and the production database
role the running application uses has **no** INSERT/UPDATE/DELETE grant on
`category='machine'` rows -- enforced at the database permission layer.
Because SQLite has no equivalent role system, the identical rule is also
enforced in application-level model validation, so local/test environments
on SQLite cannot silently drift from production behavior. The deadline/tier
computation engine only ever reads specific, hardcoded `machine_key` values
-- it never iterates over "whatever is marked machine," and it never reads a
descriptive field.

Frozen machine keys (v1, closed set -- extending requires a schema
migration, not a data change):

```
end_date
start_date
renewal_mechanism
non_renewal_notice_days
termination_for_convenience_notice_days
renewal_term_length_days
scheduled_vote_date
cancellation_effective_date
termination_effective_date
```

`non_renewal_notice_days` and `termination_for_convenience_notice_days` are
independent legal mechanisms. Neither is ever used as an automatic fallback
for the other -- each is computed and labeled separately, and returns
`unknown` on its own terms when its specific required fact is missing.

## Units

```
period_unit enum: calendar_days | business_days | months | years
```

- `calendar_days`: direct subtraction.
- `months` / `years`: calendar-arithmetic convention -- same day-of-month
  (or month/day) in the target period, clamped to the target period's last
  valid day on overflow (e.g. Mar 31 minus 1 month -> Feb 28/29).
- `business_days`: **v1 never computes a deadline from this unit.** No
  jurisdiction-specific business-day calendar is supported yet, and
  approximating with calendar days can produce a materially wrong date --
  exactly what this project exists to prevent. Returns
  `calculated_date = None`, `confidence = none`, and an explicit
  `unresolvable_reason`.
- Missing/unrecognized unit: same treatment as `business_days`.

Financial facts carry `currency` (ISO 4217, default USD) and a mandatory
`pricing_basis` (`total_contract_value | annual_recurring |
monthly_recurring | one_time`) -- never inferred, always explicit.

## Deadline computation interface (frozen)

```
compute_action_opportunities(agreement, as_of) -> list[ActionOpportunity]
```

Returns a **list** from v1 onward (not a single result), because a single
agreement can legitimately produce more than one independent action
opportunity (e.g. a non-renewal notice deadline and a separately-clocked
termination-for-convenience option). Exactly one entry (or zero) is flagged
`is_primary` for the v1 UI's single highlighted "act by" date; the interface
never needs to change shape to surface more later.

Each `ActionOpportunity` exposes: `deadline_type`, `calculated_date`,
`next_documented_public_event` (only ever a scheduled_vote_date or a
computed notice deadline -- both trace to actual documented facts),
`recommended_agenda_monitoring_start` (a system-generated advisory only,
never labeled as a documented event), `input_fact_ids`, `formula_id`,
`calculation_version`, `confidence`, `unresolvable_reason`,
`blocking_dispute_id`, `is_primary`.

**`contract_expiration_date` is never substituted for an unknown action
deadline.** When nothing is computable, the UI states plainly that no
deadline is known, why, and shows the raw expiration date only as
reference information.

Facts with `effective_date_basis = unspecified_defaulted` are excluded from
`deadline_confidence` Tier 2+ eligibility and from `is_primary` selection
until a reviewer resolves the basis to something documented.

## Retroactive effect

```
fact.effective_date_basis enum: stated_in_document | execution_date | approval_date | unspecified_defaulted
```

Unqualified retroactive computation (`valid_from` preceding the document's
own `vote_or_approval_date`/`execution_date`) is permitted **only** when
`effective_date_basis = stated_in_document` -- i.e. the document's own text
explicitly states an effective date, confirmed by the reviewer at
promotion time, never inferred. The project reports that a document states
retroactive effect; it never concludes the agreement was legally operative
during the intervening period -- that is a legal conclusion outside scope.

## Agreement status

Derived, not a freely mutable column:

```
active cancellation_effective_date fact (value <= as_of)  -> cancelled
active termination_effective_date fact (value <= as_of)   -> cancelled
contract_expiration_date in the past, nothing covers as_of -> expired
start_date fact in the future                              -> pending
otherwise                                                   -> active
```

Manual correction only through `agreement_status_override`
(`override_status`, `override_reason`, `overridden_by`, `overridden_at`,
`expires_at`) -- never a silent edit to the derived value.

## Verification / tier derivation

- No fact for a field -> not tiered, "unknown."
- Active fact with `primary_document_id` -> **Tier 2 (document-backed)**.
- Additionally covered by a `verification_fact` whose verification postdates
  the fact, and the fact is still `active` -> **Tier 3 (verified)**.
- A `disputed` fact is excluded from Tier 2/3 while the dispute is open. This
  exclusion is a live, read-time computation -- it never deletes or
  invalidates the historical `verification_fact` row that previously
  covered the fact; that row remains a permanent record of what was
  confirmed and when.
- `deadline_confidence` (agreement-level) = minimum tier across
  `required_fact_set(purpose=deadline_confidence)` rows applicable given the
  agreement's actual `renewal_mechanism` (conditional requirement).
- `record_completeness` is a separate, informational score. A missing
  `camera_count` never downgrades a fully verified deadline.
- Alert eligibility = `deadline_confidence >= Tier 2` and a non-null
  documented event or advisory window. **Unsupported (unpromoted)
  submissions never trigger public alerts under any circumstance** --
  informational alerts require the underlying agreement to be
  document-backed, even though they don't require full verification.

## Moderation workflow

`submission` (with `origin`: `public | maintainer_research | bulk_import` --
same pipeline regardless of origin, no privileged shortcut) carries
`submission_document` attachments and `submission_claim` rows. A reviewer
promotes, rejects, or holds each claim independently; promotion creates a
`fact` at Tier 2. Tier 3 requires a distinct, subsequent `verification`
event, ideally from a second reviewer. Rejected claims are retained with
reason, never shown publicly. There is exactly one path facts enter the
system through, used by the public and by maintainers alike.

## Machine-field database protection (Postgres mechanism)

The v1 immutability rule for `category='machine'` `fact_field` rows is
enforced by **Postgres row-level security (RLS)**, not a role-based
`REVOKE` alone, using two distinct database roles per environment:

- `migrator` -- owns the schema, runs `manage.py migrate` (including the
  data migration that seeds the nine frozen machine rows), never used by
  the running application.
- `app_runtime` -- the role the deployed application (and its cron jobs)
  actually connects as for all normal traffic. Distinct from `migrator`.

`registry_fact_field` has RLS enabled (not `FORCE`d, so `migrator`, as
table owner, is unaffected and needs no special grant). Four policies:
unrestricted `SELECT` for everyone (the deadline engine must be able to
read machine rows), and `INSERT`/`UPDATE`/`DELETE` scoped `TO app_runtime`
requiring `category = 'descriptive'`. This means `app_runtime` is blocked
by the database itself from ever writing a machine-category row, even via
raw SQL through a compromised or buggy code path -- not merely hidden from
an admin UI.

This is Postgres-only; SQLite has no role/RLS concept. The identical rule
is therefore also enforced at the Django model layer
(`FactField.save()`/`.delete()`, see `registry/models.py`), which is what
gives SQLite dev/test environments the same behavior and serves as
defense in depth even on Postgres, where the database would already have
refused the write.

Role provisioning (`CREATE ROLE migrator`, `CREATE ROLE app_runtime LOGIN
...`) is a one-time, per-environment infrastructure step -- intentionally
*not* done by a Django migration, since role creation is cluster-level, not
schema-level, and some managed Postgres providers restrict it. See
`README.md` for the exact commands and the resulting two-DATABASE_URL
production model.

**Bulk-operation gap.** `Model.save()`/`.delete()` guards do not cover
`QuerySet.update()`, `QuerySet.delete()`, `bulk_create()`, or
`bulk_update()` -- all four write directly via SQL without calling an
instance's overridden methods. On Postgres this doesn't matter: RLS
applies per-statement regardless of which Django API generated the SQL,
so `app_runtime` is blocked on all four paths exactly as it is on
`save()`/`delete()`. On SQLite there is no equivalent, so a dedicated
`FactFieldQuerySet` (in `registry/models.py`) overrides all four methods
with the same guard check. On SQLite, this QuerySet is the *only*
protection those four paths have -- not defense in depth, load-bearing.

**The Python guard context is not a universal override, and tests must
never assume it is.** `allow_system_field_mutation()` only ever affects
`registry`'s own application-level check -- it has no relationship to a
Postgres session's role or RLS policies. Entering the context and then
writing to a machine row as `app_runtime` still fails, exactly as it
would outside the context, because RLS doesn't know the context manager
exists. The context is genuinely authoritative *only* where there is no
database-level backstop at all (SQLite, or a privileged role like
`migrator` that owns the schema and isn't subject to the RLS policies in
the first place). See `registry/tests/test_fact_field_rls_postgres.py`
for the role-aware proof of this, including a dedicated integration test
using a second connection authenticated as `migrator` to show its
different privileges directly, rather than asserting it indirectly.

One concrete consequence worth documenting precisely: on Postgres, a
`save()` call that attempts to modify a machine row as `app_runtime`
surfaces as a `DatabaseError` (confirmed against a real run to be
`ProgrammingError` specifically -- the RLS policy violation itself, not
necessarily a downstream constraint collision), not a clean rejection.
Django's `_save_table()` (`django/db/models/base.py`) tries an `UPDATE`
first and only falls back to `INSERT` "if that doesn't update anything"
(its own comment). RLS's `USING` clause makes a machine-category row
invisible to `app_runtime`'s `UPDATE` policy, so the `UPDATE` matches zero
rows *without erroring* -- Postgres does not distinguish "no such row"
from "row exists but you can't see it" for a permissive `USING`-clause
mismatch. Django reads the zero-row result as "this row doesn't exist
yet" and attempts an `INSERT` with the existing primary key, which
Postgres then rejects -- either the INSERT policy's `WITH CHECK` clause or
plain primary-key uniqueness, depending on the specific values involved,
which is exactly why tests assert the broad `DatabaseError` rather than a
specific subclass. This was confirmed by reading Django's `_save_table`
source directly, not inferred from the symptom alone -- it is a real
Django/RLS interaction, not test isolation, fixture state, or object
state, and it is not masked with `force_update`.

**Transaction isolation when testing this.** Postgres aborts the entire
enclosing transaction after any error within it (unlike SQLite) -- a test
that triggers an RLS rejection and then tries a follow-up assertion
(`refresh_from_db()`, a confirming `SELECT`) on the same transaction will
find *that* fails too, with "current transaction is aborted," masking the
real result. Every statement in `test_fact_field_rls_postgres.py` that is
confirmed to actually raise (`save()`, `bulk_create()`) is wrapped in its
own `transaction.atomic()` savepoint for exactly this reason. Not every
rejected write raises, though -- confirmed against a real run:
`Model.delete()`, `bulk_update()`, `QuerySet.update()`, and
`QuerySet.delete()` do not raise at all when RLS's `USING` clause filters
their target row out; the statement completes normally with a clean
zero-row result, and there is nothing for a savepoint to roll back. Those
four are asserted with a tolerant helper and proven instead by checking
that the row's state is unchanged afterward. The dividing line is whether
the operation has anything resembling `save()`'s update-then-insert
fallback to collide with -- these four don't.

**Validation vs. RLS are two distinct, independently-tested layers.**
Attempting to flip a descriptive row into a machine row through
`model.save()` is caught by `FactField.clean()` (`machine_key` must be one
of the frozen `MACHINE_KEYS`) and raises `ValidationError` *before* any
SQL is sent -- this is the application layer, not RLS, catching it. A
separate test bypasses `clean()` entirely (via `QuerySet.update()`, which
never calls it) to prove RLS *independently* rejects the same transition
via its `WITH CHECK` clause, regardless of what application-level
validation would have said.

## Alert delivery lifecycle

`alert_log` rows are never written as if delivery already succeeded.
Lifecycle: `pending -> sent | failed`, with a required unique
`(subscription_id, event_key)` constraint.

- A `pending` row is inserted *before* attempting to send -- this reserves
  the dedup slot so two concurrent/overlapping cron runs can't double-send,
  but a `pending` row makes no claim about delivery.
- After the real send attempt, the row is updated to `sent` (with
  `sent_at`) or `failed` (with `last_error`, incremented `attempt_count`).
  `failed` and stuck `pending` rows (e.g. a crashed process) are safe to
  retry on the next run.
- `event_key` is derived from `(agreement_id, alert_type, deadline_type,
  calculated_date)` -- **agreement identity is mandatory in the key, not
  optional.** A subscription is scoped to a *jurisdiction*, and a single
  jurisdiction can have multiple simultaneous agreements (see "Agreement
  identity" below) -- without `agreement_id` in the key, two different
  agreements sharing the same alert type and the same calculated date
  would collide and only one alert would ever send. Keying on
  `(subscription_id, event_key)` with `event_key` already agreement-scoped
  means: a daily unchanged deadline correctly no-ops (the same key already
  has a `sent` row), a deadline that moves produces a fresh alert, and
  concurrent agreements never suppress each other.

## Source archival lifecycle

`document` carries its own retry-safe archival status, independent of
whether the underlying fact can be published:

```
wayback_status    [pending | succeeded | failed]
wayback_attempts  int
wayback_last_error  (nullable)
wayback_url       (nullable)
wayback_saved_at  (nullable)
```

A fact's promotion to Tier 2 depends only on the document's local
`archived_copy_path` (already in object storage) -- **Internet Archive
mirroring failure never blocks fact publication.** A separate
`archive_sources` command retries `pending`/`failed` documents on a
bounded schedule (e.g. exponential backoff capped at N attempts), updating
status/attempts/error each run.

## Public export boundary (versioned public interface)

CSV/JSON/SQLite exports are a **deliberately-serialized public interface**,
not a database dump. The export schema is versioned independently of the
internal schema (`export_schema_version`) so consumers can detect
breaking changes. Excluded from every export, unconditionally:

- `subscription`, `alert_log` (subscriber emails and delivery history)
- `confirmation_token`/`unsubscribe_token` or any credential-like value
- `reviewer.contact_email` and any other private reviewer field --
  `reviewer.display_name` only, if reviewer attribution is exported at all
- `submission`, `submission_document`, `submission_claim` in any
  non-`promoted` state -- only facts that have actually been promoted are
  public
- `document.unredacted_copy_status`/`unredacted_copy_sha256`/
  `unredacted_access_policy` and any other restricted-copy metadata that
  could aid reconstructing what was deliberately redacted
- `fact_dispute`/`fact_dispute_member` internal reviewer notes (the public
  view shows that a dispute exists and its resolution, not the internal
  deliberation)

The export is produced by a dedicated serializer per entity, not a `SELECT
*`, precisely so a future column addition to an internal table can't leak
into the public interface by default -- new internal fields are private
until explicitly added to the exporter.

## Public upload security boundary

See `docs/upload-security-policy.md` for accepted MIME types, size limits,
filename/storage-key handling, quarantine behavior, serving headers, and
the v1 malware-scanning policy (not yet implemented -- documented as a
known gap, mitigated by quarantine-until-reviewed + forced content-type
serving, not silently ignored).

## Milestones

- **Milestone 1 (technical deployment):** full schema, deadline engine
  tested against the frozen worked examples (including retroactive and
  coverage-gap branches), ~10 agreements entered through the real
  submission/promotion pipeline, read-only browse/search/detail pages with
  all distinct dates clearly separated, CSV/JSON export. No public
  submission form, no email.
- **Public MVP:** adds the public submission/correction forms and the
  email subscription + alert pipeline. This is the milestone that actually
  satisfies the project's success criterion (find a jurisdiction in under
  30 seconds, see one unambiguous date with a source, subscribe in one
  field).

## Deliberately deferred to v2

Agenda scraping (Legistar/Granicus/CivicPlus/NovusAGENDA/PrimeGov keyword
scraping) -- schema-ready (`jurisdiction.agenda_platform`) but not built.
