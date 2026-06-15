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

def test_config_manager_singleton(monkeypatch):
    # 4. 동일 경로에 대한 조건부 싱글톤 인스턴스 공유 검증
    ConfigManager._instances.clear()
    
    cfg1 = ConfigManager()
    cfg2 = ConfigManager()
    cfg3 = ConfigManager("config/settings.yaml")
    
    # 동일한 설정 경로(config/settings.yaml)에 대해서는 동일한 인스턴스를 공유해야 함
    assert cfg1 is cfg2
    assert cfg1 is cfg3
    
    # 임시 설정 경로에 대해서는 다른 인스턴스가 생성되어 격리되어야 함
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as tmp:
        tmp.write("system:\n  operation_mode: mock\n")
        tmp_name = tmp.name
        
    try:
        cfg_temp = ConfigManager(tmp_name)
        assert cfg_temp is not cfg1
        assert cfg_temp.get("system.operation_mode") == "mock"
    finally:
        import os
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
