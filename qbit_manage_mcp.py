"""MCP server exposing qbit_manage's Web API (FastAPI, base path /api) as tools.

Full coverage of every JSON-producing endpoint in qbit_manage's
`modules/web_api.py` (develop HEAD), read and write, but exposed as 6
resource-scoped *portmanteau* tools instead of one tool per endpoint. Each
portmanteau tool (e.g. qbit_manage_config, qbit_manage_commands) takes an
`operation` enum plus an `arguments` dict; see AGENTS.md for the rationale (a
tool-per-endpoint server blows the MCP context budget on session start).

The endpoint table is hand-written from qbit_manage's `modules/web_api.py`
handler routes. qbit_manage publishes no vendored OpenAPI spec (its FastAPI
app serves /openapi.json at runtime but the routes are registered dynamically
on a sub-router), so unlike sonarr-mcp there is no vendored spec to diff
against. `_TOOL_REGISTRY` below lists every endpoint; `_GROUPS` buckets them
by resource, and `register_tools` registers one dispatching tool per group
that calls the right function by name. Nothing about the endpoint functions
themselves changes -- grouping is purely a registration-time concern.

Auth: qbit_manage authenticates with the `X-API-Key` header (api_only) or HTTP
Basic (basic). The server sends the API key header if QBIT_MANAGE_API_KEY is
set, otherwise Basic credentials from QBIT_MANAGE_USERNAME/QBIT_MANAGE_PASSWORD;
API key wins if both are set. /health, /version, and /get_base_url are public
but sending auth anyway is harmless. A group tool is marked readOnlyHint=True
only when every operation in it is a GET; mixed groups carry no hints (writes
still get a `WRITE:` / `DESTRUCTIVE:` note in their operation's doc line).
Bodies are passed as opaque dicts/lists. build_client points at the origin
with no path suffix so httpx joins the fully-qualified /api/... paths.
"""

import inspect
import os
import sys
from typing import Any, Literal
from urllib.parse import quote

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

READONLY = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

# qbit_manage responses are JSON, except /api/docs which returns markdown text.
# `dict[str, Any]` (not bare `Any`) matters here: FastMCP needs a concrete
# schema to build MCP structured content, and skips that step entirely for an
# `Any` return type -- which silently makes Client.call_tool's `.data` come
# back None for any tool returning a JSON array. Verified against fastmcp 3.x.
JSONObj = dict[str, Any]
JSONVal = JSONObj | list[Any] | str

mcp = FastMCP("qbit-manage-mcp")

_client: httpx.AsyncClient | None = None


def build_client(
    base_url: str,
    api_key: str | None = None,
    username: str | None = None,
    password: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.AsyncClient:
    """Client pinned to the origin (no path suffix) so registered full paths
    like /api/run-command resolve correctly via httpx base join. API key wins
    over Basic credentials; neither is sent if unset."""
    headers = {"X-API-Key": api_key} if api_key else {}
    auth = httpx.BasicAuth(username, password) if (username and password and not api_key) else None
    return httpx.AsyncClient(base_url=f"{base_url.rstrip('/')}", headers=headers, auth=auth, transport=transport)


async def _req(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: JSONVal | None = None,
) -> JSONVal:
    assert _client is not None, "client not configured"
    r = await _client.request(method, path, params=params, json=json_body)
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.json().get("detail", r.text))
        except ValueError:
            msg = r.text
        raise ToolError(f"qbit_manage API {r.status_code}: {msg}")
    if not r.text:
        return {}
    try:
        return r.json()
    except ValueError:
        return r.text


