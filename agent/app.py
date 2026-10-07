"""Castellan assistant: a chat agent that runs inside the agentic mesh.

What this process does on every chat turn, in order:

1. Reads the human user's Entra ID JWT that the agentgateway ingress attached to
   the request after running the OIDC login.
2. Exchanges (RFC 8693) that JWT plus its own Kubernetes service account token at
   the agentgateway STS for a *nested* token: sub = the user, act = this agent.
3. Calls the LLM through the agentgateway waypoint (budgets, guardrails,
   observability live there, not here).
4. Lists and calls MCP tools through the same waypoint, presenting the nested
   token. The waypoint decides which tools this agent, acting for this user,
   may see and call.

Everything security-relevant is enforced outside this process. The app only
carries tokens and renders what came back, so the audience can see the
enforcement points.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any

import httpx
import jwt
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from openai import AsyncOpenAI, APIStatusError
from pydantic import BaseModel

from agentsts.core import ActorTokenService
from agentsts.core.client import STSClient, STSConfig, TokenType

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("castellan-agent")

# --- configuration (all injected by the Deployment) ------------------------------

AGENT_NAME = os.environ.get("AGENT_NAME", "castellan-assistant")
TRUST_DOMAIN = os.environ.get("TRUST_DOMAIN", "cluster.local")

# Header the ingress uses to hand us the user's Entra token. Default: standard Authorization bearer.
USER_TOKEN_HEADER = os.environ.get("USER_TOKEN_HEADER", "authorization").lower()

STS_WELL_KNOWN_URL = os.environ.get(
    "STS_WELL_KNOWN_URL",
    "http://enterprise-agentgateway.agentgateway-system.svc.cluster.local:7777/.well-known/openid-configuration",
)
STS_AUDIENCE = os.environ.get("STS_AUDIENCE", "")  # optional 'audience' param on the exchange
ACTOR_TOKEN_PATH = os.environ.get("ACTOR_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://llm.castellan.internal/openai/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")

MCP_URL = os.environ.get("MCP_URL", "http://castellan-ops.castellan-tools.svc.cluster.local/mcp")  # "" disables MCP
# Optional downstream specialist agent for ONE task. When set, this agent exposes a delegation tool for
# that task and calls the specialist over the mesh, presenting ITS OWN delegated token as the user token.
# The specialist exchanges that again, so the final token carries the whole chain (act.act).
DELEGATE_URL = os.environ.get("DELEGATE_URL", "")
DELEGATE_NAME = os.environ.get("DELEGATE_NAME", "castellan-clearance")
DELEGATE_TASK = os.environ.get("DELEGATE_TASK", "security clearance lookups")
DELEGATE_TOOL = os.environ.get("DELEGATE_TOOL", "ask_clearance_specialist")
# Where the "sign out" link goes. Ends the IdP session first, then the ingress session (/logout).
LOGOUT_URL = os.environ.get("LOGOUT_URL", "/logout")

DELEGATE_RULES = f"""
- For {DELEGATE_TASK}, always call {DELEGATE_TOOL} with the user's request verbatim and relay its answer
  verbatim, including any refusal or error it reports. Never use your own tools for that task.""" if DELEGATE_URL else ""

SYSTEM_PROMPT = f"""You are {AGENT_NAME}, an operations assistant for Castellan Defense Solutions.
You act on behalf of the logged-in user.{DELEGATE_RULES}
Rules:
- When asked who you are or what your identity is, call get_my_identity and explain the result plainly, in this order:
  the human you act for (sub, name), the actor on record (act.sub, quote it verbatim), who issued the token (iss),
  and your workload's SPIFFE identity. One short line each.
- When asked what tools you have, call list_my_tools and list exactly those. Do not invent tools.
- Use tools for facts about contracts, clearances, access requests. Never fabricate data.
- If a tool call or the model returns an error, say so clearly and quote the reason.
- Never ask the user to confirm an action. The user's request is the authorization. If a tool is
  available to you, call it directly and report the result.
