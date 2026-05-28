import asyncio
from fastapi import APIRouter, Request, HTTPException
import os
import aiohttp
import datetime
from typing import Optional
from src.database.repository import SqliteMarketDataRepository
from src.engine.utils.stock_mapper import stock_mapper
from src.engine.utils.telemetry import get_logger

logger = get_logger(__name__)
router = APIRouter()
market_repo = SqliteMarketDataRepository()

def get_prev_trading_day() -> str:
    """주말(토, 일)을 피해 가장 최근 영업일(T-1)을 YYYYMMDD 형태로 반환합니다."""
    today = datetime.date.today()
    weekday = today.weekday() # 월=0, 화=1, 수=2, 목=3, 금=4, 토=5, 일=6
    
    if weekday == 0: # 월요일인 경우 T-1은 지난주 금요일
        delta = 3
    elif weekday == 6: # 일요일인 경우 T-1은 지난주 금요일
        delta = 2
    elif weekday == 5: # 토요일인 경우 T-1은 지난주 금요일
        delta = 1
    else: # 화~금요일인 경우 T-1은 어제
        delta = 1
        
    prev_day = today - datetime.timedelta(days=delta)
    return prev_day.strftime('%Y%m%d')

@router.get("/market")
async def get_market(request: Request):
    """전체 마켓 종목 정보(한글명, 현재가, 변동률, 거래대금)를 반환합니다."""
    system = request.app.state.system
    results = await system.get_all_market_data()
    return results


@router.get("/symbols")
async def get_symbols(request: Request):
    """수집 가능한 전체 종목 목록을 반환합니다."""
    system = request.app.state.system
    all_symbols = []
    
    exchanges_config = system.config_manager.get('exchanges', {})
    
    for exch, config in exchanges_config.items():
        if not config.get('enabled', True):
            continue
            
        fixed_symbols = config.get('symbols', [])
        if fixed_symbols:
            # settings.yaml에 명시된 고정 종목 목록
            for s in fixed_symbols:
                all_symbols.append({
                    "exchange": exch,
                    "symbol": s,
                    "name": stock_mapper.get_name(exch, s)
                })
        else:
            # DB의 exchange_assets에서 거래소별 종목을 조회하고, 한글명은 메모리 캐시에서 가져옴
            from src.database.connection import get_db_conn
            try:
                async with get_db_conn(system.db_path) as db:
                    async with db.execute(
                        'SELECT symbol FROM exchange_assets WHERE exchange = ?', (exch,)
                    ) as cursor:
                        rows = await cursor.fetchall()
                for row in rows:
                    s = row['symbol']
                    all_symbols.append({
                        "exchange": exch,
                        "symbol": s,
                        "name": stock_mapper.get_name(exch, s)
                    })
            except Exception as e:
                logger.error(f"[get_symbols] Failed to load symbols for {exch}: {e}")
                
    return all_symbols

@router.get("/candles")
async def get_candles(
    request: Request = None, 
    exchange: str = "upbit", 
    symbol: str = "BTC", 
    interval: int = 60, 
    limit: int = 500, 
    start_ts: int = None, 
    end_ts: int = None
):
    """최적화된 고성능 캔들 데이터 반환 (저장소 패턴 위임)"""
    system = request.app.state.system if request and hasattr(request.app.state, 'system') else None
    return await market_repo.get_candles(
        exchange=exchange,
        symbol=symbol,
        interval=interval,
        limit=limit,
        start_ts=start_ts,
        end_ts=end_ts,
        system_app_state_system=system
    )

@router.get("/restored-candles")
async def get_restored_candles(
    exchange: Optional[str] = None,
    symbol: Optional[str] = None,
    limit_minutes: int = 1440
):
    """DB에 누락되었으나 틱으로 복구된 캔들 목록 반환"""
    return await market_repo.get_restored_candles(
        exchange=exchange,
        symbol=symbol,
        limit_minutes=limit_minutes
    )


