import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  Activity, TrendingUp, TrendingDown, BarChart2, Shield,
  Zap, AlertTriangle, RefreshCw, ChevronUp, ChevronDown
} from 'lucide-react';

const API = 'http://localhost:8007';
const POLL_MS = 3000;

// ─── Mini equity curve SVG ────────────────────────────────────────────────────
function EquityCurve({ data }) {
  if (!data || data.length < 2) {
    return <div className="equity-empty">No trades yet — curve will appear after first trade.</div>;
  }
  const vals   = data.map(d => d.capital);
  const minV   = Math.min(...vals);
  const maxV   = Math.max(...vals);
  const range  = (maxV - minV) || 1;
  const W = 420, H = 80;

  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * W;
    const y = H - ((v - minV) / range) * H * 0.85 - 4;
    return `${x},${y}`;
  }).join(' ');

  const isProfit = vals[vals.length - 1] >= vals[0];
  const color    = isProfit ? '#10b981' : '#ef4444';
  const lastPct  = ((vals[vals.length - 1] - vals[0]) / vals[0] * 100).toFixed(2);

  return (
    <div className="equity-chart-wrapper">
      <div className="equity-header">
        <span className="equity-label">Equity Curve</span>
        <span className={`equity-return ${isProfit ? 'text-green' : 'text-red'}`}>
          {isProfit ? '+' : ''}{lastPct}%
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="equity-svg">
        <defs>
          <linearGradient id="eq-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.3" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <polyline fill="none" stroke={color} strokeWidth="2" points={pts} />
        <polygon fill="url(#eq-grad)" points={`0,${H} ${pts} ${W},${H}`} />
      </svg>
    </div>
  );
}

