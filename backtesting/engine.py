import pandas as pd
import numpy as np
from typing import Optional

class BacktestingEngine:
    """
    Upgraded F&O Backtesting Engine with:
    - Strategy-specific reward/risk profiles per action type
    - Realistic NSE brokerage + STT + GST simulation
    - Daily risk limits (max 3% daily loss, max 3 consecutive losses, max 8 trades)
    - Kelly Criterion-based position sizing
    - Max drawdown, Sharpe ratio, and Profit Factor analytics
    - Monte Carlo confidence interval estimation
    """

    LOT_SIZE = 50
    STARTING_CAPITAL = 500_000  # 5 Lakhs
    RISK_PER_TRADE_PCT = 0.01    # 1% Kelly baseline
    MAX_DAILY_LOSS_PCT = -3.0
    MAX_CONSEC_LOSSES = 3
    MAX_TRADES_PER_DAY = 8
    SLIPPAGE_PCT = 0.01
    BROKERAGE_FLAT = 20.0

    def __init__(self, initial_capital: float = STARTING_CAPITAL):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades = []

    def generate_mock_history(self, days: int = 60):
        """
        Generate 1-min bar based mock data aligned with Indian trading sessions.
        ~375 1-min bars per day (9:15 to 15:30).
        Add regime shifts to simulate real-world bull/bear/range periods.
        """
        bars_per_day = 375
        n_periods = days * bars_per_day
        np.random.seed(42)

        # Regime generator: each day has a bias
        daily_bias = np.repeat(np.random.choice([0.0002, -0.0002, 0.0], size=days, p=[0.4, 0.3, 0.3]), bars_per_day)

        returns = np.random.normal(loc=daily_bias, scale=0.0008, size=n_periods)
        spot = 22000 * np.cumprod(1 + returns)

        # Mock confidence and prediction from AI
        confidences = np.clip(np.random.normal(0.72, 0.08, n_periods), 0.50, 0.98)

        # Trend prediction aligned to actual future return (noisy)
        future_return = np.roll(returns, -5)  # 5-bar lookahead
        predictions = np.where(future_return > 0.001, 1, np.where(future_return < -0.001, -1, 0))
        # Add noise: 35% of predictions are wrong
        noise_mask = np.random.rand(n_periods) < 0.35
        predictions[noise_mask] = np.random.choice([1, -1, 0], size=noise_mask.sum())

        # ADX proxy: use rolling return volatility
        rolling_vol = pd.Series(np.abs(returns)).rolling(14).mean().fillna(0.001).values
        adx_proxy = np.clip(rolling_vol / rolling_vol.max() * 60, 5, 60)

        # Supertrend direction: simple rolling sign of 10-bar return
        trend_proxy = np.sign(pd.Series(returns).rolling(10).sum().fillna(0).values)

        timestamps = pd.date_range(start="2025-01-01 09:15", periods=n_periods, freq="1min")

        return pd.DataFrame({
            "timestamp": timestamps,
            "spot": spot,
            "prediction": predictions,
            "confidence": confidences,
            "adx": adx_proxy,
            "supertrend": trend_proxy,
            "atr_pct": rolling_vol * 100,
        })

    def _brokerage(self, qty: int, premium: float, is_sell: bool) -> float:
        turnover = premium * qty
        b = min(self.BROKERAGE_FLAT, 0.0003 * turnover)
        stt = 0.0005 * turnover if is_sell else 0.0
        gst = b * 0.18
        sebi = turnover * 1e-6
        return b + stt + gst + sebi

    def _kelly_quantity(self, p: float, win_cap: float) -> int:
        """Kelly fraction position sizing."""
        q = 1 - p
        b = 1.5  # avg win/loss ratio
        kelly = max(0.0, (p * b - q) / b)
        frac = kelly * 0.25  # fractional Kelly
        risk_cash = self.capital * frac
        premium_est = 100.0
        risk_per_lot = premium_est * 0.35 * self.LOT_SIZE  # ~35% SL
        lots = max(1, int(risk_cash / risk_per_lot))
        return min(lots, int(self.capital * win_cap / (premium_est * self.LOT_SIZE)))

    def run(self):
        df = self.generate_mock_history(days=60)

        print("Starting Backtest (60 days, 1-min bars)...")

        win_hist = []
        current_date = None
        daily_trades = 0
        daily_pnl = 0.0
        consec_losses = 0

        for i, row in df.iterrows():
            ts = row['timestamp']
            day = ts.date()

            # Daily reset
            if day != current_date:
                current_date = day
                daily_trades = 0
                daily_pnl = 0.0
                consec_losses = 0

            # ── Strategy Filters ─────────────────────────────────────────────
            market_hour = ts.hour * 60 + ts.minute
            if not ((9*60+20 <= market_hour <= 11*60+30) or (14*60 <= market_hour <= 15*60+15)):
                continue

            if row['confidence'] < 0.65:
                continue
            if row['prediction'] == 0 and row['adx'] < 20:
                continue
            if row['prediction'] == 1 and row['supertrend'] == -1:
                continue
            if row['prediction'] == -1 and row['supertrend'] == 1:
                continue
            if row['atr_pct'] > 2.0:
                continue

            # Daily guards
            if daily_trades >= self.MAX_TRADES_PER_DAY:
                continue
            if (daily_pnl / self.capital) * 100 <= self.MAX_DAILY_LOSS_PCT:
                continue
            if consec_losses >= self.MAX_CONSEC_LOSSES:
                continue

            # ── Position Sizing (Kelly) ──────────────────────────────────────
            p_est = len([t for t in win_hist[-20:] if t]) / 20 if len(win_hist) >= 5 else 0.60
            qty = self._kelly_quantity(p_est, win_cap=0.10) * self.LOT_SIZE

            # ── Determine Action ─────────────────────────────────────────────
            pred = row['prediction']
            if pred == 1:
                action = "SELL_PUT" if row['adx'] < 25 else "BUY_CALL"
            elif pred == -1:
                action = "SELL_CALL" if row['adx'] < 25 else "BUY_PUT"
            else:
                action = "SELL_STRANGLE"

            is_sell = "SELL" in action
            premium = max(40, (row['atr_pct'] / 100) * 22500 * 2)
            premium_with_slip = premium * (1 + self.SLIPPAGE_PCT)

            entry_broker = self._brokerage(qty, premium_with_slip, is_sell)

            # ── Simulate Outcome ─────────────────────────────────────────────
            # Win probability calibrated by confidence and ADX
            win_prob = row['confidence'] * (0.6 + 0.3 * (row['adx'] / 60))
            won = np.random.rand() < win_prob

            if is_sell:
                sl_pct = 0.45
                target_pct = 0.70
                gross_pnl = premium * qty * target_pct if won else -premium * qty * sl_pct
            else:
                sl_pct = 0.30
                target_pct = 0.60
                gross_pnl = premium_with_slip * qty * target_pct if won else -premium_with_slip * qty * sl_pct

            exit_broker = self._brokerage(qty, premium_with_slip * (1 + (target_pct if won else -sl_pct)), not is_sell)
            net_pnl = gross_pnl - entry_broker - exit_broker

            self.capital += net_pnl
            daily_pnl += net_pnl
            daily_trades += 1
            win_hist.append(won)

            if net_pnl < 0:
                consec_losses += 1
            else:
                consec_losses = 0

            self.trades.append({
                "timestamp": ts,
                "action": action,
                "prediction": pred,
                "confidence": row['confidence'],
                "adx": row['adx'],
                "qty": qty,
                "won": won,
                "gross_pnl": round(gross_pnl, 2),
                "net_pnl": round(net_pnl, 2),
                "capital_after": round(self.capital, 2),
            })

        self.print_stats()

    def print_stats(self):
        df = pd.DataFrame(self.trades)
        if df.empty:
            print("No trades were executed.")
            return

        wins = df[df['net_pnl'] > 0]
        losses = df[df['net_pnl'] <= 0]
        win_rate = len(wins) / len(df)
        gross_profit = wins['net_pnl'].sum()
        gross_loss = abs(losses['net_pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        df['peak'] = df['capital_after'].cummax()
        df['drawdown'] = (df['peak'] - df['capital_after']) / df['peak']
        max_dd = df['drawdown'].max()

        # Sharpe: annualized (assume ~20 trading days/month, 60 bars)
        daily_ret = df.groupby(df['timestamp'].dt.date)['net_pnl'].sum() / self.initial_capital
        sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else 0

        # Monte Carlo PnL confidence interval (1000 resamplings)
        mc_finals = []
        for _ in range(1000):
            sampled = np.random.choice(df['net_pnl'].values, size=len(df), replace=True)
            mc_finals.append(self.initial_capital + sampled.sum())
        mc_5 = np.percentile(mc_finals, 5)
        mc_95 = np.percentile(mc_finals, 95)

        print('\n=== AI F&O Backtest Results ===')
        print(f'  Total Trades   : {len(df)}')
        print(f'  Win Rate       : {win_rate:.1%}  ({len(wins)}W / {len(losses)}L)')
        print(f'  Profit Factor  : {profit_factor:.2f}')
        print(f'  Sharpe Ratio   : {sharpe:.2f}')
        print(f'  Max Drawdown   : {max_dd:.1%}')
        print(f'  Initial Cap    : Rs {self.initial_capital:,.0f}')
        print(f'  Final Cap      : Rs {self.capital:,.0f}')
        print(f'  Net Profit     : Rs {(self.capital - self.initial_capital):,.0f}')
        print(f'  MC 5th  pctile : Rs {mc_5:,.0f}')
        print(f'  MC 95th pctile : Rs {mc_95:,.0f}')
        print('================================')



if __name__ == "__main__":
    engine = BacktestingEngine()
    engine.run()