@router.get("/market/ranking/types")
async def get_ranking_types():
    """12종 순위 분석 항목의 제목, 설명, 연동할 TR_ID 목록을 반환합니다."""
    return [
        {"tr_id": "FHPST01820000", "title": "예상체결 상승/하락", "description": "장전/장마감 예상체결가의 상승률/하락률 순위 분석을 조회합니다."},
        {"tr_id": "FHKST17010000", "title": "신용잔고 상위", "description": "융자/대주 신용잔고비율, 잔고수량, 잔고금액 등의 상위 순위를 조회합니다."},
        {"tr_id": "HHKDB13470100", "title": "배당률 상위", "description": "최근 결산/중간배당 기준 보통주/우선주의 현금 배당률 상위 순위를 조회합니다."},
        {"tr_id": "FHPST04820000", "title": "공매도 상위", "description": "일별/월별 공매도 거래량 비중 및 공매도 체결 수량 상위 순위를 조회합니다."},
        {"tr_id": "HHMCM000100C0", "title": "HTS조회상위", "description": "HTS(Home Trading System)에서 실시간으로 가장 많이 조회된 종목 순위입니다."},
        {"tr_id": "FHPST01870000", "title": "신고/신저근접", "description": "52주 최고가(신고가) 또는 최저가(신저가)에 근접한 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01770000", "title": "우선주 괴리율", "description": "보통주와 우선주 간의 가격 괴리율 상위 종목 순위를 조회합니다."},
        {"tr_id": "FHKST190900C0", "title": "대량체결건수", "description": "일정 금액 이상의 대량 체결 건수가 많은 종목의 순위를 조회합니다."},
        {"tr_id": "FHPST01740000", "title": "시가총액 상위", "description": "코스피/코스닥 시장의 시가총액 상위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01860000", "title": "당사매매 상위", "description": "한국투자증권 창구를 통한 순매수/순매도 상위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01800000", "title": "관심종목등록 상위", "description": "사용자들의 관심종목 등록 건수가 많은 상위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01680000", "title": "체결강도 상위", "description": "당일 체결강도(매수체결량/매도체결량)가 높은 상위 종목 순위를 조회합니다."},
        # --- 신규 추가 10개 ---
        {"tr_id": "FHPST01700000", "title": "등락률 순위", "description": "당일 주가 등락률 상위/하위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01720000", "title": "호가잔량 순위", "description": "매도/매수 호가 잔량이 많은 상위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01790000", "title": "시장가치 순위", "description": "PER/PBR/PCR/PSR 등 시장가치 지표 기준 순위를 조회합니다."},
        {"tr_id": "FHPST02340000", "title": "시간외 등락률 순위", "description": "장전/장후 시간외 단일가 기준 등락률 상위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST02350000", "title": "시간외 거래량 순위", "description": "장전/장후 시간외 거래량이 많은 상위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01760000", "title": "시간외 잔량 순위", "description": "장전/장후 시간외 매도/매수 호가 잔량 상위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01780000", "title": "이격도 순위", "description": "5/10/20/60/120일 이동평균 대비 이격도 상위/하위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01710000", "title": "거래량 순위", "description": "당일 거래량 및 거래량 증가율 기준 상위 종목 순위를 조회합니다."},
        {"tr_id": "FHPST01730000", "title": "수익자산지표 순위", "description": "매출이익/영업이익/당기순이익/자산총계 등 재무 수익자산지표 상위 순위를 조회합니다."},
        {"tr_id": "FHPST01750000", "title": "재무비율 순위", "description": "수익성/안정성/성장성/활동성 등 재무비율 기준 상위 종목 순위를 조회합니다."}
    ]

