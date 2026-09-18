# Railway and event update — verification

Date: 2026-09-17

**111 tests passed** using Python 3.12 and the pinned core dependencies.

Command, run from the project directory:

```sh
python -m pytest -q
```

The suite includes 50 existing core/dashboard/branding tests and 61 new parameterized checks. Existing expectations were updated for the default dot prefix and the additional persistent event-ticket view. `pytest.ini` limits collection to `tests/`, avoiding an optional legacy game whose filename otherwise matches pytest's collection pattern.

Covered behavior:

- Required commands register; the persistent event ticket button is registered at startup.
- Existing custom prefixes still work alongside `.`.
- Reward parsing, duplicates, invalid values, threshold rewards, even rounding, and exact cash formatting.
- Explicit reward choices: per-invite cash, fixed cash, and Nitro; no automatic reward or fallback selection.
- All eligible options appear in the picker, with no default. Only the ticket owner can select.
- Saved reward selection and safe migration from the prior database schema.
- Approval requires an explicit choice; stale/blocked/ineligible or already-approved selections cannot overwrite a claim.
- Server-promo, leave/rejoin, profile, onboarding, age, manual findings, and no-rules calculations.
- Conservative attribution for single invite changes, vanity uses, ambiguous batches, and expired/deleted invites.
- Duplicate gateway join/leave notifications and preexisting-member rejoins do not earn duplicate credit.
- Concurrent message counting, bot/webhook/DM exclusions, and SQLite persistence through a new connection.
- Embed branding, setup ownership, rewards → rules → category → preview flow, and back navigation.
- Event publication permissions, duplicate active-event prevention, and ending an event.
- Concurrent event-ticket creation and saved ticket cutoffs.
- Ticket-owner access controls, existing-ticket integration, and wrong-category rejection.
- Join cutoff, cross-guild isolation, unknown-source exclusion, reporting, channel rename, and owner notification targets.
- Manual rule findings, correction, rejection, reopening, proof requirements, approval recalculation, and duplicate approval rejection.
- Promotion mode locked for members and correctable by administrators.
- Cmds-channel enforcement with deduplicated RESET findings.
- Transient Discord member-fetch errors block reward approval.
- Proof attachment requirement, image signatures, correct destination, cross-server denial, and failed-forward behavior.

Compilation of the active Python modules and test files also succeeded. No production database, Discord token, real messages, payments, Nitro gifts, or moderation actions were used in tests.

Limitations:

- Discord operations are mocked; a live test server should verify permissions, real invite events, ticket overwrites, embeds/buttons, attachments, and owner pings.
- Docker was unavailable in the development environment. The Docker image and Railway deployment were not executed. The included entrypoint prepares a root-owned persistent volume and then drops privileges.
- No Railway account or Discord credentials were provided. Set the environment variables and attach the volume described in `RAILWAY_AND_EVENTS.md` before deployment.
- The previous report `TEST_REPORT.md` belongs to the supplied version. This update did not rerun that version's optional JavaScript browser simulation or legacy cog integrations.
- One dependency warning remains: Python 3.12 deprecates `audioop`, which discord.py imports. It does not cause test failures; use the supplied Python 3.12 Docker base.