// ─── Feature Importance Bar Chart ─────────────────────────────────────────────
function FeatureImportance({ data }) {
  if (!data) return null;
  const entries = Object.entries(data).slice(0, 10);
  const maxVal  = Math.max(...entries.map(([, v]) => v));
  return (
    <div className="feat-list">
      {entries.map(([name, score]) => (
        <div key={name} className="feat-row">
          <span className="feat-name">{name}</span>
          <div className="feat-bar-track">
            <div className="feat-bar-fill" style={{ width: `${(score / maxVal) * 100}%` }} />
          </div>
          <span className="feat-score">{Math.round(score).toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Signal trace row ─────────────────────────────────────────────────────────
function SignalRow({ sig }) {
  const isNo    = sig.type === 'NO_TRADE';
  const time    = sig.time ? new Date(sig.time).toLocaleTimeString() : '—';
  const action  = sig.action || '—';
  return (
    <div className={`signal-row ${isNo ? 'no-trade' : 'trade'}`}>
      <span className="signal-time">{time}</span>
      <span className={`signal-badge ${isNo ? '' : 'trade-badge-active'}`}>{isNo ? 'SKIP' : action}</span>
      <span className="signal-reason">{sig.reason || '—'}</span>
    </div>
  );
}

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [state,       setState]       = useState(null);
  const [metrics,     setMetrics]     = useState(null);
  const [equityCurve, setEquityCurve] = useState([]);
  const [signals,     setSignals]     = useState([]);
  const [featImp,     setFeatImp]     = useState(null);
  const [riskStatus,  setRiskStatus]  = useState(null);
  const [error,       setError]       = useState(false);
  const [activeTab,   setActiveTab]   = useState('overview');
  const [symbol,      setSymbol]      = useState('NIFTY');

  const fetchAll = useCallback(async () => {
    try {
      const [s, m, eq, sig, fi, rs] = await Promise.all([
        axios.get(`${API}/api/state`),
        axios.get(`${API}/api/metrics`),
        axios.get(`${API}/api/equity_curve`),
        axios.get(`${API}/api/signals`),
        axios.get(`${API}/api/feature_importance`),
        axios.get(`${API}/api/risk_status`),
      ]);
      setState(s.data);
      setMetrics(m.data);
      setEquityCurve(eq.data?.data || []);
      setSignals(sig.data?.data || []);
      setFeatImp(fi.data?.data || null);
      setRiskStatus(rs.data?.data || null);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, POLL_MS);
    return () => clearInterval(id);
  }, [fetchAll]);

  const price     = state?.current_price;
  const pred      = state?.prediction;
  const trade     = state?.active_trade;
  const trend     = pred?.trend || 'neutral';
  const conf      = pred?.confidence || 0;
  const isBull    = trend === 'bullish';
  const isBear    = trend === 'bearish';
  const isLive    = state?.is_live;
  const risk      = riskStatus;

  const fmtPct    = v => `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`;
  const fmtRs     = v => `Rs ${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

  return (
    <div className="dashboard-container">
      {/* ── Sidebar ─────────────────────────────────────────────────────── */}
      <aside className="sidebar">
        <div className="brand">
          <Zap size={28} className="brand-icon" />
          <h1>F&amp;O AI Trader</h1>
        </div>

        {/* Symbol switcher */}
        <div className="symbol-switcher">
          {['NIFTY', 'BANKNIFTY', 'FINIFTY'].map(s => (
            <button key={s}
              className={`sym-btn ${symbol === s ? 'active' : ''}`}
              onClick={() => setSymbol(s)}>
              {s}
            </button>
          ))}
        </div>

        <ul className="nav-links">
          {[['overview','Overview', <BarChart2 size={18}/>],
            ['signals', 'Signal Trace', <Activity size={18}/>],
            ['features','Feature Imp.', <TrendingUp size={18}/>]].map(([t, l, ic]) => (
            <li key={t} className={activeTab === t ? 'active' : ''} onClick={() => setActiveTab(t)}>
              {ic} {l}
            </li>
          ))}
        </ul>

        <div className="status-indicator">
          <div className={`status-dot ${isLive && !error ? 'live' : 'offline'}`} />
          <div>
            <div style={{fontSize:'0.8rem', fontWeight:600}}>{error ? 'API Offline' : isLive ? 'System Live' : 'Standby'}</div>
            <div style={{fontSize:'0.7rem', color:'var(--text-muted)'}}>Updates every 3s</div>
          </div>
        </div>
      </aside>

      {/* ── Main content ────────────────────────────────────────────────── */}
      <main className="main-content">

        {error && (
          <div className="error-banner">
            <AlertTriangle size={16} />
            Cannot reach API gateway (localhost:8007). Ensure <code>run_all.bat</code> is running.
          </div>
        )}

        {/* ── HEADER ── */}
        <div className="top-header">
          <div>
            <h2>{symbol} Dashboard</h2>
            <p className="subtitle">
              {price ? `Spot: Rs ${Number(price).toLocaleString('en-IN')}` : 'Fetching...'}
              {isLive && !error && <span className="pulse-dot" />}
            </p>
          </div>
          <div className="market-status">
            <div className={`badge ${isBull ? 'badge-bull' : isBear ? 'badge-bear' : ''}`}>
              {isBull ? <TrendingUp size={14}/> : isBear ? <TrendingDown size={14}/> : <Activity size={14}/>}
              &nbsp;{trend.toUpperCase()}
            </div>
            <div className="badge">
              <Shield size={14}/>&nbsp;Conf: {(conf * 100).toFixed(0)}%
            </div>
          </div>
        </div>

        {/* ════════════ OVERVIEW TAB ════════════ */}
        {activeTab === 'overview' && (
          <>
            {/* Metrics Row */}
            <div className="metrics-grid">
              {[
                { label: 'Total Trades',  val: metrics?.total_trades ?? '—',              icon: <Activity size={18}/>,    col: 'blue'   },
                { label: 'Win Rate',      val: metrics?.win_rate ? `${metrics.win_rate}%` : '—', icon: <TrendingUp size={18}/>, col: 'green'  },
                { label: 'Profit Factor', val: metrics?.profit_factor ?? '—',             icon: <BarChart2 size={18}/>,   col: 'purple' },
                { label: 'Net P&L',       val: metrics?.net_pnl != null ? fmtRs(metrics.net_pnl) : '—', icon: <Zap size={18}/>, col: metrics?.net_pnl >= 0 ? 'green' : 'red' },
              ].map(({ label, val, icon, col }) => (
                <div key={label} className="glass-panel">
                  <div className="metric-header">
                    <h3>{label}</h3>
                    <div className={`icon-wrapper ${col}`}>{icon}</div>
                  </div>
                  <div className="metric-value">{val}</div>
                </div>
              ))}
            </div>

            {/* Equity + Risk row */}
            <div className="twin-grid">
              <div className="glass-panel">
                <EquityCurve data={equityCurve} />
              </div>

              <div className="glass-panel risk-panel">
                <h3 className="panel-title"><Shield size={16}/> Daily Risk Status</h3>
                {risk ? (
                  <div className="risk-grid">
                    {[
                      ['Capital',      fmtRs(risk.capital)],
                      ['Daily PnL',    fmtPct(risk.daily_pnl_pct || 0)],
                      ['Win Rate',     `${risk.win_rate ?? 0}%`],
                      ['Trades Left',  risk.trades_left ?? '—'],
                      ['Exposure',     `${risk.exposure_pct ?? 0}%`],
                      ['Drawdown',     `${risk.current_drawdown_pct ?? 0}%`],
                    ].map(([k, v]) => (
                      <div key={k} className="risk-row">
                        <span className="risk-key">{k}</span>
                        <span className="risk-val">{v}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="empty-text">Risk manager offline</p>
                )}
              </div>
            </div>

            {/* Active Trade + AI Prediction */}
            <div className="middle-grid" style={{marginTop:'1.5rem'}}>
              {/* Active trade */}
              <div className="glass-panel ai-prediction-panel">
                <h3>Active Trade</h3>
                {trade ? (
                  <div className="active-trade-card">
                    <span className="trade-badge running">● LIVE</span>
                    <h4>{trade.action}</h4>
                    <div className="trade-details">
                      <p>Order: <strong>{trade.order_id}</strong></p>
                      <p>Qty: <strong>{trade.qty}</strong></p>
                      <p>Entry: <strong>Rs {Number(trade.entry_price).toFixed(2)}</strong></p>
                      <p>PnL:&nbsp;
                        <strong className={trade.pnl_pct >= 0 ? 'text-green' : 'text-red'}>
                          {fmtPct(trade.pnl_pct ?? 0)} (Rs {Number(trade.pnl_amount ?? 0).toFixed(0)})
                        </strong>
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="empty-trade-state">
                    <Activity size={32} opacity={0.3} />
                    <p>Scanning market...</p>
                    <span className="sub-text">Waiting for AI confidence &gt; 65%</span>
                  </div>
                )}

                {/* AI prediction */}
                {pred && (
                  <div className="prediction-content" style={{marginTop:'1rem'}}>
                    <div className={`trend-indicator`}>
                      <div className={`pulse-ring ${isBull ? 'bullish' : isBear ? 'bearish' : ''}`} />
                      <div className={`trend-label ${isBull ? 'bullish' : isBear ? 'bearish' : ''}`}>
                        {isBull ? <TrendingUp size={22}/> : isBear ? <TrendingDown size={22}/> : <Activity size={22}/>}
                        &nbsp;{trend.toUpperCase()}
                      </div>
                    </div>
                    <div className="confidence-meter">
                      <div className="meter-label"><span>AI Confidence</span><span>{(conf*100).toFixed(1)}%</span></div>
                      <div className="meter-track">
                        <div className={`meter-fill ${conf >= 0.65 ? 'high' : 'low'}`} style={{width:`${conf*100}%`}} />
                      </div>
                      {conf < 0.65 && <p className="warning-text">Below threshold — no trade</p>}
                    </div>
                  </div>
                )}
              </div>

              {/* Recent trades */}
              <div className="glass-panel trades-panel">
                <div className="panel-header">
                  <h3>Recent Trades</h3>
                  <span className="badge">{metrics?.max_drawdown != null ? `Max DD: ${metrics.max_drawdown}%` : ''}</span>
                </div>
                <div className="table-responsive">
                  <table className="trades-table">
                    <thead>
                      <tr>
                        <th>Time</th><th>Action</th><th>Status</th><th style={{textAlign:'right'}}>PnL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {metrics?.recent_trades?.length > 0 ? metrics.recent_trades.map((t, i) => (
                        <tr key={i} className="trade-row">
                          <td className="time-col">{new Date(t.time).toLocaleTimeString()}</td>
                          <td><span className={`action-badge ${t.action?.includes('CALL') || t.action?.includes('BULL') ? 'call' : 'put'}`}>{t.action}</span></td>
                          <td><span className="status-dot-small closed"/>{t.status}</td>
                          <td className={`pnl-col ${t.pnl >= 0 ? 'profit' : 'loss'}`}>{fmtRs(t.pnl)}</td>
                        </tr>
                      )) : (
                        <tr><td colSpan="4" style={{textAlign:'center', color:'var(--text-muted)', padding:'2rem'}}>No closed trades yet</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </>
        )}

        {/* ════════════ SIGNALS TAB ════════════ */}
        {activeTab === 'signals' && (
          <div className="glass-panel">
            <div className="panel-header">
              <h3>Signal Trace</h3>
              <span className="badge">{signals.length} signals</span>
            </div>
            {signals.length === 0 ? (
              <p className="empty-text">No signals logged yet. Start the orchestrator to see decisions here.</p>
            ) : (
              <div className="signal-trace">
                {signals.map((s, i) => <SignalRow key={i} sig={s} />)}
              </div>
            )}
          </div>
        )}

        {/* ════════════ FEATURES TAB ════════════ */}
        {activeTab === 'features' && (
          <div className="glass-panel">
            <div className="panel-header">
              <h3>Feature Importance (Top 10)</h3>
              <button className="btn-secondary" onClick={fetchAll}>
                <RefreshCw size={14}/> Refresh
              </button>
            </div>
            {featImp
              ? <FeatureImportance data={featImp} />
              : <p className="empty-text">Feature importance not yet available. Train the model first.</p>
            }
          </div>
        )}

      </main>
    </div>
  );
}
