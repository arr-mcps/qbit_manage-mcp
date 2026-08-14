# AGENTS.md — qbit_manage-mcp

MCP server exposing qbit_manage's Web API (REST, read + write) as tools so an LLM can read and manage a qbit_manage instance: run maintenance commands against its qBittorrent torrents, manage config files, the scheduler, logs, security settings, and system state. Uses FastMCP, `uv` for deps.

Exposed as **6 resource-scoped portmanteau tools**, not one tool per endpoint — see "Portmanteau registration" below. The API is small (23 endpoints), but it is still grouped from the very first commit per the fleet standard: tool-per-endpoint is only tolerated for `tracearr-mcp` (it predates the standard and sits under the ceiling), never for a server built today.

## Testing
- Offline suite: `make test` (or `uv run pytest`)
- Live integration (needs `QBIT_MANAGE_URL`/`QBIT_MANAGE_API_KEY`): `make test-integration`
  - GET endpoints run against the live instance.
  - POST/PUT/DELETE only run when `QBIT_MANAGE_WRITE_TESTS=1` (safe create→validate→delete cycle against a scratch config file, then cleanup). Never point write tests at a production instance you care about.

## Tool registry and the qbit_manage source
- `_TOOL_REGISTRY` in `qbit_manage_mcp.py` lists every JSON-producing qbit_manage endpoint under `/api`. qbit_manage publishes no vendored OpenAPI spec (its FastAPI `/openapi.json` is served at runtime but the routes are registered dynamically on a sub-router), so this table is hand-maintained against `modules/web_api.py` in the qbit_manage repo (currently develop HEAD). Each entry carries name/method/path/path-params/query-params/body-kind/doc.
- Response caveats: `/api/docs` returns `text/markdown` — `_req` falls back to `r.text` for non-JSON 2xx bodies and returns `{}` for empty bodies, and `dispatch` is typed `JSONVal | str` accordingly. Keep that union widened; a too-narrow return type breaks structured-content validation at runtime.
- `get_documentation`'s `file` query param is **required** (no default): its registry entry marks it `required: true` and `_tool_source` renders it without a default. The `op_to_args` test helper supplies sentinel values for required query params the same way it does for path params.
- To refresh coverage after a qbit_manage update, diff `modules/web_api.py`'s `api_router` route registrations and update `_TOOL_REGISTRY` + `_GROUPS` by hand. Do not hand-edit generated per-endpoint functions; `register_tools()` regenerates them from the registry.
- Endpoint function naming (internal, not an MCP tool name): `qbit_manage_<verb>_<resource>` derived from path + method (e.g. `qbit_manage_list_configs`, `qbit_manage_update_config`, `qbit_manage_run_command`, `qbit_manage_health_check`).

## Portmanteau registration — **do not go back to one tool per endpoint**
- `_GROUPS` buckets every `_TOOL_REGISTRY` name into one of 6 resource groups (`qbit_manage_config`, `qbit_manage_commands`, `qbit_manage_scheduler`, `qbit_manage_logs`, `qbit_manage_system`, `qbit_manage_security`). `register_tools()` registers exactly one MCP tool per group via `_register_group`, which wraps the group's endpoint functions in a single `dispatch(operation, arguments)` closure. The endpoint functions themselves are plain callables looked up by name, not separately-registered tools.
- `operation` is typed `Literal[<the group's endpoint names>]`, so FastMCP/pydantic validates it against the real endpoint list before `dispatch` ever runs — an invalid operation never reaches the group tool's body.
- Adding a new endpoint: add its entry to `_TOOL_REGISTRY`, then add its name to exactly one group in `_GROUPS`. `tests/test_tools.py::test_all_registry_names_grouped` fails if you forget.
- New resource area big enough to need its own group (rare): add a new `_GROUPS` key. Keep the total group count at or under ~15 — that ceiling is the entire point of this pattern.
- If you're tempted to add a per-endpoint `@mcp.tool` or an extra `mcp.add_tool` call outside `_register_group`, don't — every endpoint must be reachable only via its group's `operation` enum.

## Annotations convention
- A group tool is `readOnlyHint=True` (`READONLY`) only when *every* operation in it is a GET (`qbit_manage_logs` is the one all-GET group today). Mixed groups carry no hints.
- Per-operation write/destructive notes survive in the group tool's description: each operation line still ends with its original one-line doc, and write/destructive endpoints keep a `WRITE:`/`DESTRUCTIVE:` note in that doc string.

## Auth and base path
- Auth: `X-API-Key` header (generated in the qbit_manage Web UI, Security settings) or HTTP Basic. The server sends the API key header if `QBIT_MANAGE_API_KEY` is set; otherwise Basic credentials from `QBIT_MANAGE_USERNAME`/`QBIT_MANAGE_PASSWORD`. API key wins if both are set. `/health`, `/version`, `/get_base_url` are public, but sending auth anyway is harmless.
- `build_client` points at the origin with no path suffix; every registered tool carries its full `/api/...` path. `_req` raises `ToolError` with the API status and message on `>=400`.
- If the qbit_manage instance is served under a base URL subpath (`--base-url`/`QBT_BASE_URL`), the user includes it in `QBIT_MANAGE_URL`.

## Release workflow
Always use the `make bump-*` targets to bump the version (`uv version --bump patch|minor|major`), which updates `pyproject.toml` and `uv.lock` together. Do NOT edit the version by hand.

- Bump: `make bump-patch` (or `bump-minor` / `bump-major`)
- Commit message is **just the version**, e.g. `0.1.2` — nothing else.
- Tag it `v<version>` (e.g. `v0.1.2`).
- Push main and the tag:
  ```
  git push origin main
  git push origin v<version>
  ```
- Deploy to the Proxmox host (root SSH key): pull the repo then reinstall the uv tool:
  ```
  ssh root@192.168.50.3 -- 'cd /root/qbit_manage-mcp && git fetch origin && git reset --hard origin/main'
  ssh root@192.168.50.3 -- 'cd /root/qbit_manage-mcp && uv tool install --force .'
  ```
  The host runs it via `uv tool install` → `/root/.local/bin/qbit-manage-mcp` (not from the repo). Register it in `/root/.config/opencode/opencode.jsonc` with `QBIT_MANAGE_URL`/`QBIT_MANAGE_API_KEY`.

## Initial state
Version starts at `0.0.0` in the initial commit. No tag on the scaffold commit; releases begin at the first `make bump-*`.
