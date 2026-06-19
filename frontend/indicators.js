/**
     * 기술 지표 계산 엔진 (Indicator Engine Module)
     * 프론트엔드의 성능 격리 및 불변성 유지를 위해 단일 캔들에 대한 실시간 점진 지표 연산만 지원합니다.
     */
const IndicatorEngine = {
    /**
     * 이전 캔들 배열(history)과 현재 미완성 캔들(currentCandle)을 이용해 
     * 마지막 시점의 단일 보조 지표값(SMA, BB, RSI)을 계산하여 새로운 캔들 객체로 반환합니다.
     * 불변성을 보장하기 위해 기존 객체를 오염시키지 않고 복사본을 반환합니다.
     */
    calculateSingle(history, currentCandle) {
        if (!currentCandle) return null;
        
        const period = 20;
        const rsiPeriod = 14;
        
        // 연산을 위해 이전 캔들들의 종가 추출 (최대 100개 확보)
        const prevCloses = history.slice(-100).map(c => c.close);
        const closes = [...prevCloses, currentCandle.close];
        
        const n = closes.length;
        let sma = null;
        let bb_upper = null;
        let bb_lower = null;
        let rsi = null;
        
        // 1. SMA (20) 계산
        if (n >= period) {
            const sum = closes.slice(-period).reduce((acc, val) => acc + val, 0);
            sma = sum / period;
            
            // 2. Bollinger Bands (20, 2) 계산
            const mean = sma;
            const variance = closes.slice(-period).reduce((acc, val) => acc + Math.pow(val - mean, 2), 0) / period;
            const std = Math.sqrt(variance);
            bb_upper = mean + (2 * std);
            bb_lower = mean - (2 * std);
        }
        
        // 3. RSI (14) 계산
        if (n >= rsiPeriod + 1) {
            // 점진 RSI 계산을 위해 15개 원소 간의 diff 추출
            const rsiCloses = closes.slice(-(rsiPeriod + 1));
            let gains = 0;
            let losses = 0;
            
            for (let i = 1; i < rsiCloses.length; i++) {
                const diff = rsiCloses[i] - rsiCloses[i - 1];
                if (diff > 0) {
                    gains += diff;
                } else {
                    losses -= diff;
                }
            }
            
            const avgGain = gains / rsiPeriod;
            const avgLoss = losses / rsiPeriod;
            
            if (avgLoss === 0) {
                rsi = avgGain > 0 ? 100.0 : 50.0;
            } else {
                const rs = avgGain / avgLoss;
                rsi = 100.0 - (100.0 / (1 + rs));
            }
        } else {
            rsi = 50.0;
        }

        // 4. EMA (20) 계산
        let ema = null;
        const emaPeriod = 20;
        const emaAlpha = 2 / (emaPeriod + 1);
        if (n >= emaPeriod) {
            let currentEma = closes[0];
            for (let i = 1; i < n; i++) {
                currentEma = closes[i] * emaAlpha + currentEma * (1 - emaAlpha);
            }
            ema = currentEma;
        }

        // 5. MACD (12, 26, 9) 계산
        const ema12Period = 12;
        const ema26Period = 26;
        const alpha12 = 2 / (ema12Period + 1);
        const alpha26 = 2 / (ema26Period + 1);
        
        let ema12History = [];
        let ema26History = [];
        let curEma12 = closes[0];
        let curEma26 = closes[0];
        
        ema12History.push(curEma12);
        ema26History.push(curEma26);
        
        for (let i = 1; i < n; i++) {
            curEma12 = closes[i] * alpha12 + curEma12 * (1 - alpha12);
            curEma26 = closes[i] * alpha26 + curEma26 * (1 - alpha26);
            ema12History.push(curEma12);
            ema26History.push(curEma26);
        }
        
        let macdLines = [];
        for (let i = 0; i < n; i++) {
            macdLines.push(ema12History[i] - ema26History[i]);
        }
        
        let macd_line = macdLines[n - 1];
        let macd_signal = null;
        let macd_hist = null;
        
        const signalPeriod = 9;
        const alphaSignal = 2 / (signalPeriod + 1);
        if (n >= ema26Period) {
            let curSignal = macdLines[0];
            for (let i = 1; i < n; i++) {
                curSignal = macdLines[i] * alphaSignal + curSignal * (1 - alphaSignal);
            }
            macd_signal = curSignal;
            macd_hist = macd_line - macd_signal;
        }

        // 6. ATR (14) 계산
        const highs = [...history.slice(-100).map(c => c.high), currentCandle.high];
        const lows = [...history.slice(-100).map(c => c.low), currentCandle.low];
        let trs = [];
        for (let i = 1; i < n; i++) {
            const h = highs[i];
            const l = lows[i];
            const prev_c = closes[i - 1];
            const tr = Math.max(h - l, Math.abs(h - prev_c), Math.abs(l - prev_c));
            trs.push(tr);
        }
        
        let atr = null;
        const atrPeriod = 14;
        if (trs.length >= atrPeriod) {
            const recentTrs = trs.slice(-atrPeriod);
            const sum = recentTrs.reduce((acc, val) => acc + val, 0);
            atr = sum / atrPeriod;
        }
        
        // 불변성을 위해 새로운 캔들 객체 반환
        return {
            ...currentCandle,
            sma: sma !== null ? Math.round(sma * 10000) / 10000 : null,
            bb_upper: bb_upper !== null ? Math.round(bb_upper * 10000) / 10000 : null,
            bb_lower: bb_lower !== null ? Math.round(bb_lower * 10000) / 10000 : null,
            rsi: rsi !== null ? Math.round(rsi * 10000) / 10000 : null,
            ema: ema !== null ? Math.round(ema * 10000) / 10000 : null,
            macd_line: macd_line !== null ? Math.round(macd_line * 10000) / 10000 : null,
            macd_signal: macd_signal !== null ? Math.round(macd_signal * 10000) / 10000 : null,
            macd_hist: macd_hist !== null ? Math.round(macd_hist * 10000) / 10000 : null,
            atr: atr !== null ? Math.round(atr * 10000) / 10000 : null
        };
    }
};
