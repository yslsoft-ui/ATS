# -*- coding: utf-8 -*-

import os
import pytest
from src.config.manager import ConfigManager

def test_config_unification_obsolete_profiles_error(monkeypatch):
    # 1. 명시적으로 삭제된 파일 경로를 넘겨줄 경우 ValueError 발생 확인
    monkeypatch.delenv("ATS_CONFIG", raising=False)
    with pytest.raises(ValueError) as excinfo:
        ConfigManager("config/settings_production.yaml")
    assert "deleted" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        ConfigManager("config/settings_rehearsal.yaml")
    assert "deleted" in str(excinfo.value)

def test_config_unification_obsolete_env_error(monkeypatch):
    # 2. ATS_CONFIG 환경 변수에 삭제된 프로필이 지정된 경우 ValueError 발생 확인
    monkeypatch.setenv("ATS_CONFIG", "config/settings_production.yaml")
    with pytest.raises(ValueError) as excinfo:
        ConfigManager()
    assert "deleted" in str(excinfo.value)

    monkeypatch.setenv("ATS_CONFIG", "config/settings_rehearsal.yaml")
    with pytest.raises(ValueError) as excinfo:
        ConfigManager()
    assert "deleted" in str(excinfo.value)

def test_config_unification_default_load(monkeypatch):
    # 3. 기본 기동 시 config/settings.yaml 정상 로드 및 안전 가드 확인
    monkeypatch.delenv("ATS_CONFIG", raising=False)

    config = ConfigManager()
    assert config.config_path == "config/settings.yaml"
    
    # 필수 안전 설정 가드 검증
    assert config.get("system.operation_mode") == "shadow"
    assert config.get("system.live_trading_enabled") is False
    assert config.get("system.auto_strategy_promotion_enabled") is False

    # 병합된 운영 horizons 검증
    horizons = config.get("system.horizons")
    assert horizons is not None
    assert "crypto" in horizons
    assert "stock" in horizons
    
    # 리허설용 단축 horizon (10m, 30m, 2h)이 settings.yaml에 없음을 검증
    crypto_horizons = [h["name"] for h in horizons["crypto"]]
    assert "10m" not in crypto_horizons
    assert "1d" in crypto_horizons