# KIS 12개 TR 수신 데이터 필드의 한글 필드명 및 데이터 타입 메타데이터 매핑 사전
TR_COLUMNS = {
    "FHPST01820000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "stck_sdpr", "name": "주식 기준가", "type": "price"},
        {"key": "askp", "name": "매도호가", "type": "price"},
        {"key": "bidp", "name": "매수호가", "type": "price"},
        {"key": "seln_rsqn", "name": "매도 잔량", "type": "integer"},
        {"key": "shnu_rsqn", "name": "매수 잔량", "type": "integer"},
        {"key": "cntg_vol", "name": "체결 거래량", "type": "integer"},
        {"key": "antc_tr_pbmn", "name": "체결 거래대금", "type": "integer"},
        {"key": "total_askp_rsqn", "name": "총 매도호가 잔량", "type": "integer"},
        {"key": "total_bidp_rsqn", "name": "총 매수호가 잔량", "type": "integer"}
    ],
    "FHKST17010000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "whol_loan_rmnd_stcn", "name": "전체 융자 잔고 주수", "type": "integer"},
        {"key": "whol_loan_rmnd_amt", "name": "전체 융자 잔고 금액(원)", "type": "integer"},
        {"key": "whol_loan_rmnd_rate", "name": "전체 융자 잔고 비율(%)", "type": "rate"},
        {"key": "whol_stln_rmnd_stcn", "name": "전체 대주 잔고 주수", "type": "integer"},
        {"key": "whol_stln_rmnd_amt", "name": "전체 대주 잔고 금액(원)", "type": "integer"},
        {"key": "whol_stln_rmnd_rate", "name": "전체 대주 잔고 비율(%)", "type": "rate"},
        {"key": "nday_vrss_loan_rmnd_inrt", "name": "N일 대비 융자 잔고 증가율(%)", "type": "rate"}
    ],
    "HHKDB13470100": [
        {"key": "record_date", "name": "기준일", "type": "date"},
        {"key": "per_sto_divi_amt", "name": "현금/주식배당금", "type": "integer"},
        {"key": "divi_rate", "name": "현금/주식배당률(%)", "type": "rate"},
        {"key": "divi_kind", "name": "배당종류", "type": "text"}
    ],
    "FHPST04820000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "acml_tr_pbmn", "name": "누적 거래 대금", "type": "integer"},
        {"key": "ssts_cntg_qty", "name": "공매도 체결 수량", "type": "integer"},
        {"key": "ssts_vol_rlim", "name": "공매도 거래량 비중(%)", "type": "rate"},
        {"key": "ssts_tr_pbmn", "name": "공매도 거래 대금", "type": "integer"},
        {"key": "ssts_tr_pbmn_rlim", "name": "공매도 거래대금 비중(%)", "type": "rate"}
    ],
    "HHMCM000100C0": [
        {"key": "mrkt_div_cls_code", "name": "시장구분", "type": "marketDiv"}
    ],
    "FHPST01870000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "askp", "name": "매도호가", "type": "price"},
        {"key": "askp_rsqn1", "name": "매도호가 잔량1", "type": "integer"},
        {"key": "bidp", "name": "매수호가", "type": "price"},
        {"key": "bidp_rsqn1", "name": "매수호가 잔량1", "type": "integer"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "new_hgpr", "name": "신 최고/최저가", "type": "price"},
        {"key": "hprc_near_rate", "name": "고가/저가 근접 비율(%)", "type": "rate"}
    ],
    "FHPST01770000": [
        {"key": "stck_prpr", "name": "보통주 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "보통주 전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "보통주 전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "보통주 누적 거래량", "type": "integer"},
        {"key": "prst_iscd", "name": "우선주 종목코드", "type": "text"},
        {"key": "prst_kor_isnm", "name": "우선주 한글 종목명", "type": "text"},
        {"key": "prst_prpr", "name": "우선주 현재가", "type": "price", "signKey": "prst_prdy_vrss_sign"},
        {"key": "prst_prdy_vrss", "name": "우선주 전일대비", "type": "price", "signKey": "prst_prdy_vrss_sign"},
        {"key": "prst_prdy_ctrt", "name": "우선주 전일 대비율", "type": "rate", "signKey": "prst_prdy_vrss_sign"},
        {"key": "prst_acml_vol", "name": "우선주 누적 거래량", "type": "integer"},
        {"key": "diff_prpr", "name": "보통주-우선주 가격차이", "type": "price"},
        {"key": "dprt", "name": "괴리율(%)", "type": "rate"}
    ],
    "FHKST190900C0": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "shnu_cntg_csnu", "name": "매수 체결 건수", "type": "integer"},
        {"key": "seln_cntg_csnu", "name": "매도 체결 건수", "type": "integer"},
        {"key": "ntby_cnqn", "name": "순매수 체결량", "type": "integer"}
    ],
    "FHPST01740000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "lstn_stcn", "name": "상장 주수", "type": "integer"},
        {"key": "stck_avls", "name": "시가 총액(억)", "type": "integer"},
        {"key": "mrkt_whol_avls_rlim", "name": "시장 전체 시가총액 비중(%)", "type": "rate"}
    ],
    "FHPST01860000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "acml_tr_pbmn", "name": "누적 거래 대금", "type": "integer"},
        {"key": "seln_cnqn_smtn", "name": "매도 체결량 합계", "type": "integer"},
        {"key": "shnu_cnqn_smtn", "name": "매수 체결량 합계", "type": "integer"},
        {"key": "ntby_cnqn", "name": "순매수 체결량", "type": "integer"}
    ],
    "FHPST01800000": [
        {"key": "mrkt_div_cls_name", "name": "시장 분류명", "type": "text"},
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "acml_tr_pbmn", "name": "누적 거래 대금", "type": "integer"},
        {"key": "askp", "name": "매도호가", "type": "price"},
        {"key": "bidp", "name": "매수호가", "type": "price"},
        {"key": "inter_issu_reg_csnu", "name": "관심 종목 등록 건수", "type": "integer"}
    ],
    "FHPST01680000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "tday_rltv", "name": "당일 체결강도(%)", "type": "rate"},
        {"key": "seln_cnqn_smtn", "name": "매도 체결량 합계", "type": "integer"},
        {"key": "shnu_cnqn_smtn", "name": "매수 체결량 합계", "type": "integer"}
    ],
    # --- 신규 추가 10개 ---
    "FHPST01700000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "acml_tr_pbmn", "name": "누적 거래 대금", "type": "integer"},
        {"key": "stck_hgpr", "name": "주식 최고가", "type": "price"},
        {"key": "stck_lwpr", "name": "주식 최저가", "type": "price"}
    ],
    "FHPST01720000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "total_askp_rsqn", "name": "총 매도호가 잔량", "type": "integer"},
        {"key": "total_bidp_rsqn", "name": "총 매수호가 잔량", "type": "integer"},
        {"key": "ntby_rsqn", "name": "순매수 잔량", "type": "integer"}
    ],
    "FHPST01790000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "per", "name": "PER", "type": "rate"},
        {"key": "pbr", "name": "PBR", "type": "rate"},
        {"key": "pcr", "name": "PCR", "type": "rate"},
        {"key": "psr", "name": "PSR", "type": "rate"}
    ],
    "FHPST02340000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "ovtm_untp_prpr", "name": "시간외 단일가 현재가", "type": "price"},
        {"key": "ovtm_untp_prdy_vrss", "name": "시간외 전일 대비", "type": "price"},
        {"key": "ovtm_untp_prdy_ctrt", "name": "시간외 등락률(%)", "type": "rate"}
    ],
    "FHPST02350000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "ovtm_vol", "name": "시간외 거래량", "type": "integer"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"}
    ],
    "FHPST01760000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "ovtm_total_askp_rsqn", "name": "시간외 총 매도호가 잔량", "type": "integer"},
        {"key": "ovtm_total_bidp_rsqn", "name": "시간외 총 매수호가 잔량", "type": "integer"},
        {"key": "mkob_otcp_vol", "name": "장개시전 시간외종가 거래량", "type": "integer"},
        {"key": "mkfa_otcp_vol", "name": "장종료후 시간외종가 거래량", "type": "integer"}
    ],
    "FHPST01780000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "d5_dsrt", "name": "5일 이격도", "type": "rate"},
        {"key": "d20_dsrt", "name": "20일 이격도", "type": "rate"},
        {"key": "d60_dsrt", "name": "60일 이격도", "type": "rate"}
    ],
    "FHPST01710000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "prdy_vol", "name": "전일 거래량", "type": "integer"},
        {"key": "vol_inrt", "name": "거래량 증가율(%)", "type": "rate"},
        {"key": "acml_tr_pbmn", "name": "누적 거래 대금", "type": "integer"}
    ],
    "FHPST01730000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "sale_totl_prfi", "name": "매출 총 이익(억)", "type": "integer"},
        {"key": "bsop_prti", "name": "영업 이익(억)", "type": "integer"},
        {"key": "thtr_ntin", "name": "당기순이익(억)", "type": "integer"},
        {"key": "total_aset", "name": "자산총계(억)", "type": "integer"}
    ],
    "FHPST01750000": [
        {"key": "stck_prpr", "name": "주식 현재가", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_vrss", "name": "전일 대비", "type": "price", "signKey": "prdy_vrss_sign"},
        {"key": "prdy_ctrt", "name": "전일 대비율", "type": "rate", "signKey": "prdy_vrss_sign"},
        {"key": "acml_vol", "name": "누적 거래량", "type": "integer"},
        {"key": "cptl_op_prfi", "name": "총자본경상이익률(%)", "type": "rate"},
        {"key": "sale_ntin_rate", "name": "매출액 순이익률(%)", "type": "rate"},
        {"key": "bis", "name": "자기자본비율(%)", "type": "rate"},
        {"key": "lblt_rate", "name": "부채비율(%)", "type": "rate"},
        {"key": "grs", "name": "매출액 증가율(%)", "type": "rate"}
    ]
}

