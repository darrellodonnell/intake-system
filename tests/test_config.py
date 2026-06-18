from pathlib import Path

from intake_system.config import load_config


def test_load_example_config_has_required_destinations() -> None:
    config = load_config(Path("config/intake.example.yaml"))

    assert config.database.schema == "intake"
    assert config.review.privacy == "private"
    assert "ayra_confidential" in config.destinations
    assert config.destinations["ayra_confidential"].private is True

