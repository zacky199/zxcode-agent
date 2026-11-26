import json
import pytest
from zxcode_mcp import MCPAuditLog, MCPManager


def test_audit_append_verify_chain_and_private_permissions(tmp_path):
    audit = MCPAuditLog(tmp_path / "private" / "audit.jsonl")
    audit.append(event="approval_decision", server="demo", tool="lookup", risk="read_only", policy="approval", decision="allow", status="approved")
    audit.append(event="execution", server="demo", tool="lookup", risk="read_only", policy="approval", decision="allow", status="success")
    result = audit.verify()
    assert result["valid"] and result["entries"] == 2
    assert audit.path.stat().st_mode & 0o777 == 0o600
    assert audit.path.parent.stat().st_mode & 0o777 == 0o700
    entries = [json.loads(line) for line in audit.path.read_text().splitlines()]
    assert entries[1]["previous_hash"] == entries[0]["hash"]
    assert "arguments" not in entries[0] and "output" not in entries[0]


def test_audit_detects_tampering_and_refuses_append(tmp_path):
    audit = MCPAuditLog(tmp_path / "audit.jsonl")
    audit.append(event="execution", tool="fetch", status="success")
    content = audit.path.read_text().replace('"status": "success"', '"status": "changed"')
    audit.path.write_text(content)
    assert audit.verify()["valid"] is False
    with pytest.raises(OSError, match="integrity failure"):
        audit.append(event="new_event")


def test_audit_refuses_symlink(tmp_path):
    real = tmp_path / "real.jsonl"
    real.write_text("")
    link = tmp_path / "linked.jsonl"
    link.symlink_to(real)
    with pytest.raises(OSError, match="symlink"):
        MCPAuditLog(link)



def test_manager_audits_approved_invocation_without_arguments_or_result(tmp_path):
    from types import SimpleNamespace
    audit = MCPAuditLog(tmp_path / "audit.jsonl")
    manager = MCPManager(approval_callback=lambda *_: True, audit_log=audit)
    manager.add_server("sample", "https://mcp.example.com/mcp", allowed_tools={"lookup"})
    class StubClient:
        called = []
        async def list_tools(self):
            return [SimpleNamespace(name="lookup", description="read data", inputSchema={
                "type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"], "additionalProperties": False,
            })]
        async def call_tool(self, name, args):
            self.called.append((name, args))
            return {"content": [{"type": "text", "text": "mock-output"}], "isError": False}
    manager.clients["sample"] = StubClient()
    import asyncio
    asyncio.run(manager.refresh_server("sample"))
    result = asyncio.run(manager.execute_tool("mcp__sample__lookup", {"query": "secret-user-input"}))
    assert result["status"] == "ok"
    checked = audit.verify()
    assert checked["valid"] and checked["entries"] == 3
    lines = audit.path.read_text()
    assert "secret-user-input" not in lines and "mock-output" not in lines
    records = [json.loads(line) for line in lines.splitlines()]
    assert [r["event"] for r in records] == ["approval_requested", "approval_decision", "execution"]
    assert records[1]["decision"] == "allow" and records[2]["status"] == "success"


def test_audit_size_limit_blocks_append(tmp_path):
    audit = MCPAuditLog(tmp_path / "audit.jsonl", max_bytes=550, max_archives=0)
    audit.append(event="small")
    with pytest.raises(OSError, match="size limit"):
        audit.append(event="oversized", server="s" * 64, tool="t" * 96)


def test_manager_records_policy_block_before_remote_call(tmp_path):
    audit = MCPAuditLog(tmp_path / "audit.jsonl")
    manager = MCPManager(audit_log=audit)
    manager.add_server("sample", "https://mcp.example.com/mcp", allowed_tools={"lookup"}, policy_mode="deny")
    from types import SimpleNamespace
    class StubClient:
        called = False
        async def list_tools(self):
            return [SimpleNamespace(name="lookup", description="read data", inputSchema={"type":"object", "properties":{}, "additionalProperties":False})]
        async def call_tool(self, *args):
            self.called = True
    manager.clients["sample"] = StubClient()
    import asyncio
    asyncio.run(manager.refresh_server("sample"))
    result = asyncio.run(manager.execute_tool("mcp__sample__lookup", {}))
    assert result["status"] == "error" and manager.clients["sample"].called is False
    records = [json.loads(line) for line in audit.path.read_text().splitlines()]
    assert len(records) == 1 and records[0]["status"] == "policy_blocked" and records[0]["decision"] == "deny"


def test_required_audit_unavailable_fails_closed_before_remote_call():
    from types import SimpleNamespace
    manager = MCPManager(approval_callback=lambda *_: True, audit_required=True)
    manager.add_server("demo", "https://mcp.example.com/mcp", allowed_tools={"lookup"})
    class Stub:
        called = False
        async def list_tools(self):
            return [SimpleNamespace(name="lookup", description="read data", inputSchema={"type":"object", "properties":{}, "additionalProperties":False})]
        async def call_tool(self, *args):
            self.called = True
    manager.clients["demo"] = Stub()
    import asyncio
    asyncio.run(manager.refresh_server("demo"))
    outcome = asyncio.run(manager.execute_tool("mcp__demo__lookup", {}))
    assert outcome["status"] == "error" and "audit logging" in outcome["output"]
    assert manager.clients["demo"].called is False


def test_audit_rotates_segments_and_preserves_cross_segment_chain(tmp_path):
    audit = MCPAuditLog(tmp_path / "audit.jsonl", max_bytes=500, max_archives=8)
    for i in range(6):
        audit.append(event=f"event-{i}")
    verified = audit.verify()
    assert verified["valid"] and verified["entries"] == 6 and verified["segments"] >= 2
    archives = audit.archive_paths()
    assert archives and all(path.stat().st_mode & 0o777 == 0o600 for path in archives)
    all_records = [json.loads(line) for path in archives + [audit.path] if path.exists() for line in path.read_text().splitlines()]
    assert len(all_records) == 6
    for left, right in zip(all_records, all_records[1:]):
        assert right["previous_hash"] == left["hash"]


def test_audit_detects_tampering_in_rotated_archive(tmp_path):
    audit = MCPAuditLog(tmp_path / "audit.jsonl", max_bytes=500, max_archives=3)
    for i in range(4):
        audit.append(event=f"rotation-{i}")
    archive = audit.archive_paths()[0]
    archive.write_text(archive.read_text().replace('"event": "rotation-0"', '"event": "altered"'))
    assert audit.verify()["valid"] is False
    with pytest.raises(OSError, match="integrity failure"):
        audit.append(event="blocked-after-archive-tamper")


def test_audit_rotation_archive_limit_fails_closed_without_deleting(tmp_path):
    audit = MCPAuditLog(tmp_path / "audit.jsonl", max_bytes=500, max_archives=1)
    for i in range(10):
        try:
            audit.append(event=f"archive-limit-{i}")
        except OSError as exc:
            assert "archive limit" in str(exc)
            break
    else:
        pytest.fail("expected archive limit to stop appends")
    before = [p.name for p in audit.archive_paths()]
    assert len(before) == 1
    assert audit.verify()["valid"]
