from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from typing import Set, Optional, Dict, List
import shutil
import os
import asyncio
import json
import time
import aiohttp
import aiosqlite
from collections import deque
from src.engine.candles import CandleGenerator
from src.engine.indicators import IndicatorCalculator
from src.engine.strategy import StrategyRegistry
from src.engine.loader import load_dynamic_strategies, unload_strategy

app = FastAPI()
# Trigger reload for new strategies

# 전역 상태 관리
class ConnectionManager:
    """WebSocket 연결 및 종목별 구독을 관리합니다."""
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: Dict[WebSocket, str] = {}  # ws -> 구독 중인 종목

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        self.subscriptions.pop(websocket, None)

    def subscribe(self, websocket: WebSocket, symbol: str):
        """클라이언트가 특정 종목을 구독합니다."""
        self.subscriptions[websocket] = symbol

    async def broadcast(self, message: dict):
        """해당 종목을 구독 중인 클라이언트에게만 전송합니다."""
        symbol = message.get('code', '')
        msg_str = json.dumps(message)
        targets = [ws for ws, sub in self.subscriptions.items() if sub == symbol]
        if targets:
            await asyncio.gather(
                *[ws.send_text(msg_str) for ws in targets],
                return_exceptions=True
            )

    async def broadcast_global(self, message: dict):
        """종목 구독 여부와 상관없이 모든 연결된 클라이언트에게 전송합니다."""
        msg_str = json.dumps(message)
        if self.active_connections:
            await asyncio.gather(
                *[ws.send_text(msg_str) for ws in self.active_connections],
                return_exceptions=True
            )

manager = ConnectionManager()
DB_PATH = os.path.join(os.getcwd(), 'data', 'backtest.db')

# 전략별 현재 파라미터 설정 저장 (ID -> Params)
strategy_configs: Dict[str, Dict] = {
    "rsistrategy": {"rsi_window": 14, "buy_threshold": 30.0, "sell_threshold": 70.0, "enabled": True},
    "macdstrategy": {"fast_period": 12, "slow_period": 26, "signal_period": 9, "enabled": True},
    "volumepowerstrategy": {"buy_threshold": 120.0, "sell_threshold": 80.0, "enabled": True}
}

# DB 쓰기 큐 (수집 루프와 DB 저장 분리)
db_queue: asyncio.Queue = asyncio.Queue()

# 수집 가능한 전체 종목 코드
available_symbols: list = []

