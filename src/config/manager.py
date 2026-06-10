import os
import yaml
from src.engine.utils.telemetry import get_logger
import asyncio
from typing import Any, Dict, Optional, Callable, List
from dotenv import load_dotenv

logger = get_logger(__name__)

class ConfigManager:
    """
    YAML 설정을 관리하고, 실시간 변경 감지 및 환경 변수 치환을 수행합니다.
    """
    def __init__(self, config_path: Optional[str] = None):
        # .env 파일 로드
        load_dotenv()
        
        env_config_path = os.getenv("ATS_CONFIG")
        
        obsolete_profiles = ("settings_production.yaml", "settings_rehearsal.yaml")
        if env_config_path and any(obsolete in env_config_path for obsolete in obsolete_profiles):
            raise ValueError(
                f"CRITICAL CONFIGURATION ERROR: The configuration profile '{env_config_path}' has been deleted. "
                "Only 'config/settings.yaml' is supported."
            )
        if config_path and any(obsolete in config_path for obsolete in obsolete_profiles):
            raise ValueError(
                f"CRITICAL CONFIGURATION ERROR: The configuration profile '{config_path}' has been deleted. "
                "Only 'config/settings.yaml' is supported."
            )
            
        if env_config_path:
            # tests나 임시 테스트용 설정 파일이 아닌 경우에만 덮어씀
            is_legacy_default = config_path in ("config/settings.yaml", None)
            if is_legacy_default:
                self.config_path = env_config_path
            else:
                self.config_path = config_path
        else:
            self.config_path = config_path or "config/settings.yaml"
        self.config: Dict[str, Any] = {}
        self.last_mtime: float = 0
        self.subscribers: List[Callable[[Dict[str, Any]], Any]] = []
        
        # 초기 로드
        self.reload()
        
        # 변경 감지 태스크
        self._watch_task: Optional[asyncio.Task] = None

    def reload(self):
        """설정 파일을 다시 읽고 환경 변수 치환 및 병합을 수행합니다."""
        if not os.path.exists(self.config_path):
            logger.error(f"Config file not found: {self.config_path}")
            return False

        try:
            import hashlib
            with open(self.config_path, 'rb') as f:
                content_bytes = f.read()
                sha256_val = hashlib.sha256(content_bytes).hexdigest()
                
            with open(self.config_path, 'r', encoding='utf-8') as f:
                new_config = yaml.safe_load(f) or {}
                
            # 1. YAML 내부의 ${VAR_NAME} 패턴 치환
            self._substitute_env_vars(new_config)
            
            # 2. 외부 환경 변수 강제 병합 (기존 SECTION__KEY 방식 유지)
            self._merge_env_vars(new_config)
            
            # 3. 비상 Fail-Fast 안전 가드 검증
            sys_cfg = new_config.get('system', {})
            live_trading = sys_cfg.get('live_trading_enabled')
            auto_promo = sys_cfg.get('auto_strategy_promotion_enabled')
            
            if live_trading is True and auto_promo is True:
                override = os.getenv("ATS_EXPLICIT_REAL_TRADING_OVERRIDE")
                if override != "true":
                    raise ValueError(
                        "CRITICAL SAFETY GATE VIOLATION: Both live_trading_enabled and "
                        "auto_strategy_promotion_enabled are enabled in configuration, "
                        "but the safety override environment variable ATS_EXPLICIT_REAL_TRADING_OVERRIDE='true' is missing! "
                        "Emergency halt to prevent accidental live trade execution."
                    )
            
            self.config = new_config
            self.last_mtime = os.path.getmtime(self.config_path)
            self.config_sha256 = sha256_val
            
            logger.info(f"Configuration loaded from {self.config_path}")
            logger.info(f"Config File SHA256: {self.config_sha256}")
            logger.info(f"Config File Modified At: {self.last_mtime}")
            
            return True
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            raise e

    def _substitute_env_vars(self, config: Any):
        """설정 내의 ${VAR_NAME} 형식을 실제 환경 변수 값으로 치환합니다."""
        if isinstance(config, dict):
            for k, v in config.items():
                if isinstance(v, (dict, list)):
                    self._substitute_env_vars(v)
                elif isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                    env_key = v[2:-1]
                    env_val = os.getenv(env_key)
                    if env_val is not None:
                        # 숫자나 불리언 형변환 시도
                        try:
                            if env_val.lower() in ('true', 'false'):
                                config[k] = env_val.lower() == 'true'
                            elif env_val.isdigit():
                                config[k] = int(env_val)
                            elif env_val.replace('.', '', 1).isdigit():
                                config[k] = float(env_val)
                            else:
                                config[k] = env_val
                        except:
                            config[k] = env_val
        elif isinstance(config, list):
            for i, v in enumerate(config):
                if isinstance(v, (dict, list)):
                    self._substitute_env_vars(v)
                elif isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                    env_key = v[2:-1]
                    env_val = os.getenv(env_key)
                    if env_val is not None:
                        config[i] = env_val

    def _merge_env_vars(self, config: Dict[str, Any]):
        """환경 변수를 설정에 병합합니다 (형식: SECTION__KEY)."""
        for env_key, env_val in os.environ.items():
            if '__' in env_key:
                parts = env_key.lower().split('__')
                d = config
                for part in parts[:-1]:
                    if part not in d or not isinstance(d[part], dict):
                        d[part] = {}
                    d = d[part]
                
                last_key = parts[-1]
                # 기존 값의 타입에 맞춰 형변환 시도
                if last_key in d:
                    try:
                        if isinstance(d[last_key], bool):
                            d[last_key] = env_val.lower() in ('true', '1', 'yes')
                        elif isinstance(d[last_key], int):
                            d[last_key] = int(env_val)
                        elif isinstance(d[last_key], float):
                            d[last_key] = float(env_val)
                        else:
                            d[last_key] = env_val
                    except:
                        d[last_key] = env_val
                else:
                    d[last_key] = env_val

    def get(self, key: str, default: Any = None) -> Any:
        """점(.)으로 구분된 키를 사용하여 설정값을 가져옵니다 (예: 'system.db_path')."""
        parts = key.split('.')
        val = self.config
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                return default
        return val if val is not None else default

    def subscribe(self, callback: Callable[[Dict[str, Any]], Any]):
        """설정 변경 시 호출될 콜백을 등록합니다."""
        self.subscribers.append(callback)

    async def start_watching(self, interval: float = 2.0):
        """백그라운드에서 파일 변경을 감시합니다."""
        if self._watch_task:
            return
        
        self._watch_task = asyncio.create_task(self._watch_loop(interval))
        logger.info("Config hot-reloading watcher started.")

    async def stop_watching(self):
        """감시 태스크를 중지합니다."""
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

    async def _watch_loop(self, interval: float):
        while True:
            try:
                await asyncio.sleep(interval)
                current_mtime = os.path.getmtime(self.config_path)
                if current_mtime > self.last_mtime:
                    logger.info("Config file change detected. Reloading...")
                    if self.reload():
                        # 구독자들에게 알림
                        for callback in self.subscribers:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(self.config)
                            else:
                                callback(self.config)
            except Exception as e:
                logger.error(f"Config watcher error: {e}")

    def update(self, key: str, value: Any):
        """특정 설정을 업데이트하고 파일로 즉시 저장합니다."""
        parts = key.split('.')
        d = self.config
        for part in parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
        
        d[parts[-1]] = value
        return self.save()

    def save(self):
        """현재 메모리의 설정을 파일로 저장합니다."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, allow_unicode=True, sort_keys=False)
            self.last_mtime = os.path.getmtime(self.config_path)
            return True
        except Exception as e:
            logger.error(f"Error saving config to {self.config_path}: {e}")
            return False
