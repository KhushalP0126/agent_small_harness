# Controlled session receipts

Add one secret-free JSON receipt for each genuine reviewed session:

- `plain_question`
- `small_edit`
- `multi_file_edit` (must contain one approved and one rejected proposal)
- `planning_review`
- `unavailable_api`

Create a receipt with `make record-live-session SESSION_ARGS='...'`, then run:

```bash
make verify-live-sessions
```

The verifier intentionally fails until all five distinct scenarios exist. Do
not commit placeholder receipts or raw prompts, API keys, absolute paths, or
unreviewed diffs.