# 파일 동시성 보호용 락
file_lock = asyncio.Lock()

@router.get("/market/ranking/fetch")
async def fetch_ranking(request: Request, tr_id: str):
    """KIS OpenAPI 12종 순위 분석 REST API를 호출하여 종목 목록을 조회합니다."""
    system = request.app.state.system
    token = await system.cred_provider.get_kis_access_token()
    if not token:
        raise HTTPException(status_code=401, detail="KIS 토큰 발급에 실패했습니다.")

    kis_config = system.config_manager.get('exchanges.kis', {})
    app_key = kis_config.get('app_key')
    app_secret = kis_config.get('app_secret')
    api_url = kis_config.get('api_url', 'https://openapi.koreainvestment.com:9443')

    url_map = {
        "FHPST01820000": "/uapi/domestic-stock/v1/ranking/exp-trans-updown",
        "FHKST17010000": "/uapi/domestic-stock/v1/ranking/credit-balance",
        "HHKDB13470100": "/uapi/domestic-stock/v1/ranking/dividend-rate",
        "FHPST04820000": "/uapi/domestic-stock/v1/ranking/short-sale",
        "HHMCM000100C0": "/uapi/domestic-stock/v1/ranking/hts-top-view",
        "FHPST01870000": "/uapi/domestic-stock/v1/ranking/near-new-highlow",
        "FHPST01770000": "/uapi/domestic-stock/v1/ranking/prefer-disparate-ratio",
        "FHKST190900C0": "/uapi/domestic-stock/v1/ranking/bulk-trans-num",
        "FHPST01740000": "/uapi/domestic-stock/v1/ranking/market-cap",
        "FHPST01860000": "/uapi/domestic-stock/v1/ranking/traded-by-company",
        "FHPST01800000": "/uapi/domestic-stock/v1/ranking/top-interest-stock",
        "FHPST01680000": "/uapi/domestic-stock/v1/ranking/volume-power",
        # 신규 추가 10개
        "FHPST01700000": "/uapi/domestic-stock/v1/ranking/fluctuation",
        "FHPST01720000": "/uapi/domestic-stock/v1/ranking/quote-balance",
        "FHPST01790000": "/uapi/domestic-stock/v1/ranking/market-value",
        "FHPST02340000": "/uapi/domestic-stock/v1/ranking/overtime-fluctuation",
        "FHPST02350000": "/uapi/domestic-stock/v1/ranking/overtime-volume",
        "FHPST01760000": "/uapi/domestic-stock/v1/ranking/after-hour-balance",
        "FHPST01780000": "/uapi/domestic-stock/v1/ranking/disparity",
        "FHPST01710000": "/uapi/domestic-stock/v1/quotations/volume-rank",
        "FHPST01730000": "/uapi/domestic-stock/v1/ranking/profit-asset-index",
        "FHPST01750000": "/uapi/domestic-stock/v1/ranking/finance-ratio",
    }

    if tr_id not in url_map:
        raise HTTPException(status_code=400, detail="지원하지 않는 TR_ID입니다.")

    url = f"{api_url}{url_map[tr_id]}"

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": str(app_key) if app_key is not None else "",
        "appsecret": str(app_secret) if app_secret is not None else "",
        "tr_id": tr_id,
        "custtype": "P"
    }

    today_str = datetime.date.today().strftime('%Y%m%d')

    params = {}
    if tr_id == "FHPST01820000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20182",
            "fid_input_iscd": "0000",
            "fid_rank_sort_cls_code": "0",
            "fid_div_cls_code": "0",
            "fid_aply_rang_prc_1": "",
            "fid_vol_cnt": "",
            "fid_pbmn": "",
            "fid_blng_cls_code": "0",
            "fid_mkop_cls_code": "0",
        }
    elif tr_id == "FHKST17010000":
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "11701",
            "FID_INPUT_ISCD": "0000",
            "FID_OPTION": "2",
            "FID_RANK_SORT_CLS_CODE": "0",
        }
    elif tr_id == "HHKDB13470100":
        params = {
            "CTS_AREA": "",
            "GB1": "0",
            "UPJONG": "0001",
            "GB2": "0",
            "GB3": "2",
            "F_DT": "20230101",
            "T_DT": today_str,
            "GB4": "0",
        }
    elif tr_id == "FHPST04820000":
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20482",
            "FID_INPUT_ISCD": "0000",
            "FID_PERIOD_DIV_CODE": "D",
            "FID_INPUT_CNT_1": "0",
            "FID_TRGT_EXLS_CLS_CODE": "",
            "FID_TRGT_CLS_CODE": "",
            "FID_APLY_RANG_PRC_1": "",
            "FID_APLY_RANG_PRC_2": "",
            "FID_APLY_RANG_VOL": "",
        }
    elif tr_id == "HHMCM000100C0":
        params = {}
    elif tr_id == "FHPST01870000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20187",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_prc_cls_code": "0",
            "fid_input_cnt_1": "1",
            "fid_input_cnt_2": "100",
            "fid_aply_rang_prc_1": "",
            "fid_aply_rang_prc_2": "",
            "fid_aply_rang_vol": "0",
        }
    elif tr_id == "FHPST01770000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20177",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_vol_cnt": "",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
        }
    elif tr_id == "FHKST190900C0":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "11909",
            "fid_input_iscd": "0000",
            "fid_rank_sort_cls_code": "0",
            "fid_div_cls_code": "0",
            "fid_input_price_1": "100000000",
            "fid_aply_rang_prc_1": "",
            "fid_aply_rang_prc_2": "",
            "fid_input_iscd_2": "",
            "fid_trgt_exls_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_vol_cnt": "",
        }
    elif tr_id == "FHPST01740000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20174",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
        }
    elif tr_id == "FHPST01860000":
        prev_trading_day = get_prev_trading_day()
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20186",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_RANK_SORT_CLS_CODE": "1",
            "FID_INPUT_DATE_1": prev_trading_day,
            "FID_INPUT_DATE_2": prev_trading_day,
            "FID_TRGT_CLS_CODE": "0",
            "FID_TRGT_EXLS_CLS_CODE": "0",
            "FID_APLY_RANG_VOL": "0",
            "FID_APLY_RANG_PRC_1": "",
            "FID_APLY_RANG_PRC_2": "",
        }
    elif tr_id == "FHPST01800000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20180",
            "fid_input_iscd": "0000",
            "fid_input_iscd_2": "000000",
            "fid_div_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_input_cnt_1": "1",
        }
    elif tr_id == "FHPST01680000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20168",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
        }
    # 신규 추가 10개 params
    elif tr_id == "FHPST01700000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20170",
            "fid_input_iscd": "0000",
            "fid_rank_sort_cls_code": "0",
            "fid_div_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_input_date_1": "",
        }
    elif tr_id == "FHPST01720000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20172",
            "fid_rank_sort_cls_code": "0",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
        }
    elif tr_id == "FHPST01790000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20179",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_rank_sort_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
        }
    elif tr_id == "FHPST02340000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20234",
            "fid_rank_sort_cls_code": "0",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
        }
    elif tr_id == "FHPST02350000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20235",
            "fid_rank_sort_cls_code": "0",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
        }
    elif tr_id == "FHPST01760000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20176",
            "fid_rank_sort_cls_code": "1",
            "fid_div_cls_code": "0",
            "fid_input_iscd": "0000",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
        }
    elif tr_id == "FHPST01780000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20178",
            "fid_div_cls_code": "0",
            "fid_rank_sort_cls_code": "0",
            "fid_hour_cls_code": "20",
            "fid_input_iscd": "0000",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
        }
    elif tr_id == "FHPST01710000":
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "000000",
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "",
        }
    elif tr_id == "FHPST01730000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20173",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_input_option_1": str(datetime.date.today().year - 1),
            "fid_input_option_2": "3",
            "fid_rank_sort_cls_code": "0",
            "fid_blng_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_trgt_cls_code": "0",
        }
    elif tr_id == "FHPST01750000":
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20175",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_input_option_1": str(datetime.date.today().year - 1),
            "fid_input_option_2": "3",
            "fid_rank_sort_cls_code": "7",
            "fid_blng_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_trgt_cls_code": "0",
        }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"KIS ranking API Error: {resp.status} - {text}")
                    raise HTTPException(status_code=resp.status, detail=f"KIS API 오류: {text}")

                data = await resp.json()
                if data.get('rt_cd') != '0':
                    raise HTTPException(status_code=400, detail=f"KIS API 에러: {data.get('msg1')}")

                raw_results = []
                if tr_id == "FHKST17010000":
                    raw_results = data.get('output2', [])
                elif tr_id == "HHMCM000100C0":
                    raw_results = data.get('output1', [])
                elif tr_id == "HHKDB13470100":
                    raw_results = data.get('output', data.get('output1', []))
                elif tr_id in ("FHPST02340000", "FHPST02350000"):
                    # 시간외 관련 API는 종목 목록이 output2에 위치
                    raw_results = data.get('output2', [])
                else:
                    raw_results = data.get('output', [])

                raw_results = raw_results[:30]
                processed = []
                kis_symbols = stock_mapper._mapping.get('kis', {})

                for item in raw_results:
                    code = ""
                    if tr_id == "FHPST01820000":
                        code = item.get('stck_shrn_iscd', '')
                    elif tr_id == "FHKST17010000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "HHKDB13470100":
                        code = item.get('sht_cd', '')
                    elif tr_id == "FHPST04820000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "HHMCM000100C0":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01870000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01770000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHKST190900C0":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01740000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01860000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01800000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01680000":
                        code = item.get('stck_shrn_iscd', '')
                    # 신규 추가 10개 code 추출
                    elif tr_id == "FHPST01700000":
                        code = item.get('stck_shrn_iscd', '')
                    elif tr_id == "FHPST01720000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01790000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST02340000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST02350000":
                        # 시간외거래량 output2는 stck_shrn_iscd 사용
                        code = item.get('stck_shrn_iscd', '') or item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01760000":
                        code = item.get('stck_shrn_iscd', '')
                    elif tr_id == "FHPST01780000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01710000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01730000":
                        code = item.get('mksc_shrn_iscd', '')
                    elif tr_id == "FHPST01750000":
                        code = item.get('mksc_shrn_iscd', '')

                    code = code.strip() if code else ""
                    if not code:
                        continue

                    name = ""
                    if tr_id == "FHPST01820000":
                        name = item.get('hts_kor_isnm', '')
                    elif tr_id == "FHKST17010000":
                        name = item.get('hts_kor_isnm', '')
                    elif tr_id == "HHKDB13470100":
                        name = item.get('isin_name', '')
                    elif tr_id == "FHPST04820000":
                        name = item.get('hts_kor_isnm', '')
                    elif tr_id == "HHMCM000100C0":
                        name = stock_mapper.get_name('kis', code)
                    elif tr_id == "FHPST01870000":
                        name = item.get('hts_kor_isnm', '')
                    elif tr_id == "FHPST01770000":
                        name = item.get('hts_kor_isnm', '')
                    elif tr_id == "FHKST190900C0":
                        name = item.get('hts_kor_isnm', '')
                    elif tr_id == "FHPST01740000":
                        name = item.get('hts_kor_isnm', '')
                    elif tr_id == "FHPST01860000":
                        name = item.get('hts_kor_isnm', '')
                    elif tr_id == "FHPST01800000":
                        name = item.get('hts_kor_isnm', '')
                    elif tr_id == "FHPST01680000":
                        name = item.get('hts_kor_isnm', '')
                    # 신규 추가 10개 name 추출 (모두 hts_kor_isnm)
                    elif tr_id in (
                        "FHPST01700000", "FHPST01720000", "FHPST01790000",
                        "FHPST02340000", "FHPST02350000", "FHPST01760000",
                        "FHPST01780000", "FHPST01710000", "FHPST01730000", "FHPST01750000"
                    ):
                        name = item.get('hts_kor_isnm', '')

                    name = name.strip() if name else code

                    # 새로운 종목 발견 시 DB 및 메모리에 비동기 추가
                    if code not in kis_symbols:
                        await stock_mapper.add_mapping_async('kis', code, name, system.db_path)

                    processed.append({
                        "code": code,
                        "name": name,
                        "is_collected": code in kis_symbols,
                        "raw": item
                    })

                return {
                    "columns": TR_COLUMNS.get(tr_id, []),
                    "data": processed
                }
    except aiohttp.ClientError as e:
        logger.error(f"Network error calling KIS ranking {tr_id}: {e}")
        raise HTTPException(status_code=500, detail=f"네트워크 오류: {str(e)}")

