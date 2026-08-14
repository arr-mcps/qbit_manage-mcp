"""Offline tests: one per qbit_manage endpoint, plus error-path and portmanteau
grouping tests.

No network. The endpoint list is `_TOOL_REGISTRY` itself (qbit_manage publishes
no vendored OpenAPI spec, so the registry is the source of truth), and each
tool call is checked against the exact HTTP request it should produce (method,
path incl. path-param substitution, query params) via httpx.MockTransport,
using FastMCP's in-memory Client (see https://gofastmcp.com/development/tests).
"""

import json

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

import qbit_manage_mcp


class Recorder:
    """Captures the single request made during a test and replays a canned response."""

    def __init__(self):
        self.method = None
        self.url = None
        self.headers = None
        self.params = None
        self.json = None
        self.response = httpx.Response(200, json={"success": True})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.method = request.method
        self.url = request.url
        self.headers = request.headers
        self.params = request.url.params
        self.json = json.loads(request.content) if request.content else None
        return self.response


@pytest.fixture
def recorder():
    return Recorder()


@pytest_asyncio.fixture
async def server(recorder, monkeypatch):
    transport = httpx.MockTransport(recorder.handler)
    client = qbit_manage_mcp.build_client("https://qbit-manage.example.com", "test-key", transport=transport)
    monkeypatch.setattr(qbit_manage_mcp, "_client", client)
    yield qbit_manage_mcp.mcp
    await client.aclose()


def op_to_args(spec):
    """Build call args for a tool from its registry entry: path params (and
    required query params) get a sentinel value per their declared type."""
    args = {}
    for p in spec["pp"]:
        args[p["name"]] = "abc" if p["type"] == "str" else 1
    for q in spec["qp"]:
        if q.get("required"):
            args[q["name"]] = "abc" if q["type"] == "str" else 1
    return args


def expected_path(spec):
    path = spec["path"]
    for p in spec["pp"]:
        path = path.replace("{" + p["wire"] + "}", "abc" if p["type"] == "str" else "1")
    return path


_OP_GROUP = {op: group for group, ops in qbit_manage_mcp._GROUPS.items() for op in ops}


async def call(server, tool, **kwargs):
    """Call `tool` (an endpoint operation name) through the portmanteau group
    tool that now hosts it, so every per-endpoint test keeps working via the
    group dispatcher."""
    async with Client(server) as c:
        return await c.call_tool(_OP_GROUP[tool], {"operation": tool, "arguments": kwargs})


# --- one test per endpoint ---------------------------------------------------

@pytest.mark.parametrize(
    "spec",
    qbit_manage_mcp._TOOL_REGISTRY,
    ids=[s["name"] for s in qbit_manage_mcp._TOOL_REGISTRY],
)
async def test_endpoint_mapping(server, recorder, spec):
    await call(server, spec["name"], **op_to_args(spec))
    assert recorder.method == spec["method"]
    assert recorder.url.path == expected_path(spec)


# --- coverage: registry == groups ---------------------------------------------

def test_all_registry_names_grouped():
    """Every registry endpoint must land in exactly one portmanteau group -
    this is the safety net for the group-tool consolidation."""
    registry_names = [s["name"] for s in qbit_manage_mcp._TOOL_REGISTRY]
    grouped_names = [n for names in qbit_manage_mcp._GROUPS.values() for n in names]
    assert sorted(grouped_names) == sorted(registry_names)
    assert len(grouped_names) == len(set(grouped_names))
    assert len(qbit_manage_mcp._GROUPS) <= 15


def test_all_registered_tools_have_unique_names():
    names = [s["name"] for s in qbit_manage_mcp._TOOL_REGISTRY]
    assert len(names) == len(set(names))


async def test_group_tools_are_the_only_registered_tools(server):
    async with Client(server) as c:
        tools = await c.list_tools()
    assert {t.name for t in tools} == set(qbit_manage_mcp._GROUPS)


async def test_readonly_hint_only_on_all_get_groups(server):
    async with Client(server) as c:
        tools = await c.list_tools()
    by_name = {t.name: t for t in tools}
    method_by_name = {s["name"]: s["method"] for s in qbit_manage_mcp._TOOL_REGISTRY}
    for group, names in qbit_manage_mcp._GROUPS.items():
        expected = {method_by_name[n] for n in names} == {"GET"}
        t = by_name[group]
        assert (t.annotations is not None and t.annotations.readOnlyHint) is expected


async def test_unknown_operation_rejected_by_schema(server):
    # The Literal[...] enum on `operation` means an invalid value never
    # reaches _register_group's dispatch body - pydantic rejects it first.
    with pytest.raises(ToolError, match="validation error"):
        async with Client(server) as c:
            await c.call_tool("qbit_manage_config", {"operation": "not_a_real_operation"})


# --- query params --------------------------------------------------------------

async def test_query_params_use_wire_names(server, recorder):
    await call(server, "qbit_manage_get_logs", limit=50, log_filename="qbit_manage.1.log")
    assert recorder.params["limit"] == "50"
    assert recorder.params["log_filename"] == "qbit_manage.1.log"


async def test_defaults_and_empties_are_omitted(server, recorder):
    await call(server, "qbit_manage_get_logs")
    assert "limit" not in recorder.params
    assert "log_filename" not in recorder.params

    await call(server, "qbit_manage_get_logs", limit=0)
    assert recorder.params["limit"] == "0"