Keep answers short."""

app = FastAPI(title=AGENT_NAME)
HERE = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

actor_service = ActorTokenService(token_path=ACTOR_TOKEN_PATH)
_exchange_cache: dict[str, tuple[str, float]] = {}  # user token -> (nested token, exp epoch)


# --- helpers ----------------------------------------------------------------------


def decode_noverify(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, options={"verify_signature": False, "verify_exp": False, "verify_aud": False})
    except Exception as e:  # noqa: BLE001
        return {"error": f"not a JWT: {e}"}


def jwt_header(token: str) -> dict[str, Any]:
    try:
        return jwt.get_unverified_header(token)
    except Exception:  # noqa: BLE001
        return {}


def spiffe_identity() -> dict[str, Any]:
    """Derive this workload's SPIFFE ID from its projected service account token.

    In ambient mode the mTLS certificate lives in ztunnel, not in the pod, so
    the pod cannot read it. The service account namespace and name are what
    Istio's CA encodes into the SPIFFE URI, so the derivation is exact.
    """
    tok = actor_service.get_actor_token()
    if not tok:
        return {"error": "no service account token mounted"}
    c = decode_noverify(tok)
    k8s = c.get("kubernetes.io", {})
    ns = k8s.get("namespace") or os.environ.get("POD_NAMESPACE", "?")
    sa = (k8s.get("serviceaccount") or {}).get("name") or os.environ.get("SERVICE_ACCOUNT", "?")
    return {
        "spiffe_id": f"spiffe://{TRUST_DOMAIN}/ns/{ns}/sa/{sa}",
        "namespace": ns,
        "service_account": sa,
        "pod": (k8s.get("pod") or {}).get("name"),
        "actor_token_sub": c.get("sub"),
        "actor_token_iss": c.get("iss"),
        "actor_token_aud": c.get("aud"),
    }


def user_token_from_request(request: Request) -> str | None:
    v = request.headers.get(USER_TOKEN_HEADER)
    if v and v.lower().startswith("bearer "):
        v = v[7:]
    if not v:
        v = request.cookies.get("user_token")
    return v or None


async def exchange_for_nested_token(user_token: str) -> tuple[str, dict[str, Any]]:
    """RFC 8693 delegation at the agentgateway STS. Returns (token, meta)."""
    now = time.time()
    cached = _exchange_cache.get(user_token)
    if cached and cached[1] - 30 > now:
        return cached[0], {"cached": True}

    actor = actor_service.get_actor_token()
    if not actor:
        raise RuntimeError("agent has no service account token to present as actor_token")

    kwargs: dict[str, Any] = {}
    if STS_AUDIENCE:
        kwargs["audience"] = STS_AUDIENCE
    async with STSClient(STSConfig(well_known_uri=STS_WELL_KNOWN_URL, use_issuer_host=True)) as sts:
        resp = await sts.delegate(
            subject_token=user_token,
            subject_token_type=TokenType.JWT,
            actor_token=actor,
            actor_token_type=TokenType.JWT,
            **kwargs,
        )
    nested = resp.access_token
    claims = decode_noverify(nested)
    exp = float(claims.get("exp", now + 300))
    _exchange_cache[user_token] = (nested, exp)
    return nested, {"cached": False, "expires_in": resp.expires_in}


def _mcp_headers(nested: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {nested}"}


async def mcp_list_tools(nested: str) -> list[dict[str, Any]]:
    async with streamablehttp_client(MCP_URL, headers=_mcp_headers(nested)) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.list_tools()
            return [{"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema} for t in res.tools]


async def mcp_call_tool(nested: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        async with streamablehttp_client(MCP_URL, headers=_mcp_headers(nested)) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool(name, args)
                text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
                return {"ok": not res.isError, "result": text}
    except BaseException as e:  # noqa: BLE001
        # The MCP client wraps failures in an ExceptionGroup; surface the gateway's actual refusal,
        # e.g. HTTP 400 {"error":{"code":-32602,"message":"Unknown tool: revoke_credentials"}}.
        leaf: BaseException = e
        while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
            leaf = leaf.exceptions[0]
        detail = str(leaf)
        resp = getattr(leaf, "response", None)
        if resp is not None:
            try:
                body = resp.text
                detail = f"HTTP {resp.status_code}: {body[:300]}"
            except Exception:  # noqa: BLE001
                pass
        return {"ok": False, "error": detail}


async def mcp_raw_call(nested: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """tools/call as two plain JSON-RPC POSTs (initialize, then call) so the gateway's exact answer is
    visible, body included. Used by the forced-call demo path."""
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": f"Bearer {nested}"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": AGENT_NAME, "version": "0"}}}
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args}}

    def body_text(r: httpx.Response) -> str:
        txt = r.text.strip()
        for line in txt.splitlines():
            if line.startswith("data: "):
                return line[6:]
        return txt[:400]

    async with httpx.AsyncClient(timeout=20) as c:
        r1 = await c.post(MCP_URL, headers=h, json=init)
        if r1.status_code != 200:
            return {"ok": False, "status": r1.status_code, "error": f"initialize refused: HTTP {r1.status_code} {body_text(r1)}"}
        sid = r1.headers.get("mcp-session-id")
        if sid:
            h["mcp-session-id"] = sid
        r2 = await c.post(MCP_URL, headers=h, json=call)
        body = body_text(r2)
        try:
            j = json.loads(body)
        except json.JSONDecodeError:
            j = {}
        if r2.status_code == 200 and "result" in j and not j["result"].get("isError"):
            text = "\n".join(cc.get("text", "") for cc in j["result"].get("content", []))
            return {"ok": True, "status": 200, "result": text}
        err = (j.get("error") or {}).get("message") or body or f"HTTP {r2.status_code}"
        return {"ok": False, "status": r2.status_code, "error": f"HTTP {r2.status_code}: {err}"}


async def downstream(method: str, path: str, nested: str, json_body: dict[str, Any] | None = None, timeout: float = 120) -> dict[str, Any]:
    """Call the downstream agent with THIS agent's delegated token as the user token it should act for."""
    h = {USER_TOKEN_HEADER: nested}
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.request(method, f"{DELEGATE_URL}{path}", headers=h, json=json_body)
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            j = {"error": r.text[:300]}
        j["_status"] = r.status_code
        return j


