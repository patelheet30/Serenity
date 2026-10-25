"""deploy.sh finds containers by name, so its defaults must match docker-compose.yml."""

import re
from pathlib import Path

DEPLOY = Path("scripts/deploy.sh").read_text()
COMPOSE = Path("docker-compose.yml").read_text()


def deploy_default(var: str) -> str:
    match = re.search(rf"\${{DEPLOY_{var}:-([\w-]+)}}", DEPLOY)
    assert match, f"no DEPLOY_{var} default in deploy.sh"
    return match.group(1)


def test_container_defaults_match_compose():
    names = set(re.findall(r"^\s+container_name: (\S+)$", COMPOSE, re.M))
    assert deploy_default("CONTAINER") in names
    assert deploy_default("DASHBOARD") in names
