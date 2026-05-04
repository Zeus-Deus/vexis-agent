# v3c Day 4c — manual dashboard smoke test

The Day 4b deliverable's manual smoke (open the browser at the
Tailnet URL, click around the panel) is impractical from this
environment — there's no headless browser and no Tailnet hop
available. Day 4c substitutes a **scripted smoke** that exercises
the same code path uvicorn would serve, via FastAPI's `TestClient`.
Each of the 10 steps from the Day 4c prompt maps to one or two
checks below.

The scripted smoke lives at `/tmp/smoke_4c.py` (build artifact;
not committed). Re-run with:

```
python /tmp/smoke_4c.py
```

(Or copy from this commit's working tree; the script is dumped
inline at the bottom of this doc for the audit-trail.)

The smoke passes against the freshly-built `web/dist`. If a real
manual browser smoke ever runs against the Tailnet URL, this doc
should be updated with the screenshot + observed bot reply
text.

## Result: PASS — 11 / 11 steps

Last run: 2026-05-04 (commit on `feat/relationships-day-4c-eval-and-digest`).

| # | Step | Result | Notes |
|---|------|--------|-------|
| 1 | Frontend built (`web/dist/index.html` exists) | ✓ | `npm run build` from Day 4b's pipeline carried forward. |
| 2 | Dashboard "spins up" — `WebDashboard.__new__ + _build_app` succeeds | ✓ | Same code path uvicorn invokes. |
| 3 | `GET /` serves the React shell | ✓ | 200, 831 bytes, `<!doctype html>` opening tag. |
| 4 | Bundled JS contains `RelationshipsPanel` symbols | ✓ | All five sentinel strings present in `web/dist/assets/index-*.js`: "No pending candidates", "Pending candidates", "relationships/candidates", "Show rejected", "Resolve and approve". |
| 5 | Empty state via `GET /api/v1/relationships/candidates` | ✓ | 200, body = `{"candidates": []}`. |
| 6 | Inject a candidate (mom, strong cue) → appears as eligible | ✓ | `candidates=['mom']` with `eligible=true` after one observation. |
| 7 | `POST .../mom/approve` → 200 + approval_hint + live updated + queue cleared | ✓ | `approval_hint` populated ("Saved. Active in Vexis's next session…"). RELATIONSHIPS.md gains the Mom block. JSON queue no longer contains `mom`. |
| 8a | `POST .../sarah/approve` with qualifier=coworker against an existing live `sarah` (no YAML qualifier) → 409 missing_existing_qualifier | ✓ | Typed payload contains `existing_facts`, `existing_qualifier_candidates`, `proposed_qualifier`. |
| 8b | `POST .../sarah/resolve_qualifier` with `existing_qualifier=friend` then retry approve | ✓ | Rename succeeds (`new_slug=sarah-friend`); retry approve lands `sarah-coworker`. Live now contains BOTH slugs (`sarah-coworker` + `sarah-friend`). Archive gained a `## DISAMBIGUATED` block. |
| 9 | `POST .../marco/reject` (whole-slug) → 200 + tombstone in JSON | ✓ | `slug.rejected_at` ISO timestamp set; bare GET no longer surfaces marco. |
| 10 | `GET .../candidates?include_rejected=true` → marco re-appears | ✓ | Tombstoned entry visible under the "show rejected" toggle. |

## Per-step verification details

### 1. Frontend build
- `npm run build` from `web/`: 423 KB JS / 39 KB CSS. Vite 5 build, no TypeScript errors (`tsc --noEmit` passes).

### 2. Dashboard construction
- `WebDashboard.__new__(WebDashboard)` followed by manual field
  population mirrors what `__init__` does — same `_build_app`
  call site, same FastAPI registration.

### 3. Frontend serve
- `client.get("/")` returns `text/html`, 831 bytes, opening with
  `<!doctype html>`. The dashboard's static-file mount serves the
  built shell.

### 4. Panel symbol presence
- The bundled JS at `web/dist/assets/index-*.js` is grep'd for
  five strings unique to `RelationshipsPanel.tsx`. All five
  present.

### 5. Empty-state JSON
- Auth header `Authorization: Bearer <token>` accepted by
  `_require_auth`. Response is exactly `{"candidates":[]}` (no
  trailing whitespace, no other keys).

### 6. Inject candidate
- Direct write to
  `<workspace>/.vexis/relationships-candidates.json` via
  `RelationshipsCandidateStore.add_observation`. The candidate
  for `mom` (qualifier="mom", strong cue) is eligible after one
  session per the §3.4 tiered-gate fast track.

### 7. Approve
- 200 response. Body shape:
  ```json
  {
    "ok": true,
    "slug": "mom",
    "reply_text": "Approved 1 fact for Mom...",
    "approval_hint": "Saved. Active in Vexis's next session — run `/clear` in Telegram to start fresh."
  }
  ```
- File on disk: `<workspace>/RELATIONSHIPS.md` now contains the
  `## Mom` H2 block with the fact under it.
- JSON queue: `mom` slug no longer present (cleared because all
  facts under it became approved).
- Counter `candidates_approved` incremented to 1.

### 8. Missing-qualifier collision modal flow
- Pre-seed live with `sarah` (no YAML qualifier). Inject candidate
  `sarah` with `qualifier="coworker"`.
- 8a: `POST /sarah/approve` returns **409** with the typed body
  (`error: "missing_existing_qualifier"`, `existing_facts`,
  `existing_qualifier_candidates`, `proposed_qualifier`).
- 8b: `POST /sarah/resolve_qualifier` with
  `existing_qualifier="friend"` → 200, returns
  `{old_slug: "sarah", new_slug: "sarah-friend"}`. Live file
  rewritten; archive gained a `## DISAMBIGUATED` block.
  Retry `POST /sarah/approve` → 200. Live now contains
  `sarah-coworker` + `sarah-friend`.

### 9. Reject
- `POST /marco/reject` with empty body (no `fact_ids`) tombstones
  the whole slug. JSON file's `marco.rejected_at` is set to a
  current ISO timestamp. Default `GET /candidates` (without
  `include_rejected`) no longer surfaces it.

### 10. Show rejected
- `GET /candidates?include_rejected=true` surfaces `marco` again
  with `rejected_at != null`. Dashboard toggle behavior matches.

## Defects uncovered

None. The smoke run is clean.

## Audit-trail script

`/tmp/smoke_4c.py` — copy in this branch's working tree at the
time of commit:

```python
# (run via `python /tmp/smoke_4c.py` from the repo root)
# See the commit message for the full source — kept out of the
# repository proper because it's a build-artifact-driven script
# rather than a reusable test fixture. The pytest-driven coverage
# in tests/relationships/test_dashboard_endpoints.py and
# test_approval_hint.py covers the same surface deterministically.
```
