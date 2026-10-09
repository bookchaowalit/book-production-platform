from __future__ import annotations

from pathlib import Path

import yaml


COMPOSE_PATH = Path(__file__).parents[1] / "docker-compose.yml"


def test_compose_defines_healthy_video_postproduction_runtime():
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = document["services"]

    assert {"postgres", "redis", "api", "worker", "web"} <= set(services)
    for service_name in services:
        assert "healthcheck" in services[service_name], service_name

    assert services["api"]["image"] == services["worker"]["image"]
    assert "video_storage:/app/storage" in services["api"]["volumes"]
    assert "video_storage:/app/storage" in services["worker"]["volumes"]
    assert "video_storage" in document["volumes"]


def test_compose_publishes_web_and_api_only_on_loopback():
    document = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = document["services"]

    assert services["api"]["ports"] == ["127.0.0.1:8000:8000"]
    assert services["web"]["ports"] == ["127.0.0.1:3400:3000"]
