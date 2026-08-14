# qbit_manage-mcp

Part of the [arr-mcps](https://github.com/arr-mcps/arr-mcps) collection.
MCP server exposing [qbit_manage](https://github.com/StuffAnThings/qbit_manage)'s
Web API as tools, so an LLM can read and manage a qbit_manage instance:
run maintenance commands against your qBittorrent torrents, manage config
files (create/read/update/delete/validate/backup/restore), the scheduler, logs,
security settings, and system state.

Built with [FastMCP](https://gofastmcp.com). qbit_manage's API has no vendored
OpenAPI spec, so the endpoint table is hand-maintained against qbit_manage's
`modules/web_api.py` handler routes (see `AGENTS.md`).

## Enabling the Web API on your qbit_manage server

qbit_manage's REST API runs at port 8181 by default. Start it with the web
server enabled, e.g. `qbit_manage --web-server --host 0.0.0.0 --port 8181`
(or set `QBT_WEB_SERVER=true` / `QBT_HOST` / `QBT_PORT` in Docker). See
[qbit_manage's Web-API docs](https://github.com/StuffAnThings/qbit_manage/blob/develop/docs/Web-API.md)
for details.

## Install

Download a wheel from the [latest release](https://github.com/arr-mcps/qbit_manage-mcp/releases/latest)
and install it as a `uv` tool (no repo checkout needed):

```bash
uv tool install qbit_manage_mcp-*.whl
```

This puts a `qbit-manage-mcp` command on your PATH. Register it with Claude Code:

```bash
claude mcp add qbit-manage \
  --env QBIT_MANAGE_URL=http://qbit-manage.example.com:8181 \
  --env QBIT_MANAGE_API_KEY=<key> \
  -- qbit-manage-mcp
```

### From source

```bash
uv sync
cp .env.example .env   # fill in QBIT_MANAGE_URL and QBIT_MANAGE_API_KEY
```

```bash
claude mcp add qbit-manage \
  --env QBIT_MANAGE_URL=http://qbit-manage.example.com:8181 \
  --env QBIT_MANAGE_API_KEY=<key> \
  -- uv run --directory /path/to/qbit_manage-mcp qbit-manage-mcp
```

## Config

| Env var | Required | Default |
|---|---|---|
| `QBIT_MANAGE_URL` | yes | - |
| `QBIT_MANAGE_API_KEY` | yes* | none (no API key header sent if unset) |
| `QBIT_MANAGE_USERNAME` | no | used for Basic auth when no API key is set |
| `QBIT_MANAGE_PASSWORD` | no | used for Basic auth when no API key is set |

\* If authentication is enabled on qbit_manage (`method: api_only` or `basic`)
you must set the API key (or username/password), or every authenticated call
returns 401. If authentication is disabled or `bypass_auth_for_local` is on,
the server still works without credentials. If your qbit_manage instance is
served under a base URL (`--base-url`/`QBT_BASE_URL`), include that subpath in
`QBIT_MANAGE_URL`.

## Tools

Six resource-scoped tools. Each takes an `operation` (one of the listed
endpoints) plus an `arguments` dict matching that operation's parameters, and
dispatches to the underlying endpoint. This is the fleet's portmanteau
pattern: all 23 qbit_manage endpoints wrapped as 6 tools so every session's
system prompt stays small.

| Tool | Operations (endpoints) |
|---|---|
| `qbit_manage_config` | list_configs, get_config, create_config, update_config, delete_config, validate_config, backup_config, list_config_backups, restore_config_from_backup (`/api/configs`, `/api/configs/{filename}` + `/validate`, `/backup`, `/backups`, `/restore`) |
| `qbit_manage_commands` | run_command (`/api/run-command`) |
| `qbit_manage_scheduler` | get_scheduler_status, update_schedule, toggle_schedule_persistence (`/api/scheduler`, `/api/schedule`, `/api/schedule/persistence/toggle`) |
| `qbit_manage_logs` | get_logs, list_log_files, get_documentation (`/api/logs`, `/api/log_files`, `/api/docs`) |
| `qbit_manage_system` | get_version, health_check, get_base_url, force_reset_running_state (`/api/version`, `/api/health`, `/api/get_base_url`, `/api/system/force-reset`) |
| `qbit_manage_security` | get_security_settings, get_security_status, update_security_settings (`/api/security`, `/api/security/status`) |

`run_command` is how an LLM triggers actual qbit_manage work against
qBittorrent: pass a body with `commands` (e.g. `["cat_update", "tag_update"]`),
optionally `hashes` to scope to specific torrents, and `dry_run: true` to
preview without side effects. Valid commands: `cat_update`, `tag_update`,
`recheck`, `rem_unregistered`, `tag_tracker_error`, `rem_orphaned`,
`tag_nohardlinks`, `share_limits`.

Config write operations take the config data as `{"data": <config dict>}` —
`data` mirrors the YAML structure. `get_documentation` returns raw markdown
text, not JSON.

## Development

```bash
make help  # list all commands
```

| Command | Does |
|---|---|
| `make sync` | `uv sync` |
| `make test` | Offline tests - one per endpoint, mocked HTTP |
| `make test-integration` | Tests against the live instance (needs `QBIT_MANAGE_URL`/`QBIT_MANAGE_API_KEY`; write tests need `QBIT_MANAGE_WRITE_TESTS=1`) |
| `make build` | Build wheel + sdist into `dist/` |
| `make bump-patch` / `bump-minor` / `bump-major` | Bump the version in `pyproject.toml` + `uv.lock` |
| `make clean` | Remove build artifacts |

The release workflow (`.github/workflows/release.yml`) builds and publishes to
[Releases](https://github.com/arr-mcps/qbit_manage-mcp/releases) whenever a
`v*` tag is pushed - so the usual flow is `make bump-patch`, commit, then tag
and push.
