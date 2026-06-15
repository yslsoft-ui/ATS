# -*- coding: utf-8 -*-
import pytest
from src.config.manager import ConfigManager

@pytest.fixture(autouse=True)
def clear_config_manager_cache():
    """테스트 간의 격리를 위해 ConfigManager._instances 캐시를 매 테스트 전 초기화합니다."""
    ConfigManager._instances.clear()