def _omit(params: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose values are empty/None/empty-collection so the API's
    defaults apply."""
    return {k: v for k, v in params.items() if v not in ("", None) and not (isinstance(v, (list, dict)) and not v)}


# Resource groups for portmanteau registration. Every _TOOL_REGISTRY name must
# appear in exactly one group - see test_all_registry_names_grouped.
_GROUPS: dict[str, tuple[str, ...]] = {
    "qbit_manage_config": (
        'qbit_manage_backup_config',
        'qbit_manage_create_config',
        'qbit_manage_delete_config',
        'qbit_manage_get_config',
        'qbit_manage_list_config_backups',
        'qbit_manage_list_configs',
        'qbit_manage_restore_config_from_backup',
        'qbit_manage_update_config',
        'qbit_manage_validate_config',
    ),
    "qbit_manage_commands": (
        'qbit_manage_run_command',
    ),
    "qbit_manage_scheduler": (
        'qbit_manage_get_scheduler_status',
        'qbit_manage_toggle_schedule_persistence',
        'qbit_manage_update_schedule',
    ),
    "qbit_manage_logs": (
        'qbit_manage_get_documentation',
        'qbit_manage_get_logs',
        'qbit_manage_list_log_files',
    ),
    "qbit_manage_system": (
        'qbit_manage_force_reset_running_state',
        'qbit_manage_get_base_url',
        'qbit_manage_get_version',
        'qbit_manage_health_check',
    ),
    "qbit_manage_security": (
        'qbit_manage_get_security_settings',
        'qbit_manage_get_security_status',
        'qbit_manage_update_security_settings',
    ),
}


_TOOL_REGISTRY: list[dict[str, Any]] = [
 {'name': 'qbit_manage_run_command',
  'method': 'POST',
  'path': '/api/run-command',
  'pp': [],
  'qp': [],
  'bk': 'dict',
  'doc': 'Run qbit_manage commands against a config (body is a CommandRequest: config_file, commands list, hashes, dry_run, skip_cleanup, skip_qb_version_check, log_level). Valid commands: cat_update, tag_update, recheck, rem_unregistered, tag_tracker_error, rem_orphaned, tag_nohardlinks, share_limits. WRITE: this runs real qbit_manage operations against your qBittorrent instance.',
 },
 {'name': 'qbit_manage_list_configs',
  'method': 'GET',
  'path': '/api/configs',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'List available config files in the config directory (sensitive files like qbm_settings.yml are filtered out).',
 },
 {'name': 'qbit_manage_get_config',
  'method': 'GET',
  'path': '/api/configs/{filename}',
  'pp': [{'name': 'filename', 'wire': 'filename', 'type': 'str'}],
  'qp': [],
  'bk': 'none',
  'doc': 'Fetch a parsed config file. data mirrors the YAML structure; !ENV references are preserved as "!ENV <VAR>".',
 },
 {'name': 'qbit_manage_create_config',
  'method': 'POST',
  'path': '/api/configs/{filename}',
  'pp': [{'name': 'filename', 'wire': 'filename', 'type': 'str'}],
  'qp': [],
  'bk': 'dict',
  'doc': 'Create a new config file (body is {"data": <config dict>}). Returns 409 if the file already exists. WRITE: this modifies your qbit_manage instance.',
 },
 {'name': 'qbit_manage_update_config',
  'method': 'PUT',
  'path': '/api/configs/{filename}',
  'pp': [{'name': 'filename', 'wire': 'filename', 'type': 'str'}],
  'qp': [],
  'bk': 'dict',
  'doc': 'Update an existing config file (body is {"data": <full config dict>}); a timestamped backup is auto-created before writing. WRITE: this modifies your qbit_manage instance.',
 },
 {'name': 'qbit_manage_delete_config',
  'method': 'DELETE',
  'path': '/api/configs/{filename}',
  'pp': [{'name': 'filename', 'wire': 'filename', 'type': 'str'}],
  'qp': [],
  'bk': 'none',
  'doc': 'Delete a config file permanently (a backup is created before deletion). DESTRUCTIVE: this deletes data.',
 },
 {'name': 'qbit_manage_validate_config',
  'method': 'POST',
  'path': '/api/configs/{filename}/validate',
  'pp': [{'name': 'filename', 'wire': 'filename', 'type': 'str'}],
  'qp': [],
  'bk': 'dict',
  'doc': 'Validate a config through the parser without executing (body is {"data": <config dict>}); defaults backfilled during validation are written back to the real config.',
 },
 {'name': 'qbit_manage_backup_config',
  'method': 'POST',
  'path': '/api/configs/{filename}/backup',
  'pp': [{'name': 'filename', 'wire': 'filename', 'type': 'str'}],
  'qp': [],
  'bk': 'none',
  'doc': 'Create a timestamped manual backup of a config file (keeps up to 30 per config). WRITE: this modifies your qbit_manage instance.',
 },
 {'name': 'qbit_manage_list_config_backups',
  'method': 'GET',
  'path': '/api/configs/{filename}/backups',
  'pp': [{'name': 'filename', 'wire': 'filename', 'type': 'str'}],
  'qp': [],
  'bk': 'none',
  'doc': 'List available backups for a config file, newest first.',
 },
 {'name': 'qbit_manage_restore_config_from_backup',
  'method': 'POST',
  'path': '/api/configs/{filename}/restore',
  'pp': [{'name': 'filename', 'wire': 'filename', 'type': 'str'}],
  'qp': [],
  'bk': 'none',
  'doc': 'Load a backup by name (filename is the backup file name in the path, e.g. config_20260521_100000.yml) and return its data for restoring.',
 },
 {'name': 'qbit_manage_get_scheduler_status',
  'method': 'GET',
  'path': '/api/scheduler',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'Return scheduler status: current schedule (type interval|cron + value), next run time, is_running, source, persistent/file_exists/disabled state.',
 },
 {'name': 'qbit_manage_update_schedule',
  'method': 'PUT',
  'path': '/api/schedule',
  'pp': [],
  'qp': [],
  'bk': 'dict',
  'doc': 'Set the schedule (body {"schedule": "1440" or "0 4 * * *", "type": "interval" or "cron" optional}). Takes effect immediately; type auto-detected if omitted. WRITE: this modifies your qbit_manage instance.',
 },
 {'name': 'qbit_manage_toggle_schedule_persistence',
  'method': 'POST',
  'path': '/api/schedule/persistence/toggle',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'Toggle whether the schedule persists across restarts. WRITE: this modifies your qbit_manage instance.',
 },
 {'name': 'qbit_manage_get_logs',
  'method': 'GET',
  'path': '/api/logs',
  'pp': [],
  'qp': [
          {'name': 'limit', 'wire': 'limit', 'type': 'int', 'default': 'None'},
          {'name': 'log_filename', 'wire': 'log_filename', 'type': 'str', 'default': 'None'},
         ],
  'bk': 'none',
  'doc': 'Fetch recent log lines in chronological order. limit caps the tail; log_filename selects the active (<stem>.log) or rotated (<stem>.<N>.log) file (default qbit_manage.log).',
 },
 {'name': 'qbit_manage_list_log_files',
  'method': 'GET',
  'path': '/api/log_files',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'List available log files (active and rotated).',
 },
 {'name': 'qbit_manage_get_documentation',
  'method': 'GET',
  'path': '/api/docs',
  'pp': [],
  'qp': [
          {'name': 'file', 'wire': 'file', 'type': 'str', 'required': True},
         ],
  'bk': 'none',
  'doc': 'Fetch a markdown docs file by name (e.g. Home.md, Commands.md, Config-Setup.md). Returns markdown text, not JSON.',
 },
 {'name': 'qbit_manage_get_version',
  'method': 'GET',
  'path': '/api/version',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'Get the current qbit_manage version with update availability details (always public).',
 },
 {'name': 'qbit_manage_health_check',
  'method': 'GET',
  'path': '/api/health',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'Liveness/readiness probe (always public): status healthy|degraded|busy|unhealthy, queue size, config/log directory state, next scheduled run.',
 },
 {'name': 'qbit_manage_get_base_url',
  'method': 'GET',
  'path': '/api/get_base_url',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'Return the configured base URL the web server is served under (always public).',
 },
 {'name': 'qbit_manage_get_security_settings',
  'method': 'GET',
  'path': '/api/security',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'Get security settings (API key and password hash are redacted).',
 },
 {'name': 'qbit_manage_get_security_status',
  'method': 'GET',
  'path': '/api/security/status',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'Get auth summary without sensitive data: has_api_key, method, enabled.',
 },
 {'name': 'qbit_manage_update_security_settings',
  'method': 'PUT',
  'path': '/api/security',
  'pp': [],
  'qp': [],
  'bk': 'dict',
  'doc': 'Update security settings (body is a SecuritySettingsRequest: enabled, method none|basic|api_only, username, password, generate_api_key, clear_api_key, trusted_proxies, bypass_auth_for_local, current_* credentials for reauthentication). WRITE: this modifies your qbit_manage instance.',
 },
 {'name': 'qbit_manage_force_reset_running_state',
  'method': 'POST',
  'path': '/api/system/force-reset',
  'pp': [],
  'qp': [],
  'bk': 'none',
  'doc': 'Force-reset the internal is_running flag to recover from a stuck run state. WRITE: this modifies your qbit_manage instance.',
 },
]


def _tool_source(spec: dict[str, Any]) -> str:
    parts: list[str] = []
    for p in spec["pp"]:
        parts.append(f"{p['name']}: {p['type']}")
    for q in spec["qp"]:
        if q.get("required"):
            parts.append(f"{q['name']}: {q['type']}")
        else:
            parts.append(f"{q['name']}: {q['type']} = {q['default']}")
    if spec["bk"] == "dict":
        parts.append("body: dict[str, Any] = {}")
    elif spec["bk"] == "list":
        parts.append("body: list[Any] = []")
    sig = ", ".join(parts)

    path_tmpl = spec["path"]
    for p in spec["pp"]:
        path_tmpl = path_tmpl.replace("{" + p["wire"] + "}", "{" + p["name"] + "}")
    if spec["pp"]:
        arg_call = ", ".join(f"{p['name']}=quote(str({p['name']}))" for p in spec["pp"])
        url_expr = f'f"{path_tmpl}".format({arg_call})'
    else:
        url_expr = repr(spec["path"])

    if spec["qp"]:
        items = []
        for q in spec["qp"]:
            if q.get("required"):
                items.append(f'"{q["wire"]}": {q["name"]}')
            else:
                items.append(f'"{q["wire"]}": None if {q["name"]} == {q["default"]} else {q["name"]}')
        params_expr = "_omit({" + ", ".join(items) + "})"
    else:
        params_expr = "None"
    json_expr = "body" if spec["bk"] != "none" else "None"

    doc = spec["doc"].replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return (
        f"async def {spec['name']}({sig}) -> JSONVal:\n"
        f'    """{doc}"""\n'
        f"    return await _req({spec['method']!r}, {url_expr}, params={params_expr}, json_body={json_expr})\n"
    )


def _op_line(name: str, fn: Any) -> str:
    """One line of a group tool's description: signature + one-line doc."""
    sig = ", ".join(
        p.name if p.default is inspect.Parameter.empty else f"{p.name}={p.default!r}"
        for p in inspect.signature(fn).parameters.values()
    )
    return f"- {name}({sig}) — {' '.join((fn.__doc__ or '').split())}"


def _register_group(group: str, names: tuple[str, ...], ns: dict[str, Any], method_of: dict[str, str]) -> None:
    """Register one dispatching tool that fans out to every endpoint function
    named in `names`. The endpoint functions themselves are untouched -
    they're just looked up by name instead of each becoming its own tool."""
    fns = {n: ns[n] for n in names}

    async def dispatch(operation: str, arguments: JSONObj | None = None) -> JSONVal:
        fn = fns.get(operation)
        if fn is None:
            raise ToolError(f"Unknown operation {operation!r} for {group}. Valid: {', '.join(fns)}")
        return await fn(**(arguments or {}))

    dispatch.__annotations__["operation"] = Literal[names]
    ann = READONLY if {method_of[n] for n in names} == {"GET"} else None
    mcp.add_tool(
        Tool.from_function(
            dispatch,
            name=group,
            description=(
                f"{group.replace('_', ' ')} operations on qbit_manage. Pass `operation` and an "
                f"`arguments` dict matching that operation's parameters.\n\n"
                + "\n".join(_op_line(n, f) for n, f in fns.items())
            ),
            annotations=ann,
        )
    )


def register_tools() -> None:
    src = "\n".join(_tool_source(spec) for spec in _TOOL_REGISTRY)
    ns: dict[str, Any] = {}
    exec(src, globals(), ns)
    method_of = {spec["name"]: spec["method"] for spec in _TOOL_REGISTRY}
    for group, names in _GROUPS.items():
        _register_group(group, names, ns, method_of)


register_tools()


def main() -> None:
    global _client
    url = os.environ.get("QBIT_MANAGE_URL")
    if not url:
        print("QBIT_MANAGE_URL environment variable is required (e.g. http://qbit-manage.example.com:8181)", file=sys.stderr)
        raise SystemExit(1)
    _client = build_client(
        url,
        os.environ.get("QBIT_MANAGE_API_KEY"),
        os.environ.get("QBIT_MANAGE_USERNAME"),
        os.environ.get("QBIT_MANAGE_PASSWORD"),
    )
    mcp.run()


if __name__ == "__main__":
    main()
