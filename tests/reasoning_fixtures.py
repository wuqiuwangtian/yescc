"""Explicit historical-product fixtures; current CLI tests remain current."""

from pathlib import Path

import pytest
from bench.creative_reasoning_archive import restore_snapshot


@pytest.fixture(scope="module")
def historical_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    parent = tmp_path_factory.mktemp("reasoning-historical-product")
    return restore_snapshot(parent / "source")
