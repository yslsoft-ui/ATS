-- 1. exchanges
CREATE TABLE IF NOT EXISTS exchanges (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    fee_rate REAL DEFAULT 0.0005,
    market_type TEXT DEFAULT 'crypto',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO exchanges (id, name, fee_rate, market_type) VALUES ('upbit', 'Upbit', 0.0005, 'crypto');
INSERT OR IGNORE INTO exchanges (id, name, fee_rate, market_type) VALUES ('kis', 'KIS', 0.00015, 'stock');
INSERT OR IGNORE INTO exchanges (id, name, fee_rate, market_type) VALUES ('bithumb', 'Bithumb', 0.0025, 'crypto');

-- 2. trades
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_id TEXT,
    market TEXT,
    symbol TEXT,
    trade_price REAL,
    trade_volume REAL,
    ask_bid TEXT,
    trade_timestamp INTEGER,
    sequential_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2.5. orderbooks
CREATE TABLE IF NOT EXISTS orderbooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_id TEXT,
    symbol TEXT,
    timestamp INTEGER,
    bids TEXT,
    asks TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 3. portfolios (INTEGER 키로 완전 개편)
CREATE TABLE IF NOT EXISTS portfolios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('live', 'simulation', 'backtest')),
    duration REAL DEFAULT 0.0,
    strategy_info TEXT DEFAULT '',
    ended_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 3.5. portfolio_exchanges
CREATE TABLE IF NOT EXISTS portfolio_exchanges (
    portfolio_id INTEGER,
    exchange_id TEXT,
    initial_cash REAL DEFAULT 0.0,
    cash REAL DEFAULT 0.0,
    metrics TEXT DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (portfolio_id, exchange_id),
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 4. positions (INTEGER portfolio_id 반영 및 FK 제약)
CREATE TABLE IF NOT EXISTS positions (
    portfolio_id INTEGER,
    symbol TEXT,
    quantity REAL DEFAULT 0,
    avg_price REAL DEFAULT 0,
    entry_time REAL DEFAULT 0.0,
    peak_price REAL DEFAULT 0.0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    exchange_id TEXT,
    PRIMARY KEY (portfolio_id, exchange_id, symbol),
    FOREIGN KEY (portfolio_id, exchange_id) REFERENCES portfolio_exchanges(portfolio_id, exchange_id) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 5. orders_history (INTEGER portfolio_id 반영 및 FK 제약)
CREATE TABLE IF NOT EXISTS orders_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER,
    exchange_id TEXT,
    market TEXT,
    strategy_id TEXT,
    symbol TEXT,
    side TEXT,
    price REAL,
    quantity REAL,
    fee REAL,
    timestamp INTEGER,
    reason TEXT,
    context TEXT,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 6. alerts
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_id TEXT,
    symbol TEXT,
    price REAL,
    msg TEXT,
    timestamp INTEGER
);

-- 7. candles
CREATE TABLE IF NOT EXISTS candles (
    exchange_id TEXT,
    symbol TEXT,
    interval INTEGER,
    timestamp INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    is_closed INTEGER DEFAULT 1,
    PRIMARY KEY (exchange_id, symbol, interval, timestamp)
);

-- 8. asset_master
CREATE TABLE IF NOT EXISTS asset_master (
    symbol TEXT PRIMARY KEY,
    korean_name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    category TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 9. exchange_assets
CREATE TABLE IF NOT EXISTS exchange_assets (
    exchange_id TEXT,
    symbol TEXT,
    is_active INTEGER DEFAULT 1,
    is_delisted INTEGER DEFAULT 0,
    market TEXT,
    market_updated_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (exchange_id, symbol),
    FOREIGN KEY (symbol) REFERENCES asset_master(symbol) ON UPDATE CASCADE
);

-- 10. real_orders
CREATE TABLE IF NOT EXISTS real_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_id TEXT NOT NULL,
    uuid TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL DEFAULT 0.0,
    volume REAL DEFAULT 0.0,
    executed_volume REAL DEFAULT 0.0,
    fee REAL DEFAULT 0.0,
    state TEXT NOT NULL,
    created_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 11. system_events
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    target TEXT NOT NULL,
    message TEXT,
    timestamp INTEGER NOT NULL,
    context TEXT
);

-- 12. strategy_versions
CREATE TABLE IF NOT EXISTS strategy_versions (
    strategy_id TEXT PRIMARY KEY,
    current_version_id INTEGER NOT NULL,
    current_params TEXT NOT NULL,
    rollback_source_version INTEGER,
    applied_at INTEGER NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 13. strategy_parameter_history
CREATE TABLE IF NOT EXISTS strategy_parameter_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    parent_version_id INTEGER,
    old_params TEXT,
    new_params TEXT,
    proposal_id INTEGER,
    is_current INTEGER DEFAULT 0,
    changed_by TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 14. strategy_performance_snapshots
CREATE TABLE IF NOT EXISTS strategy_performance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    parameter_hash TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    roi REAL,
    mdd REAL,
    profit_factor REAL,
    win_rate REAL,
    trade_count INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 15. market_regime_summaries
CREATE TABLE IF NOT EXISTS market_regime_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    volatility REAL,
    rsi REAL,
    volume_ratio REAL,
    spread REAL,
    orderbook_imbalance REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 16. strategy_insights
CREATE TABLE IF NOT EXISTS strategy_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER,
    strategy_id TEXT,
    category TEXT NOT NULL,
    fact_summary TEXT NOT NULL,
    details_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 17. strategy_proposals
CREATE TABLE IF NOT EXISTS strategy_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    insight_id INTEGER,
    proposal_group_id TEXT,
    version INTEGER,
    portfolio_id INTEGER,
    strategy_id TEXT,
    status TEXT NOT NULL,
    outcome TEXT NOT NULL,
    original_params TEXT,
    proposed_params TEXT,
    metrics TEXT,
    mutation_trace TEXT,
    confidence_score INTEGER,
    applied_at INTEGER,
    rolled_back_at INTEGER,
    decision_path_hash TEXT UNIQUE,
    audit_log_json TEXT,
    counterfactual_roi REAL DEFAULT 0.0,
    counterfactual_mdd REAL DEFAULT 0.0,
    is_counterfactual_tracked INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (insight_id) REFERENCES strategy_insights(id) ON UPDATE CASCADE ON DELETE SET NULL,
    FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 18. proposal_evaluations
CREATE TABLE IF NOT EXISTS proposal_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL,
    horizon_name TEXT NOT NULL,
    predicted_roi_7d REAL,
    actual_roi_7d REAL,
    roi_divergence REAL,
    predicted_trade_count_7d INTEGER,
    actual_trade_count_7d INTEGER,
    trade_count_divergence INTEGER,
    candidate_roi REAL,
    champion_roi REAL,
    roi_gap REAL,
    candidate_mdd REAL,
    champion_mdd REAL,
    virtual_rollback INTEGER DEFAULT 0,
    actual_label TEXT,
    actual_label_source TEXT,
    due_at INTEGER NOT NULL DEFAULT 0,
    evaluated_at INTEGER,
    locked_at INTEGER,
    retry_count INTEGER DEFAULT 0,
    last_error TEXT,
    evaluation_status TEXT NOT NULL DEFAULT 'PENDING',
    horizon_type TEXT,
    horizon_value INTEGER,
    policy_version TEXT,
    scorer_version TEXT,
    predicted_risk_score REAL,
    baseline_value REAL,
    baseline_timestamp INTEGER,
    baseline_volume INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proposal_id) REFERENCES strategy_proposals(id) ON UPDATE CASCADE ON DELETE CASCADE,
    UNIQUE (proposal_id, horizon_name)
);

