from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal as D
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fixtures
from etf_limit_impact.cli import main
from etf_limit_impact.data import read_csv, write_csv
from etf_limit_impact.report import detail_name
from etf_limit_impact.stock_cost import cost_metrics


class StockCostTests(unittest.TestCase):
    def test_exact_classification_unknowns_and_sign(self):
        only_fee = cost_metrics('-2', '2', '2000', '10000')
        self.assertTrue(only_fee['fee_only'])
        self.assertEqual(only_fee['etf_cost_impact'], D(2))
        self.assertEqual(only_fee['etf_cost_impact_per_share'], D('.001'))
        self.assertEqual(only_fee['etf_cost_impact_ratio'], D('.0002'))
        self.assertFalse(cost_metrics('-1.999999', '2', 2000, 10000)['fee_only'])
        self.assertFalse(cost_metrics('0', '0', 2000, 10000)['fee_only'])
        self.assertFalse(cost_metrics(None, '2', 2000, 10000)['fee_only'])
        self.assertIsNone(cost_metrics(None, '2', 2000, 10000)['etf_cost_impact'])
        self.assertIsNone(cost_metrics('-2', '2', None, None)['etf_cost_impact_per_share'])
        self.assertEqual(cost_metrics('38', '2', 2000, 7620)['etf_cost_impact'], D(-38))

    def test_cli_hides_fee_only_stock_but_keeps_csv_fees_and_reconciliation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run, _, _ = fixtures.fixture(root)
            fills = read_csv(run / 'fills.csv')
            for row in fills:
                if row['trade_id'] == 'T1' and row['symbol'] == '600000.SH':
                    row['price'] = '11'  # Same as signal: only the original 2 yuan fee remains.
            write_csv(run / 'fills.csv', fills)
            attribution = read_csv(run / 'trade_attribution.csv')
            attribution[0]['cash_substitution_pnl'] = '-33'  # Previously 7, a 40 yuan cost increase.
            write_csv(run / 'trade_attribution.csv', attribution)
            trades = read_csv(run / 'trades.csv')
            trades[0]['net_pnl'] = '60'
            write_csv(run / 'trades.csv', trades)
            hashes = {p: sha256(p.read_bytes()).hexdigest() for p in run.rglob('*') if p.is_file()}
            output = root / 'analysis'
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
                result = main(['analyze', '--run-dir', str(run), '--output', str(output)])
            self.assertEqual(result, 0, errors.getvalue())
            self.assertEqual(hashes, {p: sha256(p.read_bytes()).hexdigest() for p in hashes})
            stocks = {(r['trade_id'], r['symbol']): r for r in read_csv(output / 'stock_contributions.csv')}
            row = stocks['T1', '600000.SH']
            self.assertEqual(row['fee_only'], 'True')
            self.assertEqual(D(row['stock_pnl']), D(-2))
            self.assertEqual(D(row['stock_fees']), D(2))
            self.assertEqual(D(row['etf_cost_impact_per_share']), D('.001'))
            self.assertEqual(D(row['etf_cost_impact_ratio']), D(2) / D(7620))
            physical = stocks['T1', '000001.SZ']
            self.assertEqual(D(physical['physical_price_effect']), D(0))
            self.assertEqual(D(physical['stock_pnl']), D('-.4'))
            self.assertTrue(physical['fee_only'] == 'True')
            redeem = stocks['T2', '600000.SH']
            self.assertEqual(D(redeem['etf_cost_impact']), -D(redeem['cash_substitution_pnl']))
            detail = (output / 'trades' / detail_name('T1')).read_text(encoding='utf-8')
            self.assertNotIn('合成代理乙', detail)
            self.assertIn('已隐藏仅税费行', detail)
            self.assertIn('每份ETF成本影响', detail)
            summaries = read_csv(output / 'trade_summary.csv')
            self.assertTrue(all(r['reconciliation'] == 'matched' for r in summaries))
            self.assertEqual(D(summaries[0]['final_net_pnl']), D(60))
            self.assertIn(('T1', '600000.SH'), {(r['trade_id'], r['symbol']) for r in read_csv(output / 'limit_exposures.csv')})


if __name__ == '__main__':
    unittest.main()
