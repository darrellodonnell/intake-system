from pathlib import Path

from intake_system.config import load_config
from intake_system.knowledge import KNOWLEDGE_BASE_KEYS


def test_load_example_config_has_required_destinations() -> None:
    config = load_config(Path("config/intake.example.yaml"))

    assert config.database.schema == "intake"
    assert config.review.privacy == "private"
    assert tuple(config.destinations) == KNOWLEDGE_BASE_KEYS
    assert "ayra_confidential" in config.destinations
    assert config.destinations["ayra_confidential"].private is True