def _llm_client() -> AsyncOpenAI:
    # The API key is injected by the waypoint's AgentgatewayBackend; the agent never holds it.
    return AsyncOpenAI(base_url=LLM_BASE_URL, api_key=os.environ.get("LLM_API_KEY", "not-needed-injected-by-waypoint"), timeout=60)


# --- API -------------------------------------------------------------------------


class ChatIn(BaseModel):
    message: str
    history: list[dict[str, Any]] = []


@app.get("/")
async def index() -> FileResponse:
    # no-store: after a sign-out the next load must hit the server, not the browser cache
    return FileResponse(os.path.join(HERE, "static", "index.html"), headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
async def favicon() -> JSONResponse:
    return JSONResponse(None, status_code=204)


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/identity")
async def identity(request: Request) -> JSONResponse:
    """Everything the audience needs to see about who this agent is right now."""
    user_token = user_token_from_request(request)
    out: dict[str, Any] = {
        "agent": AGENT_NAME,
        "logout_url": LOGOUT_URL,
        "workload_identity": spiffe_identity(),
        "user_token_header": USER_TOKEN_HEADER,
        "user": None,
        "nested_token": None,
        "error": None,
    }
    if not user_token:
        out["error"] = f"no user token found in header '{USER_TOKEN_HEADER}'"
        return JSONResponse(out, status_code=401)
    uc = decode_noverify(user_token)
    out["user"] = {
        "claims": uc,
        "header": jwt_header(user_token),
    }
    try:
        nested, meta = await exchange_for_nested_token(user_token)
        out["nested_token"] = {
            "raw": nested,
            "header": jwt_header(nested),
            "claims": decode_noverify(nested),
            "exchange": meta,
            "sts": STS_WELL_KNOWN_URL,
        }
    except Exception as e:  # noqa: BLE001
        out["error"] = f"token exchange failed: {e}"
        return JSONResponse(out, status_code=502)
    if DELEGATE_URL:
        try:
            d = await downstream("GET", "/api/identity", nested, timeout=20)
            out["downstream"] = {"agent": d.get("agent"), "workload_identity": d.get("workload_identity"),
                                 "nested_token": d.get("nested_token"), "error": d.get("error"), "status": d.get("_status")}
        except Exception as e:  # noqa: BLE001
            out["downstream"] = {"agent": DELEGATE_NAME, "error": f"{type(e).__name__}: {e}"}
    return JSONResponse(out)


@app.get("/api/tools")
async def tools(request: Request) -> JSONResponse:
    user_token = user_token_from_request(request)
    if not user_token:
        return JSONResponse({"error": "no user token"}, status_code=401)
    try:
        nested, _ = await exchange_for_nested_token(user_token)
        if not MCP_URL and DELEGATE_URL:
            d = await downstream("GET", "/api/tools", nested, timeout=30)
            d["held_by"] = DELEGATE_NAME
            return JSONResponse(d, status_code=200 if "tools" in d else 502)
        if not MCP_URL:
            return JSONResponse({"mcp_url": None, "tools": []})
        return JSONResponse({"mcp_url": MCP_URL, "tools": await mcp_list_tools(nested)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)


class ForceIn(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


@app.post("/api/force-tool")
async def force_tool(request: Request, body: ForceIn) -> JSONResponse:
    """Call an MCP tool directly with the delegated token, bypassing the model entirely.

    Exists to prove the deny happens at the waypoint, not in the model's manners: the tool may
    not even be in this agent's tools/list, and the gateway still answers for it."""
    steps: list[dict[str, Any]] = []
    t0 = time.time()
    user_token = user_token_from_request(request)
    if not user_token:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    try:
        nested, meta = await exchange_for_nested_token(user_token)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"token exchange failed: {e}"}, status_code=502)
    nc = decode_noverify(nested)
    steps.append({"t": 0.0, "kind": "token_exchange", "sub": nc.get("sub"), "act": (nc.get("act") or {}).get("sub"), **meta})
    if not MCP_URL and DELEGATE_URL:
        d = await downstream("POST", "/api/force-tool", nested, {"name": body.name, "arguments": body.arguments}, timeout=60)
        steps.append({"t": round(time.time() - t0, 3), "kind": "delegate", "agent": DELEGATE_NAME, "what": f"force tools/call {body.name}",
                      "steps": d.get("steps", []), "ok": (d.get("result") or {}).get("ok"), "forced": True})
        return JSONResponse({"result": d.get("result") or {"ok": False, "error": d.get("error")}, "steps": steps})
    result = await mcp_raw_call(nested, body.name, body.arguments)
    steps.append({"t": round(time.time() - t0, 3), "kind": "mcp_tool_call", "name": body.name, "args": body.arguments,
                  "ok": result.get("ok"), "error": result.get("error"), "forced": True})
    return JSONResponse({"result": result, "steps": steps})


@app.get("/api/audit")
async def audit(request: Request) -> JSONResponse:
    """What the MCP server actually executed (its own log, fetched over the mesh). Denied calls never appear."""
    if not MCP_URL and DELEGATE_URL:
        user_token = user_token_from_request(request)
        if not user_token:
            return JSONResponse({"error": "not logged in"}, status_code=401)
        try:
            nested, _ = await exchange_for_nested_token(user_token)
            return JSONResponse(await downstream("GET", "/api/audit", nested, timeout=20))
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)
    if not MCP_URL:
        return JSONResponse({"audit": []})
    base = MCP_URL.rsplit("/", 1)[0]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base}/audit")
            return JSONResponse({"audit": r.json()})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=502)


