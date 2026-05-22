import pytest
import httpx
import respx
from docdr.client import DocDrClient, FileUpdate
from docdr.diff import FileDiff


@respx.mock
def test_send_maintenance_returns_updates():
    respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(200, json={
            "updates": [{"path": "README.md", "content": "# Updated"}],
            "no_update": False,
        })
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    diffs = [FileDiff(path="src/main.py", diff="+ def foo(): pass")]
    updates = client.send_maintenance(repo="owner/repo", diffs=diffs, doc_files=[])
    assert len(updates) == 1
    assert updates[0].path == "README.md"
    assert updates[0].content == "# Updated"


@respx.mock
def test_send_maintenance_no_update():
    respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(200, json={"updates": [], "no_update": True})
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="test-key")
    updates = client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
    assert updates == []


@respx.mock
def test_send_maintenance_raises_on_403():
    respx.post("http://localhost:8000/v1/maintenance").mock(
        return_value=httpx.Response(403, json={"detail": "Invalid license key"})
    )
    client = DocDrClient(base_url="http://localhost:8000", license_key="bad-key")
    with pytest.raises(httpx.HTTPStatusError):
        client.send_maintenance(repo="owner/repo", diffs=[], doc_files=[])
