import math
import warnings
import pandas as pd
import numpy as np
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings('ignore')

SYMBOLS = {
    'USD/JPY': 'USDJPY=X',
    'EUR/USD': 'EURUSD=X',
    'GBP/JPY': 'GBPJPY=X',
    'EUR/JPY': 'EURJPY=X',
    'AUD/USD': 'AUDUSD=X',
    'GBP/USD': 'GBPUSD=X',
}

TIMEFRAMES = {
    '1h':  {'interval': '1h',  'period': '60d',  'resample': None},
    '4h':  {'interval': '1h',  'period': '120d', 'resample': '4h'},
    '1日': {'interval': '1d',  'period': '2y',   'resample': None},
}


# ──────────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────────

def _safe_list(series):
    result = []
    for v in series:
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            result.append(None)
        else:
            result.append(float(v))
    return result


# ──────────────────────────────────────────────
# テクニカル指標（手動実装）
# ──────────────────────────────────────────────

def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain  = delta.clip(lower=0).rolling(n).mean()
    loss  = (-delta.clip(upper=0)).rolling(n).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _macd(s: pd.Series, fast=12, slow=26, signal=9):
    macd_line   = _ema(s, fast) - _ema(s, slow)
    signal_line = _ema(macd_line, signal)
    hist        = macd_line - signal_line
    return macd_line, signal_line, hist


