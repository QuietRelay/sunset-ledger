# Public upload security policy (v1)

Applies once the `submissions` public form exists (not yet implemented).
Documented now so the boundary is designed before the form is built, not
retrofitted after.

## Accepted MIME types

Allowlist only, verified by **content-sniffing the uploaded bytes**, never
by trusting the client-supplied `Content-Type` header or the filename
extension:

- `application/pdf`
- `image/png`
- `image/jpeg`

Nothing else is accepted in v1. Scanned agenda packets and contracts are
overwhelmingly PDF or page-image formats; this list is intentionally short
rather than permissive.

## Size limit

25 MB per file (generous enough for a multi-page scanned contract, bounded
enough to prevent storage abuse via the public form).

## Filename handling

The original filename is **never** used to construct a storage path or a
served URL, and is never trusted as-is even for display (path separators,
control characters, and non-printable characters are stripped before it is
shown anywhere). Storage key generation:

```
{sha256_prefix}/{uuid4}.{safe_ext}
```

`sha256_prefix` groups by content hash (enables dedup detection against
existing documents before human review); `uuid4` prevents key enumeration;
`safe_ext` is derived from the sniffed MIME type, not the client-supplied
extension.

## Quarantine states (four, not one linear gate)

An uploaded file moves through four states that are **not** all the same
gate, and reaching one does not imply reaching another:

1. **Private, reviewer-accessible.** The instant an upload is accepted at
   intake. Visible only to authenticated reviewers, via the safe-download
   path in the next section -- never at a public URL.
2. **Validated for allowed type/size.** Passed the MIME allowlist
   (content-sniffed), size limit, and filename/storage-key handling in
   this document. Still not public -- this only means the file is a kind
   of thing the system is willing to store and show a reviewer at all.
3. **Publicly publishable.** A reviewer has explicitly and separately
   confirmed the *file itself* is fit to host at a public URL (e.g. free
   of unaddressed personal information, correctly typed, nothing about it
   independently objectionable). This is its own affirmative flag, set by
   a reviewer action distinct from claim promotion.
4. **Promoted as evidence.** The `submission_claim` referencing this
   document was promoted into a `fact` -- an evidentiary decision ("this
   document supports this claimed value"), entirely independent of
   whether the file has been cleared for public hosting.

**Promotion (state 4) must never be treated as proof of state 3.** A
reviewer can promote a claim -- confirming the document supports a fact --
without having separately reviewed whether the file is safe/appropriate to
serve publicly; a document can sit between states 4 and 3 (evidence for a
live fact, cited internally, not yet public) if that separate review
hasn't happened. Conversely, reaching state 3 does not retroactively
validate any claim the file happens to support. The two axes are
independent flags on `document`, not sequential steps in one pipeline, and
the public-facing site only ever serves a file that has *both* -- state 3
set, and only through documents actually referenced by a currently
promoted fact.

## Safe serving headers

Once a file reaches "publicly publishable," it is served with:

- `Content-Type` forced server-side from the stored, sniffed MIME type --
  never reflected from a request header or stored client-supplied value
- `X-Content-Type-Options: nosniff`
- `Content-Disposition: inline` for PDFs/images intended for in-browser
  viewing, `attachment` available as an explicit download option -- never
  a type that a browser could interpret as executable or as HTML/script

## Reviewer access to untrusted uploads (states 1-2, pre-publication)

Reviewers are a **higher-value target** than the public while looking at
raw, unvetted community submissions -- their sessions carry moderation
privileges, so an upload that slipped something malicious past
content-sniffing is more dangerous served to a reviewer than to an
anonymous visitor. Access to a document that has not yet reached "publicly
publishable" is therefore stricter than the public-serving policy above,
not merely gated by login:

- Served from a **separate origin/subdomain** from the authenticated
  admin/moderation session wherever practical (e.g. a distinct
  `review-files.` host), so that even a successful content-type confusion
  can't execute in a context that shares cookies with the reviewer's
  session.
- Regardless of origin, **always forced `Content-Disposition: attachment`**
  for anything not yet publicly publishable -- never `inline` -- so the
  browser downloads rather than renders it, even for PDFs/images that
  would otherwise be shown inline once public.
- Same forced `Content-Type` + `nosniff` discipline as the public path,
  applied here too, not only after promotion.

## V1 malware-scanning policy (known gap, not silently skipped)

**V1 does not run active malware/antivirus scanning** (no ClamAV or
equivalent integration yet). This is a real, documented gap, mitigated by:

1. The MIME allowlist + content-sniffing above rejects executable and
   script-bearing file types outright.
2. The quarantine-until-reviewed workflow means a human looks at every
   file before it is ever reachable at a public URL.
3. Forced content-type + `nosniff` serving means even a file that slipped
   something malicious past sniffing cannot execute in a visitor's browser
   context.

ClamAV-based (or equivalent) active scanning is deferred to a later
milestone and tracked as a known limitation, not assumed solved.
