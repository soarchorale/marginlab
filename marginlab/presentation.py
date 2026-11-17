"""Explicit display-only carries. Raw observations and statistics remain unchanged."""
import pandas as pd


def display_values(frame, snapshots, period):
    result=frame.copy()
    result['vix_display']=result.vix
    result['vix_display_kind']='observed'
    result['vix_origin']=None
    step=1440 if period=='daily' else int(period)
    closes={r['timestamp'][:10]:r for r in snapshots if r['timestamp'][11:16]=='14:57' and r['quality']=='valid' and r['value'] is not None}
    for i,row in result.iterrows():
        stamp=pd.Timestamp(row.timestamp)
        endpoint=stamp.normalize()+pd.Timedelta(hours=15) if period=='daily' else stamp+pd.Timedelta(minutes=step)
        base=closes.get(stamp.date().isoformat())
        if pd.isna(row.vix) and base and endpoint.strftime('%H:%M') in ('14:58','14:59','15:00'):
            result.at[i,'vix_display']=base['value']
            result.at[i,'vix_display_kind']='closing_auction_carry'
            result.at[i,'vix_origin']=base['timestamp']
        elif pd.isna(row.vix) and stamp.strftime('%H:%M')=='13:00' and i>0 and step==1:
            previous=result.iloc[i-1]
            before=pd.Timestamp(previous.timestamp)
            if before.date()==stamp.date() and (before+pd.Timedelta(minutes=step)).strftime('%H:%M')=='11:30' and pd.notna(previous.vix):
                result.at[i,'vix_display']=previous.vix
                result.at[i,'vix_display_kind']='lunch_carry'
                result.at[i,'vix_origin']=(before+pd.Timedelta(minutes=step)).isoformat()
    return result


def correlation_display(frame, window):
    result=frame.copy()
    for key in ('corr_return','corr_level'):
        result[key+'_display']=result[key]
        result[key+'_carried']=False
        result[key+'_origin']=None
        for i,row in result.iterrows():
            if row.get('vix_display_kind')!='lunch_carry' or i==0 or pd.isna(result.iloc[i-1][key]):continue
            previous=result.iloc[i-1]
            # Only bridge warmup caused by the one missing opening observation.
            # Other missing trading observations terminate the display carry.
            for j in range(i,min(len(result),i+window+1)):
                current=result.iloc[j]
                if pd.Timestamp(current.timestamp).date()!=pd.Timestamp(row.timestamp).date():break
                if pd.notna(current[key]):break
                if j>i and pd.isna(current.vix):break
                result.at[j,key+'_display']=previous[key]
                result.at[j,key+'_carried']=True
                result.at[j,key+'_origin']=str(previous.timestamp)
    return result
