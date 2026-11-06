from datetime import date, datetime
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from etf_limit_impact.data import Component, DayQuotes, PCF, write_csv
from etf_limit_impact.limits import (Bounds, LimitRules, bounds_from_reference,
                                   intervals, observe_at, observe_day, touch)


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.component = Component('600000.SH', '合成股票', D(100), '1', D(0), D(0))
        self.pcf = PCF('159338', date(2025, 1, 3), date(2025, 1, 2), D(1000), (self.component,))
        self.previous = DayQuotes({'600000.SH': {datetime(2025, 1, 2, 15): D('9.95')}})

    def test_decimal_tick_rounding_and_low_price_minimum(self):
        self.assertEqual(bounds_from_reference(D('9.95'), D('.1')), (D('10.95'), D('8.96')))
        self.assertEqual(bounds_from_reference(D('.04'), D('.1')), (D('.05'), D('.03')))

    def test_missing_previous_exact_close_and_official_only_remain_unknown(self):
        previous = DayQuotes({'600000.SH': {datetime(2025, 1, 2, 14, 59): D(10)}})
        self.assertEqual(LimitRules().resolve(self.component, self.pcf, previous).evidence, 'unknown')
        self.assertEqual(LimitRules(allow_estimated=False).resolve(self.component, self.pcf, self.previous).evidence, 'unknown')

    def test_provided_no_limit_and_conflicting_rows(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'limits.csv'
            row = dict(date='2025-01-03', symbol='600000.SH', upper_limit='12', lower_limit='8', no_limit='False', source='synthetic')
            write_csv(path, [row])
            bounds = LimitRules(path).resolve(self.component, self.pcf, self.previous)
            self.assertEqual((bounds.upper, bounds.lower, bounds.evidence), (D(12), D(8), 'provided'))
            write_csv(path, [row | dict(no_limit='True', upper_limit='', lower_limit='')])
            self.assertEqual(touch(D(100), LimitRules(path).resolve(self.component, self.pcf, self.previous)), 'NO_LIMIT')
            write_csv(path, [row, row | dict(upper_limit='13')])
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                LimitRules(path)

    def test_future_outlier_does_not_change_signal_status(self):
        signal = datetime(2025, 1, 3, 9, 35)
        quotes = DayQuotes({'600000.SH': {signal: D(11), datetime(2025, 1, 3, 14): D(20)}})
        bounds = Bounds(D(11), D(9), 'estimated')
        self.assertEqual(observe_at('600000.SH', quotes, signal, bounds)[:2], ('UP', True))
        daily, _, _ = observe_day(self.component, self.pcf, quotes, bounds)
        self.assertIsNone(daily['day_touched'])
        self.assertEqual(daily['evidence'], 'unknown')
        self.assertEqual(observe_at('600000.SH', quotes, signal, bounds)[0], 'UP')

    def test_intraday_touch_missing_minute_and_lunch_are_separate(self):
        times = [datetime(2025, 1, 3, h, m) for h, m in [(9,35),(9,36),(9,38),(11,30),(13,0)]]
        self.assertEqual(len(intervals(times)), 4)
        quotes = DayQuotes({'600000.SH': {**{at:D(11) for at in times}, datetime(2025, 1, 3, 15):D(10)}})
        daily, _, _ = observe_day(self.component, self.pcf, quotes, Bounds(D(11),D(9),'provided'))
        self.assertTrue(daily['day_touched'])
        self.assertEqual(daily['up_minutes'], 5)
        self.assertEqual(daily['up_spans'], '09:35~09:36; 09:38~09:38; 11:30~11:30; 13:00~13:00')
        self.assertEqual(observe_at('600000.SH', quotes, datetime(2025,1,3,9,37), Bounds(D(11),D(9),'provided'))[0], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
