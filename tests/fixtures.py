"""Synthetic backtest outputs; no private market data or reports."""

from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import json

import pyarrow as pa
import pyarrow.parquet as pq

from etf_limit_impact.data import dump_json, write_csv


def fixture(root):
    root = Path(root)
    run, pcf, market = root/'run', root/'pcf', root/'market'
    run.mkdir(parents=True)
    (pcf/'159338').mkdir(parents=True)
    (market/'2025-01').mkdir(parents=True)
    pcf_path = pcf/'159338/20250103.xml'
    components = [('159900', '102', '申赎现金', '0', '2', '6000', '6000'),
                  ('000001', '102', '合成实物甲', '100', '1', '0', '0'),
                  ('600000', '101', '合成代理乙', '200', '1', '0', '0'),
                  ('000002', '102', '合成实物或代理丙', '100', '1', '0', '0'),
                  ('600001', '101', '合成固定丁', '0', '2', '20', '18'),
                  ('000003', '102', '合成固定戊', '0', '2', '30', '25')]
    xml = '<PCFFile xmlns="http://ts.szse.cn/Fund"><SecurityID>159338</SecurityID><TradingDay>20250103</TradingDay><PreTradingDay>20250102</PreTradingDay><CreationRedemptionUnit>1000</CreationRedemptionUnit><Components>'
    for code, venue, name, qty, flag, create, redeem in components:
        xml += (f'<Component><UnderlyingSecurityID>{code}</UnderlyingSecurityID>'
                f'<UnderlyingSecurityIDSource>{venue}</UnderlyingSecurityIDSource>'
                f'<UnderlyingSymbol>{name}</UnderlyingSymbol><ComponentShare>{qty}</ComponentShare>'
                f'<SubstituteFlag>{flag}</SubstituteFlag><CreationCashSubstitute>{create}</CreationCashSubstitute>'
                f'<RedemptionCashSubstitute>{redeem}</RedemptionCashSubstitute></Component>')
    pcf_path.write_text(xml+'</Components></PCFFile>', encoding='utf-8')
    prices = []
    for symbol, previous in [('000001.SZ', 10), ('600000.SH', 10), ('000002.SZ', 5)]:
        prices.append(dict(time=datetime(2025, 1, 2, 15), thscode=symbol, close=float(previous)))
    clocks = ['09:35', '09:36', '10:36', '10:37', '14:00', '14:02', '15:00']
    values = {'000001.SZ': [10, 10.1, 10, 10.1, 11, 11, 11],
              '600000.SH': [11, 10.9, 9, 9.1, 10, 10, 10],
              '000002.SZ': [5.5, 5.4, 5, 5.1, 5.5, 5.5, 5.5]}
    for symbol, series in values.items():
        for clock, price in zip(clocks, series):
            prices.append(dict(time=datetime.fromisoformat('2025-01-03 '+clock), thscode=symbol, close=float(price)))
    pq.write_table(pa.Table.from_pylist(prices), market/'2025-01/synthetic.parquet')
    trades = [dict(trade_id='T1', opened_at='2025-01-03 09:35:00', applied_at='2025-01-03 09:36:00',
                   side='CREATE', quantity_units='2', etf_shares='2000', net_pnl='100', state='CLOSED', pcf_path=str(pcf_path)),
              dict(trade_id='T2', opened_at='2025-01-03 10:36:00', applied_at='2025-01-03 10:37:00',
                   side='REDEEM', quantity_units='1', etf_shares='1000', net_pnl='50', state='CLOSED', pcf_path=str(pcf_path))]
    dump_json(run/'config.json', dict(pcf_root=str(pcf), minute_root=str(market), etf_codes=['159338'],
        start_date='2025-01-03', end_date='2025-01-03', starting_cash='100000', mode='execute',
        simulation=dict(premium_threshold='.001'), output_dir=str(run)))
    write_csv(run/'trades.csv', trades)
    write_csv(run/'signals.csv', [dict(ts=trades[0]['opened_at'], iopv='3.81', etf_close='3.82', premium='.0026'),
                                  dict(ts=trades[1]['opened_at'], iopv='3.36', etf_close='3.35', premium='-.0029')])
    write_csv(run/'trade_attribution.csv', [
        dict(trade_id='T1', cash_substitution_pnl='7', estimated_cash_pnl='-4', estimated_cash='20', actual_cash='24', cash_substitution_cash_amount='467'),
        dict(trade_id='T2', cash_substitution_pnl='12', estimated_cash_pnl='2', estimated_cash='10', actual_cash='12', cash_substitution_cash_amount='1862')])
    fills = []
    def fill(tid, symbol, qty, price, purpose, at, side, fee):
        fills.append(dict(trade_id=tid, symbol=symbol, quantity=str(qty), price=str(price), purpose=purpose,
                          ts='2025-01-'+at, side=side, fee=str(fee)))
    fill('T1', '000001.SZ', 200, 10, 'ENTRY', '03 09:35:00', 'BUY', '.4')
    fill('T1', '600000.SH', 400, '10.9', 'MANAGER', '03 09:36:00', 'BUY', 2)
    fill('T1', '000002.SZ', 100, '5.6', 'MANAGER', '07 14:30:00', 'BUY', 1)
    fill('T2', '000001.SZ', 100, '10.1', 'EXIT', '03 10:37:00', 'SELL', '.7')
    fill('T2', '600000.SH', 200, '9.1', 'MANAGER', '03 10:37:00', 'SELL', 1)
    fill('T2', '000002.SZ', 100, '5.1', 'EXIT', '03 10:37:00', 'SELL', '.4')
    write_csv(run/'fills.csv', fills)
    write_csv(run/'orders.csv', [dict(trade_id=r['trade_id'], symbol=r['symbol'], quantity=r['quantity'], purpose=r['purpose']) for r in fills])
    write_csv(run/'fees.csv', [dict(trade_id=r['trade_id'], symbol=r['symbol'], purpose='MANAGER',
                                  charged_to='cash_substitution', total=r['fee']) for r in fills if r['purpose']=='MANAGER'])
    write_csv(run/'substitution_pricing.csv', [dict(trade_id='T1', symbol='000002.SZ', quantity='100',
                price='5.7', ts='2025-01-07 15:00:00', basis='unfilled_quantity_at_deadline_close')])
    write_csv(run/'cash_settlements.csv', [dict(trade_id='T1', kind='cash_substitution', final='467', paid='True'),
                                         dict(trade_id='T2', kind='cash_substitution', final='1862', paid='True')])
    dump_json(run/'run_summary.json', dict(state='completed'))
    (run/'report').mkdir()
    (run/'report/report.html').write_text('<html>synthetic original report</html>', encoding='utf-8')
    return run, pcf, market


def rewrite(path, transform):
    from etf_limit_impact.data import read_csv
    rows = read_csv(path)
    rows = transform(rows)
    write_csv(path, rows)
