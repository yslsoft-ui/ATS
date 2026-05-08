import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { createChart, CandlestickSeries, LineSeries } from 'lightweight-charts';
import { Activity, BarChart2, Settings, Globe } from 'lucide-react';

const MENU_ITEMS = [
  { id: 'dashboard', icon: <Activity size={20} />, label: '실시간 모니터링' },
  { id: 'market', icon: <Globe size={20} />, label: '마켓 정보' },
  { id: 'backtest', icon: <BarChart2 size={20} />, label: '백테스트' },
  { id: 'settings', icon: <Settings size={20} />, label: '설정' },
];

// 에러 방어막이 강화된 캔들 차트 컴포넌트
const CandleChart = ({ symbol, interval }) => {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const [chartError, setChartError] = useState(null);

  useEffect(() => {
    let chart;
    let timer;

    const initChart = () => {
      try {
        if (!containerRef.current) return;
        
        chart = createChart(containerRef.current, {
          layout: { background: { color: '#1E1E1E' }, textColor: '#D1D4DC' },
          grid: { vertLines: { color: '#2A2E39' }, horzLines: { color: '#2A2E39' } },
          width: containerRef.current.clientWidth || 800,
          height: 400,
          timeScale: {
            timeVisible: true,
            secondsVisible: true,
            borderColor: '#2A2E39',
            barSpacing: 10, // 캔들 간격을 조금 더 넓게 설정하여 가독성 향상
          },
          localization: {
            locale: 'ko-KR',
            priceFormatter: price => price.toLocaleString() + ' ₩',
          },
        });

        // v5.0 API: addSeries 사용
        const series = chart.addSeries(CandlestickSeries, {
          upColor: '#FF4D4D', downColor: '#4D94FF',
          borderVisible: false, wickUpColor: '#FF4D4D', wickDownColor: '#4D94FF',
        });

        // 지표 시리즈 추가 (v5 style)
        const smaSeries = chart.addSeries(LineSeries, { color: '#FFD700', lineWidth: 1, title: 'SMA(20)' });
        const bbUpperSeries = chart.addSeries(LineSeries, { color: 'rgba(77, 148, 255, 0.4)', lineWidth: 1, lineStyle: 2 });
        const bbLowerSeries = chart.addSeries(LineSeries, { color: 'rgba(77, 148, 255, 0.4)', lineWidth: 1, lineStyle: 2 });

        chartRef.current = chart;

        const update = async () => {
          try {
            const res = await axios.get(`http://localhost:8000/api/candles/${symbol}?interval=${interval}`);
            if (res.data.status === 'success' && res.data.data.length > 0) {
              const data = res.data.data;
              series.setData(data);
              
              // 지표 데이터 필터링 (값이 있는 경우만)
              smaSeries.setData(data.filter(d => d.sma).map(d => ({ time: d.time, value: d.sma })));
              bbUpperSeries.setData(data.filter(d => d.bb_upper).map(d => ({ time: d.time, value: d.bb_upper })));
              bbLowerSeries.setData(data.filter(d => d.bb_lower).map(d => ({ time: d.time, value: d.bb_lower })));
            }
          } catch (e) { console.error("Data update error:", e); }
        };

        update();
        timer = setInterval(update, Math.max(2000, interval * 1000)); // 인터벌에 맞춘 갱신 주기

        const handleResize = () => {
          if (containerRef.current && chart) {
            chart.applyOptions({ width: containerRef.current.clientWidth });
          }
        };
        window.addEventListener('resize', handleResize);

      } catch (err) {
        console.error("Chart Init Error:", err);
        setChartError(err.message);
      }
    };

    const timeout = setTimeout(initChart, 100);

    return () => {
      clearTimeout(timeout);
      if (timer) clearInterval(timer);
      if (chart) chart.remove();
    };
  }, [symbol, interval]);

  if (chartError) {
    return <div className="flex items-center justify-center h-full text-red-500 font-mono text-xs p-4 bg-red-500/5 rounded-2xl border border-red-500/20">Chart Engine Error: {chartError}</div>;
  }

  return <div ref={containerRef} className="w-full h-[400px]" />;
};