async def test_required_query_param_sent(server, recorder):
    await call(server, "qbit_manage_get_documentation", file="Home.md")
    assert recorder.params["file"] == "Home.md"


# --- request bodies ------------------------------------------------------------

async def test_body_sent_as_json(server, recorder):
    body = {"config_file": "config.yml", "commands": ["cat_update", "tag_update"], "dry_run": True}
    await call(server, "qbit_manage_run_command", body=body)
    assert recorder.json == body


async def test_config_body_sent_as_json(server, recorder):
    body = {"data": {"qbt": {"host": "localhost:8080"}, "settings": {}}}
    await call(server, "qbit_manage_update_config", filename="config.yml", body=body)
    assert recorder.json == body


async def test_path_params_substitute(server, recorder):
    await call(server, "qbit_manage_get_config", filename="config.yml")
    assert recorder.url.path == "/api/configs/config.yml"

    await call(server, "qbit_manage_validate_config", filename="config2.yml")
    assert recorder.url.path == "/api/configs/config2.yml/validate"

    await call(server, "qbit_manage_restore_config_from_backup", filename="config_20260521_100000.yml")
    assert recorder.url.path == "/api/configs/config_20260521_100000.yml/restore"


async def test_path_params_are_url_encoded(server, recorder):
    await call(server, "qbit_manage_get_config", filename="my config.yml")
    assert recorder.url.raw_path == b"/api/configs/my%20config.yml"


async def test_get_requests_have_no_body(server, recorder):
    await call(server, "qbit_manage_list_configs")
    assert recorder.json is None


# --- response handling -----------------------------------------------------------

async def test_empty_response_becomes_empty_dict(server, recorder):
    recorder.response = httpx.Response(204, text="")
    result = await call(server, "qbit_manage_force_reset_running_state")
    assert result.data == {}


async def test_text_response_returned_as_string(server, recorder):
    recorder.response = httpx.Response(200, text="# Home\nDocs content")
    result = await call(server, "qbit_manage_get_documentation", file="Home.md")
    assert result.data == "# Home\nDocs content"


# --- auth header -----------------------------------------------------------------

async def test_api_key_sent_as_x_api_key_header(server, recorder):
    await call(server, "qbit_manage_list_configs")
    assert recorder.headers["x-api-key"] == "test-key"


async def test_basic_auth_sent_when_no_api_key(recorder, monkeypatch):
    transport = httpx.MockTransport(recorder.handler)
    client = qbit_manage_mcp.build_client(
        "https://qbit-manage.example.com", None, username="admin", password="secret", transport=transport
    )
    monkeypatch.setattr(qbit_manage_mcp, "_client", client)
    await call(qbit_manage_mcp.mcp, "qbit_manage_list_configs")
    assert recorder.headers["authorization"] == "Basic " + __import__("base64").b64encode(b"admin:secret").decode()
    assert "x-api-key" not in recorder.headers
    await client.aclose()


async def test_api_key_wins_over_basic_auth(recorder, monkeypatch):
    transport = httpx.MockTransport(recorder.handler)
    client = qbit_manage_mcp.build_client(
        "https://qbit-manage.example.com", "test-key", username="admin", password="secret", transport=transport
    )
    monkeypatch.setattr(qbit_manage_mcp, "_client", client)
    await call(qbit_manage_mcp.mcp, "qbit_manage_list_configs")
    assert recorder.headers["x-api-key"] == "test-key"
    assert "authorization" not in recorder.headers
    await client.aclose()


async def test_no_credentials_means_no_auth_header(recorder, monkeypatch):
    transport = httpx.MockTransport(recorder.handler)
    client = qbit_manage_mcp.build_client("https://qbit-manage.example.com", None, transport=transport)
    monkeypatch.setattr(qbit_manage_mcp, "_client", client)
    await call(qbit_manage_mcp.mcp, "qbit_manage_list_configs")
    assert "x-api-key" not in recorder.headers
    assert "authorization" not in recorder.headers
    await client.aclose()


# --- error paths -----------------------------------------------------------------

async def test_404_error_message_reaches_caller(server, recorder):
    recorder.response = httpx.Response(404, json={"detail": "Configuration file 'nope.yml' not found"})
    with pytest.raises(ToolError, match="Configuration file 'nope.yml' not found"):
        await call(server, "qbit_manage_get_config", filename="nope.yml")


async def test_401_error_surfaces_status(server, recorder):
    recorder.response = httpx.Response(401, text="Unauthorized")
    with pytest.raises(ToolError, match="401"):
        await call(server, "qbit_manage_list_configs")


async def test_400_error_surfaces_status(server, recorder):
    recorder.response = httpx.Response(400, json={"detail": "Invalid command: nope"})
    with pytest.raises(ToolError, match="400"):
        await call(server, "qbit_manage_run_command", body={"commands": ["nope"]})


async def test_non_json_error_body_does_not_crash(server, recorder):
    recorder.response = httpx.Response(502, text="<html>Bad Gateway</html>")
    with pytest.raises(ToolError, match="502"):
        await call(server, "qbit_manage_list_configs")


# --- main() ------------------------------------------------------------------

def test_main_requires_qbit_manage_url(monkeypatch):
    monkeypatch.delenv("QBIT_MANAGE_URL", raising=False)
    with pytest.raises(SystemExit):
        qbit_manage_mcp.main()