class SpikeDetector:
    def __init__(self, window_sec=60, price_threshold=1.5, vol_multiplier=3.0, buy_ratio_threshold=0.7):
        self.windows: Dict[str, deque] = {} # symbol -> deque of ticks
        self.window_sec = window_sec
        self.price_threshold = price_threshold
        self.vol_multiplier = vol_multiplier
        self.buy_ratio_threshold = buy_ratio_threshold
        self.last_alert_time: Dict[str, Dict[str, float]] = {} # symbol -> type -> last alert timestamp
        self.alert_cooldown = 300 # 5분 (300초)
        
        # 추가 설정
        self.rsi_buy_threshold = 30.0
        self.rsi_sell_threshold = 70.0
        self.enabled_alerts = {
            "spike": True,
            "volume": True,
            "rsi": True,
            "cross": True
        }

    def process_tick(self, tick: dict, indicators: Optional[dict] = None):
        symbol = tick['code']
        now = tick['trade_timestamp'] / 1000 # ms to sec
        
        if symbol not in self.windows:
            self.windows[symbol] = deque()
            self.last_alert_time[symbol] = {}
            
        window = self.windows[symbol]
        window.append(tick)
        
        # 윈도우 시간 지난 데이터 제거
        while window and (now - (window[0]['trade_timestamp'] / 1000)) > self.window_sec:
            window.popleft()
            
        if len(window) < 15: return None 
        
        alerts = []

        # 1. 가격 급등 (Spike)
        if self.enabled_alerts.get("spike"):
            start_price = window[0]['trade_price']
            curr_price = tick['trade_price']
            price_change = (curr_price - start_price) / start_price * 100
            
            total_vol = sum(t['trade_volume'] for t in window)
            buy_vol = sum(t['trade_volume'] for t in window if t['ask_bid'] == 'BID')
            buy_ratio = buy_vol / total_vol if total_vol > 0 else 0

            if price_change >= self.price_threshold and buy_ratio >= self.buy_ratio_threshold:
                if now - self.last_alert_time[symbol].get("spike", 0) > self.alert_cooldown:
                    self.last_alert_time[symbol]["spike"] = now
                    alerts.append({
                        "type": "alert",
                        "alert_type": "spike",
                        "code": symbol,
                        "price": curr_price,
                        "change": round(price_change, 2),
                        "buy_ratio": round(buy_ratio * 100, 1),
                        "msg": f"🚀 급등 포착: {symbol} ({price_change:+.2f}%)"
                    })

        # 2. 거래량 폭증 (Volume)
        if self.enabled_alerts.get("volume"):
            total_vol = sum(t['trade_volume'] for t in window)
            avg_vol_per_sec = total_vol / self.window_sec
            recent_10s_ticks = [t for t in window if (now - (t['trade_timestamp']/1000)) <= 10]
            if recent_10s_ticks:
                recent_avg_vol_per_sec = sum(t['trade_volume'] for t in recent_10s_ticks) / 10
                if avg_vol_per_sec > 0 and recent_avg_vol_per_sec > (avg_vol_per_sec * self.vol_multiplier):
                    if now - self.last_alert_time[symbol].get("volume", 0) > self.alert_cooldown:
                        self.last_alert_time[symbol]["volume"] = now
                        alerts.append({
                            "type": "alert",
                            "alert_type": "volume",
                            "code": symbol,
                            "price": tick['trade_price'],
                            "msg": f"📊 거래량 폭증: {symbol} ({recent_avg_vol_per_sec/avg_vol_per_sec:.1f}배)"
                        })

        # 3. 지표 기반 알림 (RSI)
        if indicators and self.enabled_alerts.get("rsi"):
            rsi = indicators.get('rsi')
            if rsi:
                if rsi <= self.rsi_buy_threshold:
                    if now - self.last_alert_time[symbol].get("rsi_buy", 0) > self.alert_cooldown:
                        self.last_alert_time[symbol]["rsi_buy"] = now
                        alerts.append({ "type": "alert", "alert_type": "rsi", "code": symbol, "price": tick['trade_price'], "msg": f"📉 RSI 과매도: {symbol} ({rsi:.1f})" })
                elif rsi >= self.rsi_sell_threshold:
                    if now - self.last_alert_time[symbol].get("rsi_sell", 0) > self.alert_cooldown:
                        self.last_alert_time[symbol]["rsi_sell"] = now
                        alerts.append({ "type": "alert", "alert_type": "rsi", "code": symbol, "price": tick['trade_price'], "msg": f"📈 RSI 과매수: {symbol} ({rsi:.1f})" })

        return alerts[0] if alerts else None