@router.post("/market/symbols/kis/toggle")
async def toggle_kis_symbol(request: Request, body: dict):
    """KIS 수집 종목을 토글하고 DB에 동기화한 뒤 ZMQ IPC 메시지를 퍼블리시합니다."""
    code = body.get("code")
    name = body.get("name")
    if not code or not name:
        raise HTTPException(status_code=400, detail="종목 코드(code)와 종목명(name)이 필요합니다.")

    system = request.app.state.system
    db_path = system.db_path

    async with file_lock:
        from src.database.connection import get_db_conn
        
        async with get_db_conn(db_path) as db:
            # 1. asset_master 에 종목이 존재하는지 확인하고 없으면 등록
            await db.execute('''
                INSERT OR IGNORE INTO asset_master (symbol, korean_name, asset_type)
                VALUES (?, ?, 'stock')
            ''', (code, name))
            
            # 2. exchange_assets 에 해당 종목이 존재하는지 확인
            async with db.execute('''
                SELECT is_active FROM exchange_assets 
                WHERE exchange = 'kis' AND symbol = ?
            ''', (code,)) as cursor:
                row = await cursor.fetchone()
                
            if row is not None:
                # 존재하면 is_active 플래그 반전
                current_active = row['is_active']
                new_status_val = 0 if current_active == 1 else 1
                await db.execute('''
                    UPDATE exchange_assets SET is_active = ?, updated_at = datetime('now')
                    WHERE exchange = 'kis' AND symbol = ?
                ''', (new_status_val, code))
                new_status = (new_status_val == 1)
            else:
                # 존재하지 않으면 is_active=1 로 추가
                await db.execute('''
                    INSERT INTO exchange_assets (exchange, symbol, is_active)
                    VALUES ('kis', ?, 1)
                ''', (code,))
                new_status = True
                
            await db.commit()
            
        # 3. StockMapper 메모리 캐시 리로드
        await stock_mapper.load_from_db(db_path)

    # ZMQ IPC 메시지 발행
    publisher = getattr(request.app.state, 'control_publisher', None)
    if publisher:
        try:
            msg = {
                "type": "update_symbols",
                "exchange": "kis",
                "code": code,
                "name": name,
                "is_collected": new_status
            }
            await publisher.publish("collector_control", msg)
            logger.info(f"[Web Market Router] ZMQ IPC control message published: {msg}")
        except Exception as e:
            logger.error(f"[Web Market Router] Failed to publish ZMQ message: {e}")

    return {
        "success": True,
        "code": code,
        "is_collected": new_status
    }

