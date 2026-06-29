import traceback
from flask import Flask, jsonify, render_template, request
from analyzer import analyze, SYMBOLS, TIMEFRAMES

app = Flask(__name__)


@app.route('/')
def index():
    return render_template(
        'index.html',
        symbols=list(SYMBOLS.keys()),
        timeframes=list(TIMEFRAMES.keys()),
    )


@app.route('/api/analyze')
def api_analyze():
    symbol    = request.args.get('symbol', 'USD/JPY')
    timeframe = request.args.get('timeframe', '1h')

    if symbol not in SYMBOLS:
        return jsonify({'error': f'不明な通貨ペア: {symbol}'}), 400
    if timeframe not in TIMEFRAMES:
        return jsonify({'error': f'不明な時間足: {timeframe}'}), 400

    try:
        result = analyze(symbol, timeframe)
        if result is None:
            return jsonify({'error': 'データの取得または計算に失敗しました。しばらく経ってから再試行してください。'}), 500
        return jsonify(result)
    except Exception:
        traceback.print_exc()
        return jsonify({'error': 'サーバー内部エラーが発生しました。'}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