def _bbands(s: pd.Series, n: int = 20, std: float = 2.0):
    mid   = _sma(s, n)
    sigma = s.rolling(n).std()
    upper = mid + std * sigma
    lower = mid - std * sigma
    return upper, mid, lower


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low,
                    (high - prev_close).abs(),
                    (low  - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


# ──────────────────────────────────────────────
# データ取得
# ──────────────────────────────────────────────

def fetch_data(symbol_key: str, timeframe_key: str):
    cfg    = TIMEFRAMES[timeframe_key]
    ticker = SYMBOLS[symbol_key]

    df = yf.download(ticker, interval=cfg['interval'], period=cfg['period'], progress=False)
    if df is None or df.empty:
        return None

    # yfinance v0.2+ は MultiIndex を返す場合がある
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    df.dropna(subset=['Open', 'High', 'Low', 'Close'], inplace=True)

    if cfg['resample']:
        df = df.resample(cfg['resample']).agg(
            {'Open': 'first', 'High': 'max', 'Low': 'min',
             'Close': 'last', 'Volume': 'sum'}
        ).dropna()

    return df


# ──────────────────────────────────────────────
# 指標計算
# ──────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df['MA20'] = _sma(df['Close'], 20)
    df['MA50'] = _sma(df['Close'], 50)

    df['RSI'] = _rsi(df['Close'], 14)

    df['MACD'], df['MACD_signal'], df['MACD_hist'] = _macd(df['Close'])

    df['BB_upper'], df['BB_mid'], df['BB_lower'] = _bbands(df['Close'])

    df['ATR'] = _atr(df['High'], df['Low'], df['Close'])

    return df.dropna()


# ──────────────────────────────────────────────
# シグナル生成
# ──────────────────────────────────────────────

def _compute_signals(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    signals = {}

    rsi = latest['RSI']
    if rsi < 30:
        signals['RSI'] = ('Buy', f'RSI = {rsi:.1f}（売られすぎ水準）')
    elif rsi > 70:
        signals['RSI'] = ('Sell', f'RSI = {rsi:.1f}（買われすぎ水準）')
    else:
        signals['RSI'] = ('Neutral', f'RSI = {rsi:.1f}（中立）')

    if latest['MACD'] > latest['MACD_signal']:
        signals['MACD'] = ('Buy', 'MACDラインがシグナル線を上回っている（上昇モメンタム）')
    else:
        signals['MACD'] = ('Sell', 'MACDラインがシグナル線を下回っている（下降モメンタム）')

    if latest['MA20'] > latest['MA50']:
        signals['移動平均'] = ('Buy', 'MA20 > MA50（短期 > 長期 ＝ 上昇トレンド）')
    else:
        signals['移動平均'] = ('Sell', 'MA20 < MA50（短期 < 長期 ＝ 下降トレンド）')

    bb_range = latest['BB_upper'] - latest['BB_lower']
    bb_pos   = (latest['Close'] - latest['BB_lower']) / bb_range if bb_range > 0 else 0.5
    if bb_pos < 0.2:
        signals['ボリンジャーバンド'] = ('Buy',  f'下限バンド付近（位置 {bb_pos:.2f}）— 反発に期待')
    elif bb_pos > 0.8:
        signals['ボリンジャーバンド'] = ('Sell', f'上限バンド付近（位置 {bb_pos:.2f}）— 反落に注意')
    else:
        signals['ボリンジャーバンド'] = ('Neutral', f'バンド中央付近（位置 {bb_pos:.2f}）')

    return signals


# ──────────────────────────────────────────────
# 機械学習
# ──────────────────────────────────────────────

FEATURES = ['RSI', 'MACD', 'MACD_signal', 'MACD_hist', 'ATR', 'MA_diff', 'BB_pos']


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['MA_diff'] = (df['MA20'] - df['MA50']) / df['MA50']
    bb_range      = (df['BB_upper'] - df['BB_lower']).replace(0, np.nan)
    df['BB_pos']  = (df['Close'] - df['BB_lower']) / bb_range
    return df


def _train_ml(df: pd.DataFrame):
    df = _build_features(df).dropna(subset=FEATURES)

    df['future_ret'] = df['Close'].shift(-3) / df['Close'] - 1
    df['target']     = (df['future_ret'] > 0).astype(int)
    df = df.dropna(subset=['target'])

    X = df[FEATURES].values[:-3]
    y = df['target'].values[:-3]
    if len(X) < 60:
        return None, None

    scaler  = StandardScaler()
    X_sc    = scaler.fit_transform(X)
    model   = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
    model.fit(X_sc, y)
    return model, scaler


def _predict_ml(df: pd.DataFrame, model, scaler):
    if model is None:
        return None, None

    df  = _build_features(df).dropna(subset=FEATURES)
    row = df[FEATURES].iloc[-1:].values
    if np.any(np.isnan(row)):
        return None, None

    row_sc     = scaler.transform(row)
    pred       = int(model.predict(row_sc)[0])
    proba      = model.predict_proba(row_sc)[0]
    confidence = float(max(proba) * 100)
    return pred, confidence


# ──────────────────────────────────────────────
# チャートデータ整形
# ──────────────────────────────────────────────

def _prepare_chart(df: pd.DataFrame, limit: int = 120) -> dict:
    d     = df.tail(limit)
    dates = d.index.strftime('%Y-%m-%d %H:%M').tolist()

    return {
        'dates':       dates,
        'open':        _safe_list(d['Open']),
        'high':        _safe_list(d['High']),
        'low':         _safe_list(d['Low']),
        'close':       _safe_list(d['Close']),
        'MA20':        _safe_list(d['MA20']),
        'MA50':        _safe_list(d['MA50']),
        'BB_upper':    _safe_list(d['BB_upper']),
        'BB_mid':      _safe_list(d['BB_mid']),
        'BB_lower':    _safe_list(d['BB_lower']),
        'RSI':         _safe_list(d['RSI']),
        'MACD':        _safe_list(d['MACD']),
        'MACD_signal': _safe_list(d['MACD_signal']),
        'MACD_hist':   _safe_list(d['MACD_hist']),
    }


# ──────────────────────────────────────────────
# メイン分析エントリーポイント
# ──────────────────────────────────────────────

def analyze(symbol_key: str, timeframe_key: str):
    df = fetch_data(symbol_key, timeframe_key)
    if df is None or len(df) < 60:
        return None

    df = add_indicators(df)
    if df.empty:
        return None

    signals        = _compute_signals(df)
    model, scaler  = _train_ml(df)
    ml_pred, ml_conf = _predict_ml(df, model, scaler)

    buy  = sum(1 for v in signals.values() if v[0] == 'Buy')
    sell = sum(1 for v in signals.values() if v[0] == 'Sell')
    overall = 'Buy' if buy > sell else ('Sell' if sell > buy else 'Neutral')

    ml_info = None
    if ml_pred is not None:
        ml_info = {'signal': 'Buy' if ml_pred == 1 else 'Sell',
                   'confidence': round(ml_conf, 1)}

    latest = df.iloc[-1]

    return {
        'symbol':    symbol_key,
        'timeframe': timeframe_key,
        'price':     round(float(latest['Close']), 5),
        'overall':   overall,
        'ml':        ml_info,
        'signals':   {k: {'direction': v[0], 'reason': v[1]} for k, v in signals.items()},
        'indicators': {
            'RSI':         round(float(latest['RSI']), 2),
            'MACD':        round(float(latest['MACD']), 6),
            'MACD_signal': round(float(latest['MACD_signal']), 6),
            'MA20':        round(float(latest['MA20']), 5),
            'MA50':        round(float(latest['MA50']), 5),
            'BB_upper':    round(float(latest['BB_upper']), 5),
            'BB_lower':    round(float(latest['BB_lower']), 5),
            'ATR':         round(float(latest['ATR']), 5),
        },
        'chart': _prepare_chart(df),
    }
