"""
test_health.py — Health Check Smoke Test
========================================
MOTIVE:
-------
Azure App Service and container orchestrators (Docker, Kubernetes) perform periodic
health probes against `GET /health` to determine if the container is alive and ready
to receive traffic.
If this endpoint fails or returns a non-200 status code, Azure App Service restarts the
container automatically. This test ensures the most fundamental route never breaks.
"""


def test_health_check(client):
    """GET /health must return 200 OK with {"status": "ok"}."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
