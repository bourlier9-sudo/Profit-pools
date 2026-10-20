import copy

import pytest

from profit_pools.core.config import load_config
from profit_pools.core.data.synthetic import generate
from profit_pools.core.taxonomy import default_metadata


@pytest.fixture(scope="session")
def meta():
    return default_metadata()


@pytest.fixture(scope="session")
def bundle(meta):
    return generate(meta=meta)


@pytest.fixture
def taxonomy_spec():
    """A mutable deep copy of the shipped taxonomy config."""
    return copy.deepcopy(load_config("taxonomy"))
