"""Bounded ranking retrieval; historical statistics use information available that day."""
from datetime import date, timedelta
import json
from urllib.request import Request, urlopen
import pandas as pd
from functools import lru_cache
from time import time


@lru_cache(maxsize=128)
def day_rows(day, cache_bucket=0):
    req=Request('https://ds.huidian.net/szoc/getSCRankingDatas',data=json.dumps({'day':day}).encode(),headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=12) as response:
        return json.load(response).get('data',[])


def fetch_ranking(start=None,end=None):
    finish=date.fromisoformat(end) if end else date.today()
    begin=date.fromisoformat(start) if start else finish-timedelta(days=7)
    if not 0 <= (finish-begin).days <= 31:
        raise ValueError('The ranking query must span 1-32 calendar days')
    result, history, previous = [], {}, {}
    try:
        for day in pd.date_range(begin,finish):
            rows=day_rows(day.strftime('%Y%m%d'), int(time()//60) if day.date() == date.today() else 0)
            if not rows:
                continue
            current={row['userName']:row for row in rows}
            for name,row in current.items():
                old=history.get(name)
                rank=float(row['sortID'])
                result.append(dict(userName=name,curDate=str(day.date()),sortID=rank,totalProfit=row['totalProfit'],profit_change=float(row['totalProfit'])-float(old['totalProfit']) if old else None,commission=float(row['commission'])-float(old['commission']) if old and name in previous else None,rank_change=float(previous[name]['sortID'])-rank if name in previous else '重新进榜' if old else '区间首次出现',days_on_list=old['days_on_list']+1 if old else 1,best_rank=min(old['best_rank'],rank) if old else rank))
                history[name]=dict(row,days_on_list=result[-1]['days_on_list'],best_rank=result[-1]['best_rank'])
            for name,row in previous.items():
                if name not in current:
                    result.append(dict(userName=name,curDate=str(day.date()),rank_change='掉榜',sortID=None,totalProfit=row['totalProfit'],profit_change=None,commission=None,days_on_list=history[name]['days_on_list'],best_rank=history[name]['best_rank']))
            previous=current
    except Exception as exc:
        raise RuntimeError('Ranking service request failed; retry later') from exc
    return sorted(result,key=lambda row:row['curDate'],reverse=True)
