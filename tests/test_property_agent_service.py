from datetime import date

import pytest

from property_agent_service import (
    MaintenanceAgentRequest,
    decide_inspection_action,
    run_agent_loop,
)


def request_with(*documents: str) -> MaintenanceAgentRequest:
    return MaintenanceAgentRequest(
        request_id="maint-1042",
        property_ref="property-a17",
        tenant_document_kinds=documents,
        inspection_due_on=date(2026, 9, 23),
        agent_run_id="run-1042-01",
    )


def test_missing_entry_notice_creates_inspection_reminder() -> None:
    decision = decide_inspection_action(
        request_with("maintenance_photo"), today=date(2026, 9, 18)
    )

    assert decision.action == "create_inspection_reminder"
    assert decision.reason == "inspection_due_within_7_days_and_entry_notice_missing"


def test_agent_failure_captures_minimised_context_and_reraises() -> None:
    captured: list[dict] = []

    def failed_decision(*args: object, **kwargs: object) -> None:
        raise RuntimeError("classifier output was incomplete")

    with pytest.raises(RuntimeError, match="classifier output was incomplete"):
        run_agent_loop(
            request_with("private_lease_document"),
            today=date(2026, 9, 18),
            decide=failed_decision,
            capture=lambda exception: captured.append(dict(exception)) or {},
        )

    assert captured[0]["fingerprint"] == ["property-agent", "inspection-decision"]
    assert captured[0]["context"] == {
        "request_id": "maint-1042",
        "property_ref": "property-a17",
        "agent_run_id": "run-1042-01",
    }
    assert "private_lease_document" not in str(captured[0])
    assert len(captured[0]["idempotency_key"]) == 64