async def save_alert(alert: dict):
    """알림을 DB에 영구 저장합니다."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO alerts (symbol, price, change, buy_ratio, msg, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (alert['code'], alert['price'], alert.get('change', 0), alert.get('buy_ratio', 0), alert['msg'], int(time.time() * 1000))
            )
            await db.commit()
    except Exception as e:
        print(f"[ERROR] Alert Save Error: {e}")

spike_detector = SpikeDetector()

# 수집기 태스크 관리
class CollectorManager:
    def __init__(self):
        self.task: Optional[asyncio.Task] = None
        self.is_running = False
        self.candle_generators: Dict[str, CandleGenerator] = {}
        self.active_strategies: Dict[str, Dict[str, BaseStrategy]] = {}

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.is_running = False
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def run(self):
        global available_symbols

        # Upbit REST API에서 전체 KRW 마켓 종목 조회
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.upbit.com/v1/market/all") as resp:
                    markets = await resp.json()
                    available_symbols = sorted([
                        m['market'] for m in markets
                        if m['market'].startswith('KRW-')
                    ])
                    print(f"[INFO] {len(available_symbols)}개 KRW 종목 로드 완료")
                    self.candle_generators = {symbol: CandleGenerator(intervals=[60]) for symbol in available_symbols}
                    self.active_strategies = {symbol: {} for symbol in available_symbols}
        except Exception as e:
            print(f"[ERROR] 종목 목록 조회 실패: {e}")
            if not available_symbols:
                available_symbols = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]
                self.candle_generators = {symbol: CandleGenerator(intervals=[60]) for symbol in available_symbols}
                self.active_strategies = {symbol: {} for symbol in available_symbols}

        # 실시간 지표 계산기 딕셔너리
        self.indicator_calculators: Dict[str, IndicatorCalculator] = {
            symbol: IndicatorCalculator(window_size=20) for symbol in available_symbols
        }

        print("[INFO] 전략 인스턴스 워밍업 시작 (최근 1000개 틱)...")
        enabled_configs = {sid: cfg for sid, cfg in strategy_configs.items() if cfg.get('enabled', True)}
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                for symbol in available_symbols:
                    # 전략 인스턴스화
                    for sid, cfg in enabled_configs.items():
                        strategy = StrategyRegistry.create_strategy(sid, cfg.get('params', {}))
                        if strategy:
                            self.active_strategies[symbol][sid] = strategy

                    # DB에서 과거 틱 데이터 로드하여 캔들 채우기
                    async with db.execute("SELECT trade_price, trade_volume, ask_bid, trade_timestamp FROM trades WHERE symbol = ? ORDER BY trade_timestamp DESC LIMIT 1000", (symbol,)) as cursor:
                        rows = await cursor.fetchall()
                        for row in reversed(rows):
                            closed_candles = self.candle_generators[symbol].process_tick(
                                symbol, row['trade_price'], row['trade_volume'], row['ask_bid'], row['trade_timestamp']
                            )
                            for candle in closed_candles:
                                for strategy in self.active_strategies[symbol].values():
                                    strategy.on_candle(candle)
            print("[INFO] 전략 워밍업 완료")
        except Exception as e:
            print(f"[WARNING] 워밍업 중 오류 발생 (데이터가 없을 수 있습니다): {e}")

        url = "wss://api.upbit.com/websocket/v1"
        subscribe_data = [
            {"ticket": "collector"},
            {"type": "trade", "codes": available_symbols}
        ]

        while self.is_running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(url) as ws:
                        await ws.send_json(subscribe_data)
                        print(f"[INFO] Collector Started - {len(available_symbols)}개 종목 구독 중")

                        async for msg in ws:
                            if not self.is_running: break
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                data = json.loads(msg.data.decode('utf-8'))

                                # 1. 구독 중인 클라이언트에게 브로드캐스트
                                await manager.broadcast(data)

                                # 2. 실시간 지표 계산
                                symbol = data['code']
                                indicators = None
                                if symbol in self.indicator_calculators:
                                    indicators = self.indicator_calculators[symbol].update(data['trade_price'])

                                # 3. 급등/지표 탐지 및 글로벌 알림
                                alert = spike_detector.process_tick(data, indicators)
                                if alert:
                                    await manager.broadcast_global(alert)
                                    asyncio.create_task(save_alert(alert))

                                # 4. DB 큐에 삽입
                                await db_queue.put(data)
                                
                                # 5. 전략 엔진 (실시간 캔들 생성 및 매매 평가)
                                closed_candles = self.candle_generators[symbol].process_tick(
                                    symbol, data['trade_price'], data['trade_volume'], data['ask_bid'], data['trade_timestamp']
                                )
                                for candle in closed_candles:
                                    # 설정(동적 토글) 반영하여 전략 객체 관리
                                    for sid, cfg in strategy_configs.items():
                                        if cfg.get('enabled', True):
                                            if sid not in self.active_strategies[symbol]:
                                                strategy = StrategyRegistry.create_strategy(sid, cfg.get('params', {}))
                                                if strategy: self.active_strategies[symbol][sid] = strategy
                                            else:
                                                self.active_strategies[symbol][sid].update_params(cfg.get('params', {}))
                                            
                                            if sid in self.active_strategies[symbol]:
                                                result = self.active_strategies[symbol][sid].on_candle(candle)
                                                if result and result.action in ["BUY", "SELL"]:
                                                    asyncio.create_task(simulate_trade_from_engine(symbol, result.action, result.price or candle.close, result.reason))
                                        else:
                                            # 비활성화 시 즉시 메모리에서 제거
                                            if sid in self.active_strategies[symbol]:
                                                del self.active_strategies[symbol][sid]
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.is_running:
                    print(f"[ERROR] Collector Error: {e}. Reconnecting...")
                    await asyncio.sleep(5)

collector = CollectorManager()

# DB Writer 백그라운드 태스크
async def db_writer_loop():
    """Queue에서 데이터를 꺼내 배치 단위로 DB에 저장합니다."""
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                while True:
                    buffer = []
                    try:
                        # 최대 500건 또는 1초 타임아웃까지 수집
                        while len(buffer) < 500:
                            item = await asyncio.wait_for(db_queue.get(), timeout=1.0)
                            buffer.append((
                                item['code'],
                                item['trade_price'],
                                item['trade_volume'],
                                item['ask_bid'],
                                item['trade_timestamp']
                            ))
                            db_queue.task_done()
                    except asyncio.TimeoutError:
                        pass

                    if buffer:
                        await db.executemany(
                            "INSERT INTO trades (symbol, trade_price, trade_volume, ask_bid, trade_timestamp) VALUES (?, ?, ?, ?, ?)",
                            buffer
                        )
                        await db.commit()
                        # 다른 비동기 태스크(API 등)에 제어권을 양보합니다.
                        await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            # 종료 시 잔여 데이터 플러시
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    buffer = []
                    while not db_queue.empty():
                        item = db_queue.get_nowait()
                        buffer.append((
                            item['code'], item['trade_price'],
                            item['trade_volume'], item['ask_bid'],
                            item['trade_timestamp']
                        ))
                    if buffer:
                        await db.executemany(
                            "INSERT INTO trades (symbol, trade_price, trade_volume, ask_bid, trade_timestamp) VALUES (?, ?, ?, ?, ?)",
                            buffer
                        )
                        await db.commit()
                        print(f"[INFO] DB Writer: 잔여 {len(buffer)}건 플러시 완료")
            except Exception:
                pass
            break
        except Exception as e:
            print(f"[ERROR] DB Writer Error: {e}")
            await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    # DB 초기화
    if not os.path.exists(os.path.dirname(DB_PATH)):
        os.makedirs(os.path.dirname(DB_PATH))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL") # 동시 읽기/쓰기 성능 향상
        await db.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, trade_price REAL, trade_volume REAL, ask_bid TEXT, trade_timestamp INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, price REAL, change REAL, buy_ratio REAL, msg TEXT, timestamp INTEGER)")
        await db.commit()
    # DB Writer 백그라운드 태스크 시작
    app.state.db_writer = asyncio.create_task(db_writer_loop())

@app.on_event("shutdown")
async def shutdown_event():
    print("[INFO] Shutting down...")
    await collector.stop()
    app.state.db_writer.cancel()
    try:
        await app.state.db_writer
    except asyncio.CancelledError:
        pass
    print("[INFO] Shutdown complete.")

@app.get("/market")
async def get_market():
    """전체 KRW 마켓 종목 정보(한글명, 현재가, 변동률, 거래대금)를 반환합니다."""
    try:
        async with aiohttp.ClientSession() as session:
            # 1. 종목 기본 정보 (한글명 포함)
            async with session.get("https://api.upbit.com/v1/market/all?is_details=false") as resp:
                all_markets = await resp.json()
            krw_markets = [m for m in all_markets if m['market'].startswith('KRW-')]
            market_codes = [m['market'] for m in krw_markets]

            # 2. 현재가 조회 (100개씩 배치)
            tickers = []
            for i in range(0, len(market_codes), 100):
                batch = ','.join(market_codes[i:i+100])
                async with session.get(f"https://api.upbit.com/v1/ticker?markets={batch}") as resp:
                    tickers.extend(await resp.json())

            # 3. 데이터 병합 후 거래대금 내림차순 정렬
            ticker_map = {t['market']: t for t in tickers}
            result = []
            for m in krw_markets:
                code = m['market']
                t = ticker_map.get(code, {})
                result.append({
                    'market': code,
                    'korean_name': m.get('korean_name', ''),
                    'english_name': m.get('english_name', ''),
                    'trade_price': t.get('trade_price', 0),
                    'signed_change_rate': t.get('signed_change_rate', 0),
                    'signed_change_price': t.get('signed_change_price', 0),
                    'acc_trade_price_24h': t.get('acc_trade_price_24h', 0),
                    'high_price': t.get('high_price', 0),
                    'low_price': t.get('low_price', 0),
                })
            result.sort(key=lambda x: x['acc_trade_price_24h'], reverse=True)
            return result
    except Exception as e:
        return {"error": str(e)}

@app.get("/symbols")
async def get_symbols():
    """수집 가능한 전체 KRW 종목 목록을 반환합니다."""
    if available_symbols:
        return available_symbols
    # 아직 수집기가 시작되지 않았으면 직접 조회
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.upbit.com/v1/market/all") as resp:
                markets = await resp.json()
                return sorted([m['market'] for m in markets if m['market'].startswith('KRW-')])
    except Exception:
        return ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]

@app.get("/collector/status")
async def get_status():
    return {"is_running": collector.is_running}

@app.post("/collector/start")
async def start_collector():
    await collector.start()
    return {"message": "Collector started"}

@app.post("/collector/stop")
async def stop_collector():
    await collector.stop()
    return {"message": "Collector stopped"}

@app.get("/api/strategies")
async def list_strategies():
    """사용 가능한 모든 전략 목록과 메타데이터를 반환합니다."""
    metadata = StrategyRegistry.get_all_metadata()
    # 현재 설정값(configs)을 메타데이터에 병합
    for m in metadata:
        s_id = m['id']
        if s_id in strategy_configs:
            for p_name, p_val in strategy_configs[s_id].items():
                if p_name == 'enabled':
                    m['enabled'] = p_val
                elif p_name in m['params']:
                    m['params'][p_name]['current'] = p_val
        else:
            # 기본값 설정
            m['enabled'] = True
    return metadata

@app.post("/api/strategies/{strategy_id}/params")
async def update_strategy_params(strategy_id: str, params: Dict):
    """특정 전략의 파라미터를 업데이트합니다."""
    s_id = strategy_id.lower()
    if s_id not in strategy_configs:
        strategy_configs[s_id] = {}
    
    # 전달받은 파라미터 업데이트
    strategy_configs[s_id].update(params)
    
    return {"message": f"Strategy {strategy_id} parameters updated", "current_params": strategy_configs[s_id]}

@app.delete("/api/strategies/{strategy_id}")
async def disable_strategy(strategy_id: str):
    """특정 전략을 비활성화(사용 안함) 처리합니다."""
    s_id = strategy_id.lower()
    if s_id not in strategy_configs:
        strategy_configs[s_id] = {}
    strategy_configs[s_id]['enabled'] = False
    return {"message": f"Strategy {strategy_id} disabled", "status": "disabled"}

@app.post("/api/strategies/{strategy_id}/enable")
async def enable_strategy(strategy_id: str):
    """특정 전략을 활성화합니다."""
    s_id = strategy_id.lower()
    if s_id not in strategy_configs:
        strategy_configs[s_id] = {}
    strategy_configs[s_id]['enabled'] = True
    return {"message": f"Strategy {strategy_id} enabled", "status": "enabled"}

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 동적 전략을 로드합니다."""
    strategies_dir = os.path.join(os.getcwd(), 'src', 'engine', 'strategies')
    count = load_dynamic_strategies(strategies_dir)
    print(f"[INFO] {count} strategies loaded on startup.")

