"""castellan-ops MCP server.

A small, themed MCP server for the Castellan Defense Solutions demo. It exposes
four tools across three risk tiers so that agentgateway tool-level policy has
something meaningful to filter:

  read        list_contracts, get_clearance_status
  write       approve_access_request
  destructive revoke_credentials

The server itself performs NO authorization. That is the point of the demo:
authorization happens at the agentgateway waypoint in front of it, keyed on the
calling agent's SPIFFE identity and the user's claims in the nested STS token.
The server only echoes back what it was given so the audience can see which
identity reached it.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("castellan-ops")

PORT = int(os.environ.get("PORT", "8000"))

mcp = FastMCP(
    "castellan-ops",
    instructions=(
        "Operations tools for Castellan Defense Solutions. Tools are tiered by "
        "risk: read, write, destructive. Use the least powerful tool that answers "
        "the question."
    ),
    host="0.0.0.0",
    port=PORT,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)

# --- fake data -------------------------------------------------------------

CONTRACTS = [
    {"id": "CDS-2041", "agency": "Dept. of Energy", "program": "Grid Resilience", "status": "active", "value_usd_m": 48.2},
    {"id": "CDS-2077", "agency": "US Coast Guard", "program": "Maritime Domain Awareness", "status": "active", "value_usd_m": 112.0},
    {"id": "CDS-2103", "agency": "GSA", "program": "Zero Trust Modernization", "status": "proposal", "value_usd_m": 9.7},
    {"id": "CDS-1988", "agency": "NOAA", "program": "Sensor Data Platform", "status": "closed", "value_usd_m": 31.5},
]

CLEARANCES = {
    "e10412": {"employee": "Dana Whitfield", "level": "Secret", "status": "active", "expires": "2027-03-31"},
    "e20877": {"employee": "Marcus Oyelaran", "level": "Top Secret", "status": "in-reinvestigation", "expires": "2026-11-15"},
    "e30591": {"employee": "Priya Raman", "level": "Secret", "status": "active", "expires": "2028-08-01"},
}

ACCESS_REQUESTS = {
    "AR-5510": {"requester": "e30591", "system": "Grid Resilience data lake", "status": "pending"},
    "AR-5518": {"requester": "e10412", "system": "MDA analytics cluster", "status": "pending"},
}

AUDIT: list[dict] = []


def _audit(tool: str, tier: str, detail: dict) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "tool": tool, "tier": tier, **detail}
    AUDIT.append(entry)
    log.info("AUDIT %s", json.dumps(entry))


# --- read tier -------------------------------------------------------------


@mcp.tool()
def list_contracts(status: str | None = None) -> list[dict]:
    """List Castellan government contracts. Optionally filter by status
    (active, proposal, closed). Read-only."""
    _audit("list_contracts", "read", {"status": status})
    if status:
        return [c for c in CONTRACTS if c["status"] == status]
    return CONTRACTS


@mcp.tool()
def get_clearance_status(employee_id: str) -> dict:
    """Look up the security clearance record for an employee id such as e10412.
    Read-only."""
    _audit("get_clearance_status", "read", {"employee_id": employee_id})
    rec = CLEARANCES.get(employee_id)
    if not rec:
        return {"error": f"no clearance record for {employee_id}"}
    return {"employee_id": employee_id, **rec}


# --- write tier ------------------------------------------------------------


@mcp.tool()
def approve_access_request(request_id: str, justification: str) -> dict:
    """Approve a pending system access request such as AR-5510. This is a
    reversible write: it changes the request status to approved."""
    _audit("approve_access_request", "write", {"request_id": request_id, "justification": justification})
    req = ACCESS_REQUESTS.get(request_id)
    if not req:
        return {"error": f"unknown access request {request_id}"}
    if req["status"] != "pending":
        return {"error": f"{request_id} is already {req['status']}"}
    req["status"] = "approved"
    return {"request_id": request_id, "status": "approved", "system": req["system"]}


# --- destructive tier --------------------------------------------------------


@mcp.tool()
def revoke_credentials(employee_id: str, reason: str) -> dict:
    """Immediately revoke all credentials and system access for an employee.
    DESTRUCTIVE and not reversible from this tool."""
    _audit("revoke_credentials", "destructive", {"employee_id": employee_id, "reason": reason})
    rec = CLEARANCES.get(employee_id)
    if not rec:
        return {"error": f"no record for {employee_id}"}
    rec["status"] = "revoked"
    return {"employee_id": employee_id, "employee": rec["employee"], "status": "revoked", "reason": reason}


# --- plain HTTP endpoints for the demo ----------------------------------------


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


@mcp.custom_route("/audit", methods=["GET"])
async def audit(_: Request) -> JSONResponse:
    """Show the last 50 tool invocations the server actually received. Useful on
    stage to prove that denied tools never reached the server."""
    return JSONResponse(AUDIT[-50:])


if __name__ == "__main__":
    log.info("castellan-ops MCP server listening on :%d (streamable HTTP at /mcp)", PORT)
    mcp.run(transport="streamable-http")
