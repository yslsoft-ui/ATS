# -*- coding: utf-8 -*-

import os
import re

def test_no_legacy_strategy_patterns():
    """전략 관련 코드베이스에 레거시 패턴(on_candle, required_indicators, context.indicators)이 없는지 정적 검증합니다."""
    
    target_files = [
        "src/engine/strategy.py",
        "src/engine/strategy_host.py"
    ]
    
    # src/engine/strategies/ 폴더 내 파일 수집
    strategies_dir = "src/engine/strategies"
    if os.path.exists(strategies_dir):
        for f in os.listdir(strategies_dir):
            if f.endswith(".py") and f != "__init__.py":
                target_files.append(os.path.join(strategies_dir, f))
                
    forbidden_patterns = {
        r"def\s+on_candle": "def on_candle",
        r"required_indicators": "required_indicators",
        r"\bcontext\.indicators\b": "context.indicators",
        r"\bself\.indicators\b": "self.indicators"
    }
    
    violations = []
    
    for file_path in target_files:
        if not os.path.exists(file_path):
            continue
            
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        for pattern, desc in forbidden_patterns.items():
            # 구체적인 위반 라인 찾기
            for line_no, line in enumerate(content.splitlines(), 1):
                if re.search(pattern, line):
                    # 주석 처리된 부분은 허용
                    if line.strip().startswith("#") or line.strip().startswith('"""') or line.strip().startswith('class '):
                        continue
                    # get_indicator() 설명 등의 주석/문자열 체크 우회
                    if "def " not in line and "on_candle" in line and "#" in line:
                        continue
                    violations.append(
                        f"Violation in {file_path}:{line_no} - Found forbidden pattern '{desc}': {line.strip()}"
                    )
                        
    assert not violations, "Found legacy strategy patterns that must be removed:\n" + "\n".join(violations)