-- 19. girs_shadow_metrics
CREATE TABLE IF NOT EXISTS girs_shadow_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    proposal_id INTEGER,
    strategy_id TEXT,
    model_risk_score REAL,
    fallback_risk_score REAL,
    final_promotion_score REAL,
    shadow_risk_score REAL,
    replay_drift REAL,
    correction_active INTEGER DEFAULT 0,
    operation_mode TEXT,
    model_version TEXT,
    scaler_version TEXT,
    strategy_version_id INTEGER,
    simulation_session_id TEXT,
    decision_type TEXT,
    blocked_reason TEXT,
    trade_age_ms INTEGER,
    orderbook_age_ms INTEGER,
    indicator_age_ms INTEGER,
    is_fresh INTEGER DEFAULT 1,
    stale_reason TEXT,
    snapshot_version TEXT,
    snapshot_hash TEXT,
    feature_vector_hash TEXT,
    orderbook_available INTEGER DEFAULT 0,
    market_type TEXT,
    session_state TEXT,
    volatility_regime TEXT,
    liquidity_regime TEXT,
    exchange_id TEXT,
    tps REAL,
    trade_count INTEGER,
    volume REAL,
    idle_time REAL
);

-- 20. promotion_event_log
CREATE TABLE IF NOT EXISTS promotion_event_log (
    global_sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    proposal_id INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    timestamp REAL NOT NULL,
    feature_snapshot TEXT,
    graph_embedding TEXT,
    model_version TEXT,
    scaler_version TEXT,
    UNIQUE(proposal_id, sequence_no)
);

-- 21. universe_guard_state (exchange_id 명칭 통일)
CREATE TABLE IF NOT EXISTS universe_guard_state (
    exchange_id TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT,
    blocked_reason TEXT,
    blocked_count INTEGER DEFAULT 0,
    last_blocked_at REAL,
    last_event_logged_reason TEXT,
    PRIMARY KEY (exchange_id, market_type, symbol)
);

