"""Legacy frame API preserved behind a Blueprint, with validated inputs."""
from threading import RLock
from flask import Blueprint, render_template, request, jsonify
from .chip_data import generate_data, compute_chip, compute_price_series

bp = Blueprint('chips',__name__)
_lock = RLock()
_df = generate_data()


@bp.get('/chips')
def page():
    return render_template('chips.html',total_records=len(_df), page='chips', page_title='筹码回放')


@bp.post('/api/frame')
def frame():
    from . import number
    body=request.get_json(silent=True) or {}
    if not isinstance(body,dict):
        raise ValueError('The request body must be a JSON object')
    with _lock:
        df=_df
    idx=number(body,'frame_idx',0,0,len(df)-1,True)
    window=number(body,'window_size',200,1,10000,True)
    cfg=dict(bins=number(body,'bins',40,2,100,True),time_layers=number(body,'time_layers',10,1,20,True),scheme=number(body,'scheme',1,1,3,True))
    for name in ['hist_price_min','hist_price_max']:
        cfg[name]=None if body.get(name) is None else number(body,name,0,-1e8,1e8)
    if cfg['hist_price_min'] is not None and cfg['hist_price_max'] is not None and cfg['hist_price_min']>=cfg['hist_price_max']:
        raise ValueError('The upper price bound must exceed the lower bound')
    start=max(0,idx-window+1)
    return jsonify(chip=compute_chip(df.iloc[start:idx+1],cfg),price_series=compute_price_series(df,idx,number(body,'ma_window',20,1,500,True)),latest_price=float(df.iloc[idx].price),frame_idx=idx,total=len(df),win_start=start,win_end=idx)


@bp.post('/api/regenerate')
def regenerate():
    from . import number
    global _df
    body=request.get_json(silent=True) or {}
    if not isinstance(body,dict):
        raise ValueError('The request body must be a JSON object')
    df=generate_data(number(body,'n_records',1000,20,5000,True),number(body,'seed',42,0,2**32-1,True))
    with _lock:
        _df=df
    return jsonify(status='ok',total_records=len(df))


@bp.post('/api/chips')
def market_chips():
    """Minute-volume profile, not trade-side inference or actual holder inventory."""
    from flask import current_app
    from .market import payload
    from . import number
    body = request.get_json(silent=True) or {}
    if not isinstance(body,dict):
        raise ValueError('The request body must be an object')
    data = payload(current_app,dict(symbol=body.get('symbol','50'),period='1',start=body.get('start'),end=body.get('end')))
    bars = data['future_bars']
    return jsonify(bars=bars,status=data['status'],collector=data['collector'],
                   method='minute_close_volume_profile',direction='unknown',
                   note='分钟成交量按该分钟收盘价归集；不是逐笔成交分布或真实持仓成本。行情无法可靠识别主动买卖方向，统一展示成交量。')
