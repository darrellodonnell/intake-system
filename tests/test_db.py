from intake_system.db import _migration_files


def test_migration_files_are_discoverable_from_repo_root() -> None:
    migrations = _migration_files()

    assert migrations
    assert migrations[0].name == "001_init.sql"

