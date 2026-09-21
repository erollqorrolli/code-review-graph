"""An application-shaped workload for httpx, as opposed to its test suite.

Every result so far derives its runtime signal from httpx's own tests, which
are written to cover the library exhaustively. That is not how anyone uses it.
This exercises the client the way an application does: a handful of endpoints,
hit repeatedly, through the paths real callers take.

Runs against an in-process WSGI app so it is deterministic and needs no
network, while still going through the real client, transport, and response
machinery.
"""

from __future__ import annotations

import json


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")

    if path == "/redirect":
        start_response("302 Found", [("Location", "/items")])
        return [b""]
    if path == "/protected":
        if not environ.get("HTTP_AUTHORIZATION"):
            start_response("401 Unauthorized", [("WWW-Authenticate", "Basic")])
            return [b'{"error":"auth required"}']
        start_response("200 OK", [("Content-Type", "application/json")])
        return [b'{"ok":true}']
    if path == "/missing":
        start_response("404 Not Found", [("Content-Type", "application/json")])
        return [b'{"error":"not found"}']
    if path == "/stream":
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [f"chunk {i}\n".encode() for i in range(20)]
    if path == "/slow":
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"done"]

    body = json.dumps({"method": method, "path": path, "items": [1, 2, 3]}).encode()
    start_response("200 OK", [
        ("Content-Type", "application/json"),
        ("Set-Cookie", "session=abc123; Path=/"),
    ])
    return [body]


def run(iterations: int = 25) -> dict:
    """Drive httpx the way an application would. Returns a small summary."""
    import httpx

    transport = httpx.WSGITransport(app=app)
    counts = {"ok": 0, "errors": 0, "redirects": 0, "streamed": 0}

    with httpx.Client(transport=transport, base_url="http://app.local",
                      follow_redirects=True, timeout=5.0) as client:
        for i in range(iterations):
            r = client.get("/items", params={"page": i, "limit": 20})
            r.json()
            counts["ok"] += r.status_code == 200

            r = client.post("/items", json={"name": f"item-{i}", "tags": ["a", "b"]})
            r.json()
            counts["ok"] += r.status_code == 200

            r = client.put(f"/items/{i}", content=b"raw bytes",
                           headers={"X-Request-Id": str(i)})
            counts["ok"] += r.status_code == 200

            client.delete(f"/items/{i}")

            r = client.get("/redirect")
            counts["redirects"] += len(r.history)

            r = client.get("/missing")
            counts["errors"] += r.status_code == 404
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError:
                pass

            r = client.get("/protected", auth=("user", "pass"))
            counts["ok"] += r.status_code == 200

            with client.stream("GET", "/stream") as resp:
                for _ in resp.iter_lines():
                    counts["streamed"] += 1

    # a separate short-lived client, as apps that don't pool connections do
    for i in range(5):
        with httpx.Client(transport=transport, base_url="http://app.local") as c:
            c.get("/items", headers={"Accept": "application/json"}).json()

    return counts


if __name__ == "__main__":
    print(run())
