import os
import importlib.util
import sys
import logging
from typing import List, Dict
from .strategy import StrategyRegistry

logger = logging.getLogger(__name__)

# 전략 ID -> 파일 경로 매핑 저장
_strategy_files: Dict[str, str] = {}

def load_dynamic_strategies(strategies_dir: str):
    """
    지정된 디렉토리 내의 모든 .py 파일을 찾아 전략 클래스로 로드합니다.
    """
    if not os.path.exists(strategies_dir):
        os.makedirs(strategies_dir)
        return 0

    # 디렉토리를 sys.path에 추가 (임포트 호환성용)
    abs_dir = os.path.abspath(strategies_dir)
    if abs_dir not in sys.path:
        sys.path.append(abs_dir)

    loaded_count = 0
    print(f"[DEBUG] Scanning directory: {strategies_dir}")
    for filename in os.listdir(strategies_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            file_path = os.path.join(strategies_dir, filename)
            module_name = f"dynamic_strategy_{filename[:-3]}"
            print(f"[DEBUG] Attempting to load: {filename}")
            
            try:
                # 모듈 동적 로드
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                if spec is None or spec.loader is None:
                    print(f"[DEBUG] Failed to create spec for {filename}")
                    continue
                    
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                # 등록된 전략 ID 확인
                registered_ids = []
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and hasattr(attr, 'on_candle'):
                        s_id = attr.__name__.lower()
                        if s_id in StrategyRegistry._strategies:
                            _strategy_files[s_id] = file_path
                            registered_ids.append(s_id)

                loaded_count += 1
                print(f"[DEBUG] Successfully loaded {filename}. Registered IDs: {registered_ids}")
                logger.info(f"Strategy loaded successfully from {filename}")
            except Exception as e:
                logger.error(f"Failed to load strategy from {filename}: {str(e)}")

    return loaded_count

def unload_strategy(strategy_id: str):
    """
    메모리 레지스트리에서 전략을 제거합니다.
    (파일은 삭제하지 않고 관리 목록에서만 제외)
    """
    s_id = strategy_id.lower()
    
    if s_id in StrategyRegistry._strategies:
        del StrategyRegistry._strategies[s_id]
        if s_id in _strategy_files:
            del _strategy_files[s_id]
        return True
    return False