@router.post("/market/sync-assets")
async def api_sync_assets(request: Request):
    """
    거래소 API 전체 종목 정보를 조회하여 DB와 메모리 캐시를 수동으로 동기화합니다.
    """
    system = request.app.state.system
    db_path = system.db_path
    
    # 동기화 작업을 파일 락 안전하게 수행
    async with file_lock:
        from src.database.sync_assets import sync_exchange_assets
        try:
            logger.info("[Web API] 어드민 요청으로 거래소 자산 동기화(sync_exchange_assets)를 수동 구동합니다.")
            results = await sync_exchange_assets(db_path)
            
            # StockMapper 메모리 캐시 최신화
            from src.engine.utils.stock_mapper import stock_mapper
            await stock_mapper.load_from_db(db_path)
            
            # 수집기 데몬에게 ZMQ IPC 제어 신호를 보내 구독 리스트 리로드 지시 (KIS 등)
            publisher = getattr(request.app.state, 'control_publisher', None)
            if publisher:
                msg = {
                    "type": "update_symbols",
                    "exchange": "all",
                    "code": "all",
                    "name": "all",
                    "is_collected": True
                }
                await publisher.publish("collector_control", msg)
                logger.info("[Web API] ZMQ IPC control message for full sync published.")
                
            return {"success": True, "message": "거래소 자산 동기화 성공", "results": results}
        except Exception as e:
            logger.error(f"[Web API] 수동 자산 동기화 중 에러 발생: {e}")
            raise HTTPException(status_code=500, detail=f"동기화 실패: {str(e)}")




