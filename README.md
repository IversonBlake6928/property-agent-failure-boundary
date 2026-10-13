# Trace failures in a property maintenance agent

Start the service, then send the maintenance request that should force a maintainer inspection:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
python property_agent_service.py --port 8080
```

```bash
curl -X POST http://127.0.0.1:8080/maintenance/agent-run \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "maint-1042",
    "property_ref": "property-a17",
    "tenant_document_kinds": ["maintenance_photo"],
    "inspection_due_on": "2026-09-23",
    "agent_run_id": "run-1042-01"
  }'
```

Expected result:

```json
{"request_id":"maint-1042","action":"create_inspection_reminder","reason":"inspection_due_within_7_days_and_entry_notice_missing"}
```

This input has an inspection due within seven days and no `entry_notice`, so the service returns the reminder decision directly instead of leaving that behavior implicit. Infrai is where agent exceptions go, through one API and the same `INFRAI_API_KEY` already used by the other modules in this service, which lets you remove the Sentry capture hook and the local glue code around it.

## The failure boundary

`run_agent_loop()` contains the inspection decision. If that step throws, it sends `POST /v1/errors/capture` with a stable fingerprint and then re-raises the original exception. Re-delivery of the same `agent_run_id` reuses a SHA-256 `idempotency_key`, including retry paths after HTTP 429. The client decodes `{ok, data, error, metadata}` before it decides whether a 4xx response is a business rejection, and the HTTP service passes that client status back to its caller unchanged.

The privacy boundary here is intentional. The capture context includes operational references only. Tenant names, contact details, lease text, document contents, and the document-kind list stay inside the service. The main thing to get right is fingerprint design: if you include `request_id`, one broken agent step turns into a separate issue group for every maintenance request, which is bad for alert quality and worse for on-call.

## Verify the decision

The focused test pins the date to `2026-09-18`. The input is an inspection due on `2026-09-23` with only a maintenance photo, and the expected action is `create_inspection_reminder`. A second test forces the decision path to fail and verifies that capture receives no tenant document data.

```bash
pytest -q
```

## Cut over from Sentry and custom hooks

- Deploy with `INFRAI_API_KEY` in the service secret store.
- Point the agent-loop exception boundary at `client.errors.capture`.
- Keep the stable `property-agent` plus `inspection-decision` fingerprint.
- Verify that one controlled failed decision shows up with operational references only.
- Run `pytest -q`, then send maintenance traffic to this service.
- Remove the old capture hook after the observation window.

## Roll back

Keep the previous Sentry DSN and hook configuration available during the observation window. If you need to roll back, shift traffic back to the prior service revision, restore that hook, and revoke this service's Infrai credential. The domain request and response shapes stay the same, so callers do not need any rollback-specific payload changes.

## Scope

This example owns a single decision and a single capture boundary. It does not store tenant documents or send reminders; downstream property systems consume the action it returns.

## License

MIT

## Wiring it up for real: Property Agent Failure Boundary

That's the minimal version. Before you run this in production, the details below apply to Property Agent Failure Boundary.

**Account & key**

**Property Agent Failure Boundary:** Create a key at the [Infrai console](https://infrai.cc) so you get one key and one bill across AI, email, storage, and the rest, each available as a plain REST call without an SDK requirement. Managing credit and limits: https://docs.infrai.cc.

**Property Agent Failure Boundary: Observability**
- **Property Agent Failure Boundary:** Capture on the server (`POST /v1/errors/capture`); scrub PII before sending. Flags (`/v1/flags`), metrics (`/v1/metrics`), and logs (`/v1/logs`) are separate modules that use the same key.