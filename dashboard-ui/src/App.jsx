import React, { useState, useEffect } from 'react';
import { Activity, TrendingUp, TrendingDown, DollarSign, ShieldAlert, BarChart3, Clock, AlertTriangle } from 'lucide-react';
import axios from 'axios';
import './index.css';

const API_BASE = 'http://localhost:8007/api';

const App = () => {
    const [trades, setTrades] = useState([]);
    const [metrics, setMetrics] = useState({ win_rate: 0, profit_factor: 0, max_drawdown: 0, total_pnl: 0 });
    const [liveState, setLiveState] = useState(null);
    const [isLive, setIsLive] = useState(false);
    const [error, setError] = useState(null);

    // Fetch live state and metrics every 3 seconds
    useEffect(() => {
        const fetchData = async () => {
            try {
                // Fetch State
                const stateRes = await axios.get(`${API_BASE}/state`);
                if (stateRes.data.status === 'success') {
                    setLiveState(stateRes.data.data);
                    setIsLive(true);
                    setError(null);
                } else if (stateRes.data.status === 'waiting') {
                    setIsLive(false);
                    setError("Waiting for Orchestrator to start...");
                }

                // Fetch Metrics
                const metricsRes = await axios.get(`${API_BASE}/metrics`);
                if (metricsRes.data.status === 'success') {
                    setMetrics(metricsRes.data.data);
                    // Trades coming from metrics endpoint are the last 10
                    // Reverse to show newest on top
                    setTrades(metricsRes.data.data.trades.reverse());
                }
            } catch (err) {
                console.error("Dashboard API Error:", err);
                setIsLive(false);
                setError("Cannot connect to Dashboard API (Port 8007). Ensure it is running.");
            }
        };

        fetchData(); // run immediately
        const interval = setInterval(fetchData, 3000);
        return () => clearInterval(interval);
    }, []);

    // Derived values with fallbacks
    const trend = liveState?.prediction?.trend || 'neutral';
    const confidence = liveState?.prediction?.confidence ? liveState.prediction.confidence : 0;
    const currentPrice = liveState?.current_price || 0;
    const activeTrade = liveState?.active_trade;
    const features = liveState?.prediction?.features || {};

    return (
        <div className="dashboard-container">
            {/* Sidebar / Nav */}
            <nav className="sidebar">
                <div className="brand">
                    <Activity className="brand-icon" />
                    <h1>AI F&O Trader</h1>
                </div>
                <ul className="nav-links">
                    <li className="active"><BarChart3 size={18} /> Dashboard</li>
                    <li><Clock size={18} /> History</li>
                    <li><ShieldAlert size={18} /> Risk Mgmt</li>
                </ul>
                <div className="status-indicator">
                    <div className={`status-dot ${isLive ? 'live' : 'offline'}`}></div>
                    <span className="status-text">
                        {isLive ? 'System Live' : 'Backend Offline'}
                    </span>
                </div>
            </nav>

            {/* Main Content */}
            <main className="main-content">
                <header className="top-header">
                    <div>
                        <h2>Trading Overview</h2>
                        <p className="subtitle">Real-time model predictions and execution</p>
                    </div>
                    {error && (
                        <div className="error-banner">
                            <AlertTriangle size={16} />
                            <span>{error}</span>
                        </div>
                    )}
                    <div className="market-status">
                        {/* We dynamically render whatever symbol is in state, fallback to NIFTY */}
                        <span className="badge nifty">
                            {liveState?.symbol || 'NIFTY'}: {currentPrice ? currentPrice.toFixed(2) : '---'}
                            {isLive && <span className="pulse-dot"></span>}
                        </span>
                    </div>
                </header>

                {/* Metrics Row */}
                <section className="metrics-grid">
                    <div className="metric-card glass-panel">
                        <div className="metric-header">
                            <h3>Win Rate</h3>
                            <div className="icon-wrapper blue"><TrendingUp size={20} /></div>
                        </div>
                        <div className="metric-value">{metrics.win_rate}%</div>
                        <div className="metric-trend">Total Strategy WR</div>
                    </div>

                    <div className="metric-card glass-panel">
                        <div className="metric-header">
                            <h3>Profit Factor</h3>
                            <div className="icon-wrapper purple"><BarChart3 size={20} /></div>
                        </div>
                        <div className="metric-value">{metrics.profit_factor}</div>
                        <div className="metric-trend">Gross Profit / Loss</div>
                    </div>

                    <div className="metric-card glass-panel">
                        <div className="metric-header">
                            <h3>Total Net PnL</h3>
                            <div className="icon-wrapper green"><DollarSign size={20} /></div>
                        </div>
                        <div className={`metric-value highlight ${metrics.total_pnl < 0 ? 'text-red' : ''}`}>
                            ₹{metrics.total_pnl.toLocaleString()}
                        </div>
                        <div className="metric-trend up">{trades.length} Trades Executed</div>
                    </div>

                    <div className="metric-card glass-panel">
                        <div className="metric-header">
                            <h3>Est. Max Drawdown</h3>
                            <div className="icon-wrapper red"><TrendingDown size={20} /></div>
                        </div>
                        <div className="metric-value">{metrics.max_drawdown}%</div>
                        <div className="metric-trend down">Historical Peak Drop</div>
                    </div>
                </section>

                {/* Middle Row: AI Prediction & Active Trade Info */}
                <section className="middle-grid">
                    <div className="ai-prediction-panel glass-panel">
                        <h3>Live AI Engine State</h3>
                        <div className="prediction-content">
                            <div className="trend-indicator">
                                <div className={`pulse-ring ${trend}`}></div>
                                <div className={`trend-label ${trend}`}>
                                    {trend.toUpperCase()}
                                    {trend === 'bullish' ? <TrendingUp size={24} className="ml-2" /> : trend === 'bearish' ? <TrendingDown size={24} className="ml-2" /> : ''}
                                </div>
                            </div>

                            <div className="confidence-meter">
                                <div className="meter-label">
                                    <span>Model Confidence (Threshold 65%)</span>
                                    <span>{(confidence * 100).toFixed(1)}%</span>
                                </div>
                                <div className="meter-track">
                                    <div
                                        className={`meter-fill ${confidence > 0.65 ? 'high' : 'low'}`}
                                        style={{ width: `${confidence * 100}%` }}
                                    ></div>
                                </div>
                                {confidence > 0 && confidence <= 0.65 && <p className="warning-text">Below 65% threshold - No trade zone</p>}
                            </div>

                            {Object.keys(features).length > 0 && (
                                <div className="features-breakdown mt-4">
                                    <h4>Latest Feature Engine Output</h4>
                                    <ul className="feature-list">
                                        <li><span>VWAP Dist</span> <span className="val">{features.vwap_dist?.toFixed(4)}</span></li>
                                        <li><span>RSI (14)</span> <span className="val">{features.rsi?.toFixed(1)}</span></li>
                                        <li><span>OI Change %</span> <span className="val">{features.oi_change_pct?.toFixed(2)}%</span></li>
                                    </ul>
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="trades-panel glass-panel">
                        <div className="panel-header">
                            <h3>Active Trade Monitor</h3>
                        </div>

                        {activeTrade ? (
                            <div className="active-trade-card">
                                <div className="trade-badge running">LIVE POSITION</div>
                                <h4>{activeTrade.symbol}</h4>
                                <div className="trade-details">
                                    <p><strong>Action:</strong> <span className={activeTrade.action.includes('CALL') ? 'text-green' : 'text-red'}>{activeTrade.action.replace('_', ' ')}</span></p>
                                    <p><strong>Qty:</strong> {activeTrade.qty}</p>
                                    <p><strong>Entry:</strong> ₹{activeTrade.entry_price.toFixed(2)}</p>
                                    <p><strong>Real-Time PnL (Simulated):</strong> <span className={activeTrade.simulated_pnl_amount >= 0 ? 'text-green highlight' : 'text-red highlight'}>
                                        {activeTrade.simulated_pnl_amount >= 0 ? '+' : ''}₹{activeTrade.simulated_pnl_amount.toFixed(2)} ({activeTrade.current_pnl_pct}%)
                                    </span></p>
                                </div>
                            </div>
                        ) : (
                            <div className="empty-trade-state">
                                <Activity size={32} opacity={0.3} />
                                <p>Scanning market for opportunities...</p>
                                <span className="sub-text">Waiting for AI confidence > 65%</span>
                            </div>
                        )}

                        <div className="panel-header mt-4">
                            <h3>Recent Historical Trades</h3>
                        </div>
                        <div className="table-responsive">
                            <table className="trades-table">
                                <thead>
                                    <tr>
                                        <th>Symbol</th>
                                        <th>Action</th>
                                        <th>Entry</th>
                                        <th>PnL</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {trades.length === 0 ? (
                                        <tr><td colSpan="4" style={{ textAlign: 'center', padding: '20px', opacity: 0.5 }}>No trades logged yet.</td></tr>
                                    ) : trades.map((trade, i) => (
                                        <tr key={i} className="trade-row">
                                            <td><strong>{trade.symbol}</strong></td>
                                            <td>
                                                <span className={`action-badge ${trade.action.includes('CALL') ? 'call' : 'put'}`}>
                                                    {trade.action.replace('_', ' ').substring(0, 6)}
                                                </span>
                                            </td>
                                            <td>₹{trade.entry_price.toFixed(2)}</td>
                                            <td className={`pnl-col ${trade.pnl > 0 ? 'profit' : trade.pnl < 0 ? 'loss' : ''}`}>
                                                {trade.pnl > 0 ? '+' : ''}₹{trade.pnl.toFixed(2)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </section>
            </main>
        </div>
    );
};

export default App;
