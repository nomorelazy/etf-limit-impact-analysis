from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal as D
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fixtures
from etf_limit_impact.analysis import analyze
from etf_limit_impact.cli import main
from etf_limit_impact.data import read_csv, write_csv


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run, self.pcf, self.market = fixtures.fixture(self.root)
        self.output = self.root/'analysis'

    def analyze(self, **kwargs):
        return analyze(self.run, self.output, progress=lambda message: None, **kwargs)

    def test_complete_cli_reports_weights_cash_and_unchanged_source(self):
        hashes = {str(p): sha256(p.read_bytes()).hexdigest() for p in self.run.rglob('*') if p.is_file()}
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
            result = main(['analyze','--run-dir',str(self.run),'--output',str(self.output)])
        self.assertEqual(result, 0, errors.getvalue())
        self.assertEqual(hashes, {name:sha256(Path(name).read_bytes()).hexdigest() for name in hashes})
        summary = json.loads((self.output/'summary.json').read_text(encoding='utf-8'))
        self.assertEqual((summary['trade_count'], summary['matched_trades']), (2,2))
        self.assertEqual(D(summary['cash_substitution_pnl']), D(19))
        self.assertEqual(D(summary['estimated_cash_pnl']), D(-2))
        rows = {(r['trade_id'],r['symbol']):r for r in read_csv(self.output/'stock_contributions.csv')}
        cash = rows['T1','600000.SH']
        self.assertEqual(D(cash['cash_substitution_pnl']), D(38))
        self.assertEqual(D(cash['stock_quantity']), D(400))
        self.assertEqual(D(cash['weight']), D(4400)/D(7620))
        self.assertEqual(cash['signal_status'], 'UP')
        partial = rows['T1','000002.SZ']
        self.assertEqual((D(partial['filled_stock_quantity']),D(partial['valued_stock_quantity'])),(D(100),D(100)))
        self.assertEqual(D(partial['cash_substitution_pnl']), D(-31))
        self.assertEqual(D(rows['T2','600001.SH']['cash_substitution_pnl']), D(-2))
        self.assertEqual(D(rows['T2','000003.SZ']['cash_substitution_pnl']), D(-5))
        physical = rows['T2','000001.SZ']
        self.assertEqual(physical['signal_touched'], 'False')
        self.assertEqual(physical['day_touched'], 'True')
        self.assertEqual(D(physical['cash_substitution_pnl']), D(0))
        self.assertEqual(D(physical['physical_price_effect']), D(10))
        trade_rows = read_csv(self.output/'trade_summary.csv')
        self.assertEqual(D(trade_rows[0]['signal_cash_pnl']), D(7))
        self.assertEqual(D(trade_rows[1]['signal_cash_pnl']), D(19))
        html = (self.output/'report.html').read_text(encoding='utf-8')
        self.assertIn('全天曾触价', html)
        self.assertIn('整篮预估现金E/A影响', html)
        self.assertTrue((self.output/'weight_vs_cash_pnl.svg').is_file())
        self.assertEqual(len(list((self.output/'trades').glob('*.html'))), 2)
        self.assertTrue(all(r['reconciliation']=='matched' for r in trade_rows))

    def test_official_only_preserves_unknown_classification(self):
        self.analyze(allow_estimated=False)
        rows = read_csv(self.output/'stock_contributions.csv')
        self.assertTrue(all(r['signal_touched']=='' for r in rows))
        self.assertEqual(read_csv(self.output/'limit_exposures.csv'), [])
        summary = json.loads((self.output/'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['matched_trades'], 2)
        self.assertGreater(summary['unknown_limit_stock_days'], 0)

    def test_corrupt_saved_pnl_and_fee_mismatch_fail(self):
        fixtures.rewrite(self.run/'trade_attribution.csv',lambda rows:[rows[0]|dict(cash_substitution_pnl='8'),rows[1]])
        with self.assertRaisesRegex(ValueError,'does not reconcile'):
            self.analyze()
        status=json.loads((self.output/'analysis_status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['state'],'failed')
        self.output=self.root/'fees_failure'
        fixtures.rewrite(self.run/'fees.csv',lambda rows:[rows[0]|dict(total='999'),*rows[1:]])
        with self.assertRaisesRegex(ValueError,'fee sources disagree'):
            self.analyze()

    def test_unpriced_stock_keeps_unknown_not_zero(self):
        fixtures.rewrite(self.run/'substitution_pricing.csv',lambda rows:[])
        fixtures.rewrite(self.run/'trade_attribution.csv',lambda rows:[rows[0]|dict(cash_substitution_pnl=''),rows[1]])
        self.analyze()
        rows = {(r['trade_id'],r['symbol']):r for r in read_csv(self.output/'stock_contributions.csv')}
        self.assertEqual(rows['T1','000002.SZ']['cash_substitution_pnl'],'')
        self.assertEqual(read_csv(self.output/'trade_summary.csv')[0]['reconciliation'],'incomplete')

    def test_priced_unpaid_stock_does_not_finalize_pending_trade_pnl(self):
        fixtures.rewrite(self.run/'trades.csv',lambda rows:[rows[0]|dict(state='APPLIED'),rows[1]])
        fixtures.rewrite(self.run/'cash_settlements.csv',lambda rows:[rows[0]|dict(paid='False'),rows[1]])
        self.analyze()
        trade=read_csv(self.output/'trade_summary.csv')[0]
        self.assertEqual(trade['final_net_pnl'],'')
        self.assertEqual(D(trade['cash_substitution_pnl']),D(7))
        self.assertEqual(trade['reconciliation'],'matched')

    def test_no_overwrite_and_no_output_inside_raw_data(self):
        self.analyze()
        with self.assertRaisesRegex(ValueError,'not empty'):
            self.analyze()
        with self.assertRaisesRegex(ValueError,'raw data'):
            analyze(self.run,self.market/'analysis',progress=lambda x:None)

    def test_empty_selection_still_generates_valid_report(self):
        from datetime import date
        self.analyze(start=date(2026,1,1))
        summary=json.loads((self.output/'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['trade_count'],0)
        self.assertTrue((self.output/'report.html').is_file())

    def test_missing_signal_quote_is_incomplete_and_not_backfilled(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from datetime import datetime
        path=self.market/'2025-01/synthetic.parquet'
        rows=[r for r in pq.read_table(path).to_pylist() if not (
            r['thscode']=='600000.SH' and r['time']==datetime(2025,1,3,9,35))]
        pq.write_table(pa.Table.from_pylist(rows),path)
        self.analyze()
        row=next(r for r in read_csv(self.output/'stock_contributions.csv') if r['trade_id']=='T1' and r['symbol']=='600000.SH')
        self.assertEqual(row['signal_status'],'UNKNOWN')
        self.assertEqual(row['cash_substitution_pnl'],'')
        self.assertEqual(row['signal_stock_price'],'')


if __name__ == '__main__':
    unittest.main()
