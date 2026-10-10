# Sunset Ledger: repository scope

This repository is **only** for Sunset Ledger: the Django app that tracks when
US public bodies' automated license plate reader (ALPR) contracts come up for
renewal and alerts subscribers (see `README.md` and `docs/schema-spec.md`).

- Do not add unrelated utilities, scripts, experiments, or applications here
  (for example Grab, Convert, AreaCode, terminal or desktop setups) unless the
  maintainer explicitly approves that specific addition in this repository.
- If a task isn't about Sunset Ledger, stop before writing files or running Git
  commands. Say which repository the work belongs in (`QuietRelay/grab`,
  `QuietRelay/area-code-lookup`, a new repository, ...) and ask, even if this
  repository is the only one attached to the session.
- Before committing or pushing, confirm the remote is
  `QuietRelay/sunset-ledger` and that the change is Sunset Ledger work.