@app.get("/test-alert")
async def test_alert(symbol: str = "KRW-BTC"):
    """UI 확인용 테스트 알림을 강제로 발생시킵니다."""
    mock_alert = {
        "type": "alert",
        "code": symbol,
        "price": 100000000,
        "change": 5.23,
        "buy_ratio": 88.5,
        "msg": f"🚀 [TEST] 급등 포착: {symbol} (+5.23%)"
    }
    await manager.broadcast_global(mock_alert)
    await save_alert(mock_alert)
    return {"message": f"Test alert for {symbol} sent to all clients and saved"}

@app.get("/alerts")
async def get_alerts(limit: int = 50):
    """최근 알림 기록을 반환합니다."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

@app.delete("/api/alerts")
async def clear_alerts():
    """모든 알림 기록을 삭제합니다."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM alerts")
        await db.commit()
    return {"message": "모든 알림 기록이 삭제되었습니다."}

@app.get("/candles")
async def get_candles(symbol: str = "KRW-BTC", interval: int = 60, limit: int = 5000, start_ts: int = None, end_ts: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT trade_price, trade_volume, ask_bid, trade_timestamp FROM trades WHERE symbol = ?"
        params = [symbol]
        
        if start_ts and end_ts:
            query += " AND trade_timestamp BETWEEN ? AND ?"
            params.extend([start_ts, end_ts])
            query += " ORDER BY trade_timestamp ASC"
        else:
            query += " ORDER BY trade_timestamp DESC LIMIT ?"
            params.append(limit if limit > 5000 else 10000) # 기본 1만개로 상향
            
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            # LIMIT 쿼리의 경우 결과를 다시 시간순으로 정렬
            if not (start_ts and end_ts):
                ticks = sorted(rows, key=lambda x: x['trade_timestamp'])
            else:
                ticks = rows
                
            generator = CandleGenerator(intervals=[interval])
            all_candles = []
            for row in ticks:
                # ask_bid 정보를 함께 전달
                closed = generator.process_tick(symbol, row['trade_price'], row['trade_volume'], row['ask_bid'], row['trade_timestamp'])
                all_candles.extend(closed)
            
            # 실시간 진행 중인 캔들 추가 (범위 조회가 아닐 때만)
            if not (start_ts and end_ts):
                current = generator.get_current_candle(symbol, interval)
                if current: all_candles.append(current)
            
            # 기술 지표 계산
            if len(all_candles) > 0:
                df = IndicatorCalculator.calculate_all_indicators(all_candles)
                df = df.replace({float('nan'): None})
                return df.to_dict(orient='records')
            return []

@app.get("/trades")
async def get_trades(symbol: str = "KRW-BTC", limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT trade_price, trade_volume, ask_bid, trade_timestamp FROM trades WHERE symbol = ? ORDER BY trade_timestamp DESC LIMIT ?", (symbol, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

@app.post("/data/cleanup")
async def cleanup_data(date: str):
    """지정된 날짜 이전의 데이터를 삭제합니다."""
    import datetime
    try:
        # ISO 날짜를 타임스탬프(ms)로 변환
        dt = datetime.datetime.fromisoformat(date)
        ts = int(dt.timestamp() * 1000)
        
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("DELETE FROM trades WHERE trade_timestamp < ?", (ts,))
            deleted_count = cursor.rowcount
            await db.commit()
            return {"message": f"{deleted_count}개의 데이터가 삭제되었습니다.", "count": deleted_count}
    except Exception as e:
        return {"message": f"삭제 실패: {str(e)}", "count": 0}

@app.post("/trade/simulate")
async def simulate_trade(order: dict):
    """프론트엔드에서 강제 트리거하는 시뮬레이션용 (레거시 지원)"""
    symbol = order.get('symbol')
    side = order.get('side')
    price = order.get('price')
    amount = order.get('amount')
    print(f"[TRADE SIM] {side} {symbol} at {price} (Qty: {amount/price:.4f})")
    return {"status": "success", "message": f"{side} 주문 완료", "data": order}

async def simulate_trade_from_engine(symbol: str, side: str, price: float, reason: str):
    """엔진에서 실시간 전략으로 발생한 모의 체결을 브로드캐스트합니다."""
    print(f"[ENGINE TRADE] {side} {symbol} at {price} (Reason: {reason})")
    trade_alert = {
        "type": "alert",
        "alert_type": "trade",
        "code": symbol,
        "price": price,
        "msg": f"🤖 [전략매매] {side} 주문: {symbol} ({reason})"
    }
    await manager.broadcast_global(trade_alert)
    await save_alert(trade_alert)

@app.post("/settings/alerts")
async def update_alert_settings(settings: dict):
    spike_detector.update_settings(settings)
    return {"message": "알림 설정이 업데이트되었습니다."}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                msg = json.loads(text)
                # 클라이언트가 구독할 종목을 지정
                if 'subscribe' in msg:
                    manager.subscribe(websocket, msg['subscribe'])
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if not os.path.exists("frontend"): os.makedirs("frontend")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
