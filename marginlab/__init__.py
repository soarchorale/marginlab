"""Margin Lab unified options research application."""
from pathlib import Path
import os
from flask import Flask, jsonify, render_template, request
from .analytics import DriftConfig, analyze
from .data import SYMBOLS, demo, csv_bars, records
from .pricing import greeks, stress, taylor
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def number(args, name, default, lo, hi, integer=False):
    value = float(args.get(name, default))
    if not np.isfinite(value) or not lo <= value <= hi or (integer and value != int(value)):
        raise ValueError(f'{name} must be between {lo} and {hi}' + (' and must be an integer' if integer else ''))
    return int(value) if integer else value


def pricing_params(args):
    return dict(S=number(args,'S',3,.00001,1e7), K=number(args,'K',3,.00001,1e7), T=number(args,'t',30/365,.000001,20), r=number(args,'r',.02,-1,1), q=number(args,'q',0,-1,1), sigma=number(args,'v',.2,.00001,10), kind=args.get('type','call'))


def create_app(config=None):
    app = Flask(__name__, template_folder=str(ROOT/'templates'), static_folder=str(ROOT/'static'))
    app.config.update(MAX_CONTENT_LENGTH=4*1024*1024)
    app.config.update(
        DATABASE=os.environ.get('MARGIN_DATABASE', str(ROOT/'instance'/'margin.sqlite3')),
        HISTORY_MONTHS=int(os.environ.get('MARGIN_HISTORY_MONTHS', '3')),
        MARKET_REFRESH_SECONDS=900, MARKET_RETRY_SECONDS=60, MARKET_TIMEOUT=20,
        AUTH_IDLE_TIMEOUT=1800, AUTH_MAX_AGE=28800,
        COOKIE_SECURE=os.environ.get('MARGIN_COOKIE_SECURE', '0') == '1',
        PUBLIC_ORIGIN=os.environ.get('MARGIN_PUBLIC_ORIGIN'),
        TQ_AUTH_FILE=os.environ.get('TQ_AUTH_FILE',str(ROOT/'tqauth.json')),
        TQ_QUOTE_MAX_AGE=600, MVIX_RATE=float(os.environ.get('MVIX_RATE','0.02')),
    )
    if config:
        app.config.update(config)
    if not np.isfinite(app.config['MVIX_RATE']) or not -.1 <= app.config['MVIX_RATE'] <= .5:
        raise ValueError('MVIX_RATE must be finite and between -0.1 and 0.5')
    if not 1 <= app.config['HISTORY_MONTHS'] <= 36:
        raise ValueError('HISTORY_MONTHS must be between 1 and 36')
    from .storage import initialize
    from .auth import install_auth
    from .cache import prune
    initialize(app.config['DATABASE'])
    from . import tqstore
    from .tqfeed import Feed
    tqstore.initialize(app.config['DATABASE'])
    from .history import initialize as initialize_history
    initialize_history(app.config['DATABASE'])
    app.extensions['tq_feed'] = Feed(app)
    install_auth(app)
    prune(app)
    app.json.ensure_ascii = False
    from .chips import bp as chip_blueprint
    app.register_blueprint(chip_blueprint)

    @app.errorhandler(ValueError)
    @app.errorhandler(TypeError)
    def bad_request(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(RuntimeError)
    def unavailable(error):
        return jsonify(error=str(error)), 503

    @app.get('/')
    def index():
        return render_template('index.html', page='research', page_title='波动率研究', history_cutoff=prune(app))

    @app.get('/pricing')
    def pricing_page():
        return render_template('index.html', page='pricing', page_title='定价与压力测试')

    @app.get('/method')
    def method_page():
        return render_template('index.html', page='method', page_title='指标与数据说明')

    @app.route('/settings', methods=['GET', 'POST'])
    def settings_page():
        from .cache import settings, cache_summary, prune
        from .storage import connect
        from flask import redirect
        if request.method == 'POST':
            months = number(request.form, 'history_months', 3, 1, 36, True)
            refresh = number(request.form, 'refresh_seconds', 900, 60, 86400, True)
            with connect(app.config['DATABASE']) as db:
                db.executemany('INSERT OR REPLACE INTO settings VALUES(?,?)', [('history_months',str(months)),('refresh_seconds',str(refresh))])
            prune(app)
            return redirect('/settings?saved=1')
        return render_template('settings.html', page='settings', page_title='管理员设置', settings=settings(app), cache_rows=cache_summary(app), saved=request.args.get('saved')=='1')

    @app.route('/api/research', methods=['GET','POST'])
    def research():
        args = request.get_json(silent=True) if request.method == 'POST' else request.args
        args = args or {}
        if not isinstance(args, (dict, type(request.args))):
            raise ValueError('The request body must be a JSON object')
        symbol, period, source = args.get('symbol','510050'), args.get('period','15'), args.get('source','demo')
        if source == 'live':
            if request.method != 'POST':
                return jsonify(error='刷新行情需要带 CSRF 的 POST'),405
            from .market import payload
            return jsonify(payload(app,args))
        if symbol not in SYMBOLS or period not in ['1','5','15','30','60','daily']:
            raise ValueError('Unsupported symbol or period')
        cache_info = None
        if source == 'demo':
            scenario = args.get('scenario','rising')
            if scenario not in ['rising','falling','neutral']:
                raise ValueError('Unknown demo scenario')
            df = demo(symbol, period, scenario)
        elif source == 'csv':
            df = csv_bars(args.get('csv',''))
        else:
            raise ValueError('Unknown data source')
        cfg = DriftConfig(window=number(args,'window',60,20,500,True), horizon=number(args,'horizon',16,2,100,True), correlation=number(args,'correlation',30,5,250,True), band=number(args,'band',.25,.05,.9), gap=number(args,'gap',4,1,100,True), lookback_days=number(args,'lookback_days',5,1,250,True))
        result = analyze(df,cfg,252 if period=='daily' else 252*240/int(period))
        # Fixed-maturity proxy diagnostic, never presented as contract-specific IV.
        strike = float(df.close.iloc[0])
        sensitivities = [greeks(S=float(row.close), K=strike, sigma=float(row.vix)/100) for row in df.itertuples()]
        for key in ['vanna', 'volga']:
            result['bars'][key] = [g[key] for g in sensitivities]
        return jsonify(bars=records(result['bars']), visits=result['visits'], anchor=result['anchor'], meta=dict(symbol=symbol,name=SYMBOLS[symbol][0],period=period,source=source,count=len(df),asof=str(df.timestamp.iloc[-1]),settings=cfg.__dict__,cache=cache_info,notes=['同价位差未做带内价格响应修正；未控制事件、期限结构、日内季节性。','残差为同步异常检测，非预测或交易指令。','回访阈值未按间隔分层；异常标签仅为研究候选。']))

    @app.get('/option/greek')
    @app.get('/api/greeks')
    def greek():
        params = pricing_params(request.args)
        g = greeks(**params)
        g['evaluated_price'] = taylor(number(request.args,'option_price',g['fair_price'],0,1e8),number(request.args,'d_s',0,-1e7,1e7),number(request.args,'d_v',0,-10,10),g)
        return jsonify(g)

    @app.get('/api/stress')
    @app.get('/option/pressure')
    def pressure():
        return jsonify(stress(pricing_params(request.args)))

    @app.get('/api/options/<kind>')
    @app.get('/options/<kind>')
    def options(kind):
        if kind not in ['history','value','risk']:
            raise ValueError('Unknown screening type')
        return jsonify(error='旧 ETF 筛选接口已停用；股指期权已迁移到 TqSdk /api/research'),410

    @app.get('/api/futures')
    @app.get('/future/hv')
    def futures():
        return jsonify(error='请使用登录后的 TqSdk 看板查询股指期货'),410

    @app.get('/api/rank')
    @app.get('/hdoption/rank')
    def rank():
        return jsonify(error='排行榜暂不开放，历史实现保留'),410

    @app.get('/correlation')
    @app.get('/correlation/day')
    @app.get('/option/vanna')
    @app.get('/option/volga')
    def migrated():
        # The new dashboard provides interactive correlations and Greek time series.
        from flask import redirect
        return redirect('/')

    return app
