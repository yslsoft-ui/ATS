# -*- coding: utf-8 -*-

class IndicatorNotReady(Exception):
    """캔들 부족 또는 웜업 중으로 인해 기술 지표를 계산할 수 없는 정상적인 대기 상태 예외."""
    pass

class UnsupportedIndicatorError(ValueError):
    """지원하지 않는 기술 지표명을 요청했을 때 발생하는 예외."""
    pass
