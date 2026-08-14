"""Integration tests against a real qbit_manage instance.

Skipped unless QBIT_MANAGE_URL and QBIT_MANAGE_API_KEY are set. Run with:
    uv run pytest -m integration

GET endpoints run read-only against the live instance. Write tests
(POST/PUT/DELETE) only run when QBIT_MANAGE_WRITE_TESTS=1 and perform a safe
create -> validate -> update -> delete cycle against a scratch config file
cloned from the instance's current default config, then clean up. Never point
write tests at a production instance you care about. run_command is NOT covered
here - it executes real qbit_manage operations against qBittorrent and belongs
to manual/ad-hoc use.
"""

import os
import uuid

import pytest
from fastmcp import Client

import qbit_manage_mcp

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.environ.get("QBIT_MANAGE_URL") and os.environ.get("QBIT_MANAGE_API_KEY")),
        reason="requires QBIT_MANAGE_URL and QBIT_MANAGE_API_KEY",
    ),
]

WRITE_TESTS = os.environ.get("QBIT_MANAGE_WRITE_TESTS") == "1"


@pytest.fixture(autouse=True)
def configure_client():
    qbit_manage_mcp._client = qbit_manage_mcp.build_client(os.environ["QBIT_MANAGE_URL"], os.environ["QBIT_MANAGE_API_KEY"])
    yield


async def call(name, **kwargs):
    async with Client(qbit_manage_mcp.mcp) as c:
        return await c.call_tool(name, kwargs)


# --- read-only ------------------------------------------------------------

async def test_version():
    result = await call("qbit_manage_get_version")
    assert "version" in result.data
    assert "branch" in result.data


async def test_health():
    result = await call("qbit_manage_health_check")
    assert result.data["status"] in ("healthy", "degraded", "busy", "unhealthy")


async def test_get_base_url():
    result = await call("qbit_manage_get_base_url")
    assert "baseUrl" in result.data


async def test_list_configs():
    result = await call("qbit_manage_list_configs")
    assert isinstance(result.data["configs"], list)
    assert "default_config" in result.data


async def test_get_config():
    result = await call("qbit_manage_list_configs")
    default_config = result.data["default_config"]

    got = await call("qbit_manage_get_config", filename=default_config)
    assert got.data["filename"] == default_config
    assert isinstance(got.data["data"], dict)


async def test_get_documentation_returns_markdown():
    result = await call("qbit_manage_get_documentation", file="Home.md")
    assert isinstance(result.data, str)


async def test_scheduler_status():
    result = await call("qbit_manage_get_scheduler_status")
    assert "is_running" in result.data


async def test_list_log_files():
    result = await call("qbit_manage_list_log_files")
    assert "log_files" in result.data


async def test_get_logs():
    result = await call("qbit_manage_get_logs", limit=5)
    assert isinstance(result.data["logs"], list)


async def test_security_status():
    result = await call("qbit_manage_get_security_status")
    assert "enabled" in result.data


# --- write (only with QBIT_MANAGE_WRITE_TESTS=1) --------------------------------

@pytest.mark.skipif(not WRITE_TESTS, reason="set QBIT_MANAGE_WRITE_TESTS=1 to run write tests")
async def test_config_lifecycle():
    list_result = await call("qbit_manage_list_configs")
    default_config = list_result.data["default_config"]
    source = await call("qbit_manage_get_config", filename=default_config)
    config_data = source.data["data"]

    scratch = f"mcp-test-{uuid.uuid4().hex[:8]}.yml"
    try:
        created = await call("qbit_manage_create_config", filename=scratch, body={"data": config_data})
        assert created.data["status"] == "success"

        validated = await call("qbit_manage_validate_config", filename=scratch, body={"data": config_data})
        assert "valid" in validated.data

        got = await call("qbit_manage_get_config", filename=scratch)
        assert got.data["filename"] == scratch

        updated = await call("qbit_manage_update_config", filename=scratch, body={"data": config_data})
        assert updated.data["status"] == "success"

        backed_up = await call("qbit_manage_backup_config", filename=scratch)
        assert backed_up.data["status"] == "success"
        assert backed_up.data["backup_file"].startswith(scratch.rsplit(".", 1)[0])

        backups = await call("qbit_manage_list_config_backups", filename=scratch)
        assert len(backups.data["backups"]) >= 1
    finally:
        await call("qbit_manage_delete_config", filename=scratch)
