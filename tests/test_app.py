import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"Hello": "World"}


def test_sse_endpoint():
    with client.stream("GET", "/sse/") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        first_line = next(response.iter_lines())
        assert b"endpoint" in first_line


def test_http_stream_endpoint():
    with client.stream("GET", "/http/stream/") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        first_line = next(response.iter_lines())
        data = json.loads(first_line)
        assert "endpoint" in data