@app.post("/api/chat")
async def chat(request: Request, body: ChatIn) -> JSONResponse:
    steps: list[dict[str, Any]] = []
    t0 = time.time()

    def step(kind: str, **kw: Any) -> None:
        steps.append({"t": round(time.time() - t0, 3), "kind": kind, **kw})

    user_token = user_token_from_request(request)
    if not user_token:
        return JSONResponse({"error": "not logged in", "steps": steps}, status_code=401)
    uc = decode_noverify(user_token)
    step("user", sub=uc.get("sub"), name=uc.get("name") or uc.get("preferred_username"), roles=uc.get("roles"))

    try:
        nested, meta = await exchange_for_nested_token(user_token)
        nc = decode_noverify(nested)
        step("token_exchange", sts=STS_WELL_KNOWN_URL, sub=nc.get("sub"), act=nc.get("act"), iss=nc.get("iss"), **meta)
    except Exception as e:  # noqa: BLE001
        step("token_exchange_error", error=str(e))
        return JSONResponse({"error": f"token exchange failed: {e}", "steps": steps}, status_code=502)

    # Tool inventory (this agent, this user).
    mcp_tools: list[dict[str, Any]] = []
    if MCP_URL:
        try:
            mcp_tools = await mcp_list_tools(nested)
            step("mcp_tools_list", url=MCP_URL, tools=[t["name"] for t in mcp_tools])
        except Exception as e:  # noqa: BLE001
            step("mcp_tools_list_error", url=MCP_URL, error=f"{type(e).__name__}: {e}")

    local_tools = [
        {
            "type": "function",
            "function": {
                "name": "get_my_identity",
                "description": "Return this agent's workload identity and the delegated (nested) token it is acting with.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_my_tools",
                "description": "Return the MCP tools currently available to this agent.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    if DELEGATE_URL:
        local_tools.append({
            "type": "function",
            "function": {
                "name": DELEGATE_TOOL,
                "description": f"Delegate {DELEGATE_TASK} to the {DELEGATE_NAME} agent, which acts for the same user. Returns its answer.",
                "parameters": {"type": "object", "properties": {"request": {"type": "string", "description": "The user's request, verbatim."}}, "required": ["request"]},
            },
        })
    openai_tools = local_tools + [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["inputSchema"]}}
        for t in mcp_tools
    ]

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += body.history[-20:]
    messages.append({"role": "user", "content": body.message})

    client = _llm_client()
    final_text = ""
    for _ in range(6):
        try:
            resp = await client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=openai_tools,
                tool_choice="auto",
                temperature=0,
                extra_headers={"x-agent-user": str(uc.get("sub", ""))},
            )
        except APIStatusError as e:
            detail = ""
            try:
                detail = json.dumps(e.response.json())
            except Exception:  # noqa: BLE001
                detail = (e.response.text or "")[:500]
            step("llm_error", base_url=LLM_BASE_URL, status=e.status_code, detail=detail)
            friendly = f"The LLM call was refused (HTTP {e.status_code})."
            if e.status_code == 429:
                friendly = "The LLM call was refused: HTTP 429, rate limit exceeded."
            elif e.status_code == 403:
                friendly = "The LLM call was refused: HTTP 403."
            return JSONResponse({"reply": f"{friendly}\n\n{detail}", "steps": steps, "blocked": True})
        except Exception as e:  # noqa: BLE001
            step("llm_error", base_url=LLM_BASE_URL, error=f"{type(e).__name__}: {e}")
            return JSONResponse({"reply": f"LLM call failed: {e}", "steps": steps, "blocked": True})

        usage = getattr(resp, "usage", None)
        step(
            "llm",
            base_url=LLM_BASE_URL,
            model=resp.model,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            final_text = msg.content or ""
            messages.append({"role": "assistant", "content": final_text})
            break

        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            }
        )
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "get_my_identity":
                result = {
                    "workload_identity": spiffe_identity(),
                    "nested_token_claims": nc,
                    "nested_token_raw": nested,
                }
                step("local_tool", name=name)
            elif name == "list_my_tools":
                result = {"mcp_url": MCP_URL, "allowed_tools": [t["name"] for t in mcp_tools]}
                if DELEGATE_URL:
                    result["delegated"] = {DELEGATE_TOOL: f"{DELEGATE_TASK} via {DELEGATE_NAME}"}
                step("local_tool", name=name, tools=result["allowed_tools"])
            elif DELEGATE_URL and name == DELEGATE_TOOL:
                d = await downstream("POST", "/api/chat", nested, {"message": args.get("request", ""), "history": []}, timeout=120)
                result = {"agent": DELEGATE_NAME, "reply": d.get("reply") or d.get("error"), "blocked": d.get("blocked", False)}
                step("delegate", agent=DELEGATE_NAME, what=args.get("request", ""), reply=(d.get("reply") or d.get("error") or "")[:200],
                     steps=d.get("steps", []), ok=d.get("_status") == 200 and not d.get("blocked"))
            else:
                result = await mcp_call_tool(nested, name, args)
                step("mcp_tool_call", name=name, args=args, ok=result.get("ok"), error=result.get("error"))
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)[:8000]})

    return JSONResponse(
        {
            "reply": final_text,
            "steps": steps,
            "history": [m for m in messages[1:] if m["role"] in ("user", "assistant") and m.get("content")][-20:],
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
