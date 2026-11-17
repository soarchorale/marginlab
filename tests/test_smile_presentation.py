import json
import unittest
import numpy as np
import pandas as pd
from marginlab.smile import forward_price,implied_volatility,build_smile
from marginlab.presentation import display_values,correlation_display


class SmileTests(unittest.TestCase):
    def test_recovers_forward_iv_for_both_sides_and_rejects_bounds(self):
        for side in ('CALL','PUT'):
            for strike in (90,100,110):
                price=forward_price(101,strike,30/365,.02,.3,side)
                self.assertAlmostEqual(implied_volatility(price,101,strike,30/365,.02,side),30,places=6)
        self.assertIsNone(implied_volatility(.1,120,100,.1,.02,'CALL'))
        self.assertIsNone(implied_volatility(200,120,100,.1,.02,'CALL'))

    def test_missing_bid_does_not_become_iv_or_connect_across_strikes(self):
        at=pd.Timestamp('2026-11-16T10:00',tz='Asia/Shanghai').timestamp()
        expiry=at+30*86400
        details=json.dumps({'near':{'expiry':expiry,'forward':100},'far':{'expiry':expiry,'forward':100},'rate':.02})
        quotes=json.dumps([dict(symbol='test',expiry=expiry,strike=100,side='CALL',bid=None,ask=5)])
        result=build_smile('2026-11-16T10:00:00',details,quotes)
        self.assertEqual(len(result['terms']),1)
        self.assertIsNone(result['terms'][0]['points'][0]['iv'])
        self.assertEqual(result['terms'][0]['excluded'],1)


class PresentationTests(unittest.TestCase):
    def frame(self,times,values):
        return pd.DataFrame({'timestamp':pd.to_datetime(times),'vix':values})

    def test_auction_uses_exact_same_day_valid_anchor_and_keeps_raw(self):
        df=self.frame(['2026-11-16T14:57','2026-11-16T14:58','2026-11-16T14:59','2026-11-17T14:59'],[np.nan,np.nan,22,np.nan])
        snapshots=[dict(timestamp='2026-11-16T14:57:00',quality='valid',value=18)]
        out=display_values(df,snapshots,'1')
        self.assertEqual(out.vix_display.iloc[:3].tolist(),[18,18,22])
        self.assertTrue(pd.isna(out.vix_display.iloc[3]))
        pd.testing.assert_series_equal(df.vix,out.vix)
        snapshots[0]['quality']='reference'
        self.assertTrue(pd.isna(display_values(df,snapshots,'1').vix_display.iloc[0]))

    def test_lunch_bridges_only_opening_bar_and_does_not_cross_day(self):
        df=self.frame(['2026-11-16T11:29','2026-11-16T13:00','2026-11-16T13:01','2026-11-17T13:00'],[18,np.nan,np.nan,np.nan])
        out=display_values(df,[],'1')
        self.assertEqual(out.vix_display.iloc[1],18)
        self.assertTrue(out.vix_display.iloc[2:].isna().all())
        self.assertTrue(pd.isna(out.vix.iloc[1]))

    def test_lunch_correlation_display_does_not_change_calculated_values(self):
        df=self.frame(['2026-11-16T11:29','2026-11-16T13:00','2026-11-16T13:01','2026-11-16T13:02','2026-11-16T13:03'],[18,np.nan,19,np.nan,20])
        df=display_values(df,[],'1')
        df['corr_return']=[-.8,np.nan,np.nan,np.nan,np.nan]
        df['corr_level']=[-.7,np.nan,np.nan,np.nan,np.nan]
        out=correlation_display(df,30)
        self.assertEqual(out.corr_return_display.iloc[:3].tolist(),[-.8,-.8,-.8])
        self.assertTrue(out.corr_return_display.iloc[3:].isna().all())
        pd.testing.assert_series_equal(out.corr_return,df.corr_return)

    def test_no_lunch_gap_for_already_observed_values(self):
        df=self.frame(['2026-11-16T11:29','2026-11-16T13:00'],[18,19])
        out=display_values(df,[],'1')
        self.assertEqual(out.vix_display.tolist(),[18,19])
        self.assertEqual(out.vix_display_kind.tolist(),['observed','observed'])


if __name__=='__main__':unittest.main()