function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [recentTrades, setRecentTrades] = useState([]);
  const [isCollectorRunning, setIsCollectorRunning] = useState(false);
  const [systemStatus, setSystemStatus] = useState({ backend: 'offline', db: 'offline' });
  const [chartInterval, setChartInterval] = useState(5);
  
  const currentTab = MENU_ITEMS.find(item => item.id === activeTab);

  // 시스템 상태 및 수집기 상태 체크
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const res = await axios.get('http://localhost:8000/api/status');
        setSystemStatus({ backend: 'online', db: 'connected' });
        setIsCollectorRunning(res.data.collector === 'running');
      } catch (error) {
        setSystemStatus({ backend: 'offline', db: 'offline' });
        setIsCollectorRunning(false);
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  // 수집기 제어 함수
  const handleCollectorToggle = async () => {
    try {
      const endpoint = isCollectorRunning ? 'stop' : 'start';
      const res = await axios.post(`http://localhost:8000/api/collector/${endpoint}`);
      if (res.data.status === 'success') {
        setIsCollectorRunning(!isCollectorRunning);
      }
    } catch (error) {
      alert("백엔드 서버와 통신할 수 없습니다. 서버가 실행 중인지 확인하세요.");
    }
  };

  useEffect(() => {
    const fetchTrades = async () => {
      try {
        const res = await axios.get('http://localhost:8000/api/trades/KRW-BTC?limit=15');
        if (res.data.status === 'success') {
          setRecentTrades(res.data.data.reverse());
        }
      } catch (e) { console.error(e); }
    };
    fetchTrades();
    const inv = setInterval(fetchTrades, 2000);
    return () => clearInterval(inv);
  }, []);

  return (
    <div className="flex h-screen bg-[#121212] text-white font-sans overflow-hidden">
      {/* Sidebar */}
      <aside className="w-72 bg-[#1E1E1E] border-r border-gray-800 p-8 flex flex-col shrink-0">
        <div className="flex items-center gap-3 mb-12">
          <Activity className="text-red-500" size={32} />
          <h1 className="text-2xl font-black tracking-tighter">ANTIGRAVITY</h1>
        </div>
        <nav className="flex-1 space-y-2">
          {MENU_ITEMS.map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`w-full flex items-center gap-4 px-5 py-4 rounded-2xl transition-all ${activeTab === item.id ? 'bg-red-500 text-white shadow-lg' : 'text-gray-500 hover:bg-gray-800'}`}
            >
              {item.icon} <span className="font-bold text-sm">{item.label}</span>
            </button>
          ))}
        </nav>
        
        <div className="mt-auto p-5 bg-[#121212] rounded-3xl border border-gray-800/50">
          <div className="flex items-center gap-3 mb-2">
            <div className={`w-2 h-2 rounded-full ${systemStatus.backend === 'online' ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]' : 'bg-red-500'}`}></div>
            <span className="text-[10px] font-bold text-gray-500 uppercase tracking-widest">
              {systemStatus.backend === 'online' ? 'System Live' : 'System Offline'}
            </span>
          </div>
          <div className="text-[10px] text-gray-600 font-mono space-y-1">
            <p>COLLECTOR: {isCollectorRunning ? 'RUNNING' : 'IDLE'}</p>
            <p>DB: {systemStatus.db.toUpperCase()}</p>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-24 border-b border-gray-800/50 flex items-center justify-between px-12 bg-[#1E1E1E]/50">
          <h2 className="text-2xl font-black">{currentTab?.label}</h2>
          <div className="flex items-center gap-4">
            <div className="px-4 py-2 bg-gray-800 rounded-xl text-xs font-bold text-gray-400">KRW-BTC</div>
            {isCollectorRunning && (
              <div className="flex items-center gap-2 px-4 py-2 bg-green-500/10 text-green-500 rounded-xl text-xs font-bold border border-green-500/20">
                <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></div>
                LIVE DATA
              </div>
            )}
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-12 custom-scrollbar">
          {activeTab === 'dashboard' && (
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-10">
              <section className="xl:col-span-2 bg-[#1E1E1E] rounded-[2.5rem] border border-gray-800 p-10 shadow-2xl relative">
                <div className="flex justify-between items-center mb-8">
                  <h3 className="text-sm font-black text-gray-500 uppercase tracking-widest">Professional Chart</h3>
                  <div className="flex gap-2 bg-[#121212] p-1.5 rounded-2xl border border-gray-800">
                    {[1, 5, 10, 30, 60].map((sec) => (
                      <button
                        key={sec}
                        onClick={() => setChartInterval(sec)}
                        className={`px-4 py-1.5 rounded-xl text-[10px] font-black transition-all ${chartInterval === sec ? 'bg-red-500 text-white' : 'text-gray-500 hover:text-gray-300'}`}
                      >
                        {sec}S
                      </button>
                    ))}
                  </div>
                </div>
                {!isCollectorRunning && recentTrades.length === 0 && (
                  <div className="absolute inset-0 z-10 bg-[#1E1E1E]/80 backdrop-blur-sm flex flex-col items-center justify-center rounded-[2.5rem]">
                    <Activity size={48} className="text-gray-700 mb-4" />
                    <p className="text-gray-500 font-bold">수집기가 정지되어 있습니다.</p>
                    <button onClick={() => setActiveTab('settings')} className="mt-4 px-6 py-2 bg-red-500 text-white rounded-xl font-bold text-sm">설정에서 시작하기</button>
                  </div>
                )}
                <div className="bg-[#121212]/50 rounded-3xl p-2 border border-gray-800/30">
                  <CandleChart symbol="KRW-BTC" interval={chartInterval} />
                </div>
              </section>
              
              <aside className="space-y-10">
                <div className="bg-[#1E1E1E] rounded-[2.5rem] border border-gray-800 p-10 shadow-xl">
                  <h3 className="text-xs font-black text-gray-600 tracking-widest mb-6 uppercase">Live Tape</h3>
                  <div className="space-y-3">
                    {recentTrades.map((t, i) => (
                      <div key={i} className="flex justify-between items-center text-sm">
                        <span className="text-gray-600 font-mono text-xs">{new Date(t.timestamp).toLocaleTimeString()}</span>
                        <span className={`font-black ${t.ask_bid === 'BID' ? 'text-blue-500' : 'text-red-500'}`}>{t.price.toLocaleString()}</span>
                      </div>
                    ))}
                    {recentTrades.length === 0 && <p className="text-gray-700 text-xs text-center py-10 italic">No trade data yet</p>}
                  </div>
                </div>
              </aside>
            </div>
          )}

          {activeTab === 'settings' && (
            <div className="max-w-4xl mx-auto space-y-8">
              <div className="bg-[#1E1E1E] rounded-[2.5rem] border border-gray-800 p-10 shadow-2xl">
                <h3 className="text-xl font-black mb-8 border-b border-gray-800 pb-4">데이터 관리</h3>
                <div className="flex items-center justify-between p-8 bg-[#121212] rounded-3xl border border-gray-800/50">
                  <div>
                    <h4 className="text-lg font-bold mb-2">업비트 실시간 수집기</h4>
                    <p className="text-gray-500 text-sm">WebSocket을 통해 실시간 체결 및 호가 정보를 DB에 저장합니다.</p>
                  </div>
                  <button 
                    onClick={handleCollectorToggle}
                    className={`px-10 py-4 rounded-2xl font-black text-lg transition-all ${
                      isCollectorRunning 
                      ? 'bg-gray-800 text-red-500 border border-red-500/30 hover:bg-red-500 hover:text-white' 
                      : 'bg-red-500 text-white shadow-lg shadow-red-500/30 hover:scale-105'
                    }`}
                  >
                    {isCollectorRunning ? '수집 중지' : '수집 시작'}
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                <div className="bg-[#1E1E1E] rounded-[2.5rem] border border-gray-800 p-10 shadow-xl">
                  <h3 className="text-sm font-black text-gray-500 uppercase tracking-widest mb-6">시스템 상태</h3>
                  <div className="space-y-4">
                    <div className="flex justify-between p-4 bg-[#121212] rounded-2xl border border-gray-800/30">
                      <span className="text-gray-500 font-bold">API Server</span>
                      <span className={systemStatus.backend === 'online' ? 'text-green-500 font-black' : 'text-red-500 font-black'}>{systemStatus.backend.toUpperCase()}</span>
                    </div>
                    <div className="flex justify-between p-4 bg-[#121212] rounded-2xl border border-gray-800/30">
                      <span className="text-gray-500 font-bold">Database</span>
                      <span className={systemStatus.db === 'connected' ? 'text-green-500 font-black' : 'text-red-500 font-black'}>{systemStatus.db.toUpperCase()}</span>
                    </div>
                  </div>
                </div>
                <div className="bg-[#1E1E1E] rounded-[2.5rem] border border-gray-800 p-10 shadow-xl">
                  <h3 className="text-sm font-black text-gray-500 uppercase tracking-widest mb-6">전략 구성 (Strategy)</h3>
                  <div className="space-y-6">
                    <div className="space-y-2">
                      <label className="text-xs font-bold text-gray-400">활성 전략</label>
                      <select className="w-full bg-[#121212] border border-gray-800 rounded-xl px-4 py-3 text-sm font-bold outline-none focus:border-red-500">
                        <option>RSI Mean Reversion</option>
                        <option>Bollinger Band Breakout</option>
                        <option>MACD Crossover</option>
                      </select>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <label className="text-xs font-bold text-gray-400">매수 기준 (Low)</label>
                        <input type="number" defaultValue={30} className="w-full bg-[#121212] border border-gray-800 rounded-xl px-4 py-3 text-sm font-bold" />
                      </div>
                      <div className="space-y-2">
                        <label className="text-xs font-bold text-gray-400">매도 기준 (High)</label>
                        <input type="number" defaultValue={70} className="w-full bg-[#121212] border border-gray-800 rounded-xl px-4 py-3 text-sm font-bold" />
                      </div>
                    </div>
                    <button className="w-full py-3 bg-gray-800 hover:bg-gray-700 text-white rounded-xl text-xs font-bold transition-colors">
                      전략 파라미터 저장
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'backtest' && (
            <div className="max-w-4xl mx-auto space-y-8">
              <div className="bg-[#1E1E1E] rounded-[2.5rem] border border-gray-800 p-10 shadow-2xl">
                <h3 className="text-xl font-black mb-8 border-b border-gray-800 pb-4">시뮬레이션 설정</h3>
                <div className="grid grid-cols-2 gap-6 mb-8">
                  <div className="space-y-2">
                    <label className="text-xs font-bold text-gray-500 uppercase">대상 심볼</label>
                    <input type="text" value="KRW-BTC" disabled className="w-full bg-[#121212] border border-gray-800 rounded-xl px-4 py-3 text-sm font-bold text-gray-400" />
                  </div>
                  <div className="space-y-2">
                    <label className="text-xs font-bold text-gray-500 uppercase">초기 자본 (KRW)</label>
                    <input type="number" defaultValue={1000000} id="initialCash" className="w-full bg-[#121212] border border-gray-800 rounded-xl px-4 py-3 text-sm font-bold focus:border-red-500 outline-none" />
                  </div>
                </div>
                <button 
                  onClick={async () => {
                    const initialCash = document.getElementById('initialCash').value;
                    try {
                      const res = await axios.post('http://localhost:8000/api/backtest/run', {
                        symbol: 'KRW-BTC',
                        start_date: '2024-01-01',
                        end_date: '2024-01-02',
                        initial_cash: parseFloat(initialCash)
                      });
                      
                      if (res.data.status === 'success') {
                        alert(`백테스트 완료!\n최종 자산: ${res.data.summary.final_value.toLocaleString()} KRW\n수익률: ${res.data.summary.roi}%`);
                      } else {
                        alert("백테스트 실패: " + res.data.message);
                      }
                    } catch (e) { alert("통신 에러: " + e.message); }
                  }}
                  className="w-full py-5 bg-red-500 text-white rounded-2xl font-black text-lg shadow-xl shadow-red-500/20 hover:scale-[1.02] transition-all"
                >
                  백테스트 실행
                </button>
              </div>

              <div className="bg-[#1E1E1E] rounded-[2.5rem] border border-gray-800 p-10 shadow-xl">
                <h3 className="text-sm font-black text-gray-500 uppercase tracking-widest mb-6">최근 실행 결과</h3>
                <div className="flex flex-col items-center justify-center py-20 border-2 border-dashed border-gray-800 rounded-3xl">
                  <BarChart2 size={48} className="text-gray-800 mb-4" />
                  <p className="text-gray-600 font-bold text-sm">실행된 백테스트 결과가 없습니다.</p>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

export default App;