-- 22. proposal_reevaluation_jobs
CREATE TABLE IF NOT EXISTS proposal_reevaluation_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    requested_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER,
    requested_by TEXT NOT NULL,
    mode TEXT NOT NULL,
    input_snapshot_id INTEGER,
    error_message TEXT,
    worker_id TEXT,
    FOREIGN KEY (proposal_id) REFERENCES strategy_proposals(id) ON UPDATE CASCADE ON DELETE CASCADE
);

-- 23. proposal_evaluation_runs
CREATE TABLE IF NOT EXISTS proposal_evaluation_runs (
    evaluation_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL,
    job_id INTEGER,
    girs_score REAL,
    promotion_score REAL,
    stability_score REAL,
    rollback_probability REAL,
    data_quality_blocked INTEGER DEFAULT 0,
    counterfactual_result_id INTEGER,
    model_version TEXT,
    scorer_version TEXT,
    simulator_version TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES strategy_proposals(id) ON UPDATE CASCADE ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES proposal_reevaluation_jobs(job_id) ON UPDATE CASCADE ON DELETE SET NULL
);

-- 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_trades_exch_sym_time ON trades (exchange_id, symbol, trade_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_candles_exch_sym_time ON candles (exchange_id, symbol, interval, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_orders_history_portfolio_id ON orders_history (portfolio_id);
CREATE INDEX IF NOT EXISTS idx_positions_portfolio_id ON positions (portfolio_id);
CREATE INDEX IF NOT EXISTS idx_exchange_assets_active ON exchange_assets (exchange_id, is_active);
CREATE INDEX IF NOT EXISTS idx_real_orders_exch_sym ON real_orders (exchange_id, symbol);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades (trade_timestamp);
CREATE INDEX IF NOT EXISTS idx_candles_timestamp ON candles (timestamp);
CREATE INDEX IF NOT EXISTS idx_system_events_timestamp ON system_events (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_system_events_type ON system_events (event_type);
CREATE INDEX IF NOT EXISTS idx_strategy_param_hist ON strategy_parameter_history (strategy_id, version_id);
CREATE INDEX IF NOT EXISTS idx_strategy_perf_snap ON strategy_performance_snapshots (strategy_id, version_id);
CREATE INDEX IF NOT EXISTS idx_market_regime_sum ON market_regime_summaries (symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_prop_group ON strategy_proposals (proposal_group_id);
CREATE INDEX IF NOT EXISTS idx_prop_eval_status_due ON proposal_evaluations (evaluation_status, due_at);
CREATE INDEX IF NOT EXISTS idx_prop_eval_id_horizon ON proposal_evaluations (proposal_id, horizon_name);
CREATE INDEX IF NOT EXISTS idx_girs_shadow_metrics_time ON girs_shadow_metrics (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_promotion_event_log_prop ON promotion_event_log (proposal_id);
CREATE INDEX IF NOT EXISTS idx_proposal_reeval_jobs_prop ON proposal_reevaluation_jobs (proposal_id, status);
CREATE INDEX IF NOT EXISTS idx_proposal_eval_runs_prop ON proposal_evaluation_runs (proposal_id);
CREATE INDEX IF NOT EXISTS idx_universe_guard_state_status ON universe_guard_state (status);
CREATE INDEX IF NOT EXISTS idx_universe_guard_state_lookup ON universe_guard_state (exchange_id, market_type, status);
CREATE INDEX IF NOT EXISTS idx_ob_exch_sym_time ON orderbooks (exchange_id, symbol, timestamp DESC);

-- live 포트폴리오 시드 데이터
INSERT OR IGNORE INTO portfolios (id, name, type) VALUES (1, '실거래 포트폴리오', 'live');

-- 24. kis_stock_info (한국투자증권 주식 기본 세부정보 캐시 테이블)
CREATE TABLE IF NOT EXISTS kis_stock_info (
    symbol TEXT PRIMARY KEY,
    prdt_name TEXT,
    prdt_abrv_name TEXT,
    mket_id_cd TEXT,
    scty_grp_id_cd TEXT,
    excg_dvsn_cd TEXT,
    lstg_stqt INTEGER,
    lstg_cptl_amt INTEGER,
    cpta INTEGER,
    papr REAL,
    issu_pric REAL,
    kospi200_item_yn TEXT,
    scts_mket_lstg_dt TEXT,
    kosdaq_mket_lstg_dt TEXT,
    lstg_abol_dt TEXT,
    std_pdno TEXT,
    prdt_eng_name TEXT,
    tr_stop_yn TEXT,
    admn_item_yn TEXT,
    thdt_clpr REAL,
    bfdy_clpr REAL,
    std_idst_clsf_cd_name TEXT,
    idx_bztp_lcls_cd_name TEXT,
    idx_bztp_mcls_cd_name TEXT,
    idx_bztp_scls_cd_name TEXT,
    cptt_trad_tr_psbl_yn TEXT,
    nxt_tr_stop_yn TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

