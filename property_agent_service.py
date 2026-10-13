"""HTTP service for a privacy-minimised property maintenance agent loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import traceback
from dataclasses import asdict, dataclass
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Sequence

from infrai_errors import InfraiClient, InfraiError


@dataclass(frozen=True)
class MaintenanceAgentRequest:
    request_id: str
    property_ref: str
    tenant_document_kinds: Sequence[str]
    inspection_due_on: date
    agent_run_id: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MaintenanceAgentRequest":
        required = {
            "request_id",
            "property_ref",
            "tenant_document_kinds",
            "inspection_due_on",
            "agent_run_id",
        }
        if set(value) != required:
            raise ValueError(f"request fields must be exactly {sorted(required)}")
        documents = value["tenant_document_kinds"]
        if not isinstance(documents, list) or not all(
            isinstance(item, str) for item in documents
        ):
            raise ValueError("tenant_document_kinds must be a list of strings")
        return cls(
            request_id=str(value["request_id"]),
            property_ref=str(value["property_ref"]),
            tenant_document_kinds=tuple(documents),
            inspection_due_on=date.fromisoformat(str(value["inspection_due_on"])),
            agent_run_id=str(value["agent_run_id"]),
        )


@dataclass(frozen=True)
class AgentDecision:
    request_id: str
    action: str
    reason: str


def decide_inspection_action(
    request: MaintenanceAgentRequest, *, today: date
) -> AgentDecision:
    days_until_inspection = (request.inspection_due_on - today).days
    has_entry_notice = "entry_notice" in request.tenant_document_kinds
    if 0 <= days_until_inspection <= 7 and not has_entry_notice:
        return AgentDecision(
            request.request_id,
            "create_inspection_reminder",
            "inspection_due_within_7_days_and_entry_notice_missing",
        )
    return AgentDecision(
        request.request_id,
        "continue_maintenance_triage",
        "document_and_inspection_checks_clear",
    )


def run_agent_loop(
    request: MaintenanceAgentRequest,
    *,
    today: date,
    decide: Callable = decide_inspection_action,
    capture: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> AgentDecision:
    try:
        return decide(request, today=today)
    except Exception as exc:
        idempotency_key = hashlib.sha256(
            f"{request.agent_run_id}:inspection-decision".encode("utf-8")
        ).hexdigest()
        # Only operational references leave the service; tenant document contents do not.
        capture(
            {
                "title": "property agent inspection decision failed",
                "message": f"{type(exc).__name__}: {exc}",
                "exception": traceback.format_exc(),
                "level": "error",
                "fingerprint": ["property-agent", "inspection-decision"],
                "context": {
                    "request_id": request.request_id,
                    "property_ref": request.property_ref,
                    "agent_run_id": request.agent_run_id,
                },
                "service": "property-maintenance-agent",
                "idempotency_key": idempotency_key,
            }
        )
        raise


class PropertyAgentHandler(BaseHTTPRequestHandler):
    capture: Callable[[Mapping[str, Any]], dict[str, Any]]
    today: Callable[[], date] = date.today

    def do_POST(self) -> None:
        if self.path != "/maintenance/agent-run":
            self._write_json(404, {"error": "route_not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("request body must be a JSON object")
            request = MaintenanceAgentRequest.from_dict(body)
            decision = run_agent_loop(
                request, today=self.today(), capture=self.capture
            )
        except (ValueError, json.JSONDecodeError) as exc:
            self._write_json(400, {"error": str(exc)})
            return
        except InfraiError as exc:
            client_status = exc.status_code if 400 <= exc.status_code < 500 else 502
            self._write_json(client_status, {"error": exc.code})
            return
        except Exception:
            self._write_json(422, {"error": "agent_decision_failed"})
            return
        self._write_json(200, asdict(decision))

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write_json(self, status: int, body: Mapping[str, Any]) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def serve(port: int) -> None:
    infrai = InfraiClient()
    PropertyAgentHandler.capture = infrai.errors.capture
    server = ThreadingHTTPServer(("127.0.0.1", port), PropertyAgentHandler)
    print(f"property agent listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    serve(args.port)
