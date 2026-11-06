"""Per-stock attribution that reconciles to the original backtest report."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from etf_limit_impact.data import (Market, Run, ZERO, dump_json, moment, number,
                                  read_pcf, write_csv)
from etf_limit_impact.limits import LimitRules, observe_at, observe_day, weight_bucket

TOLERANCE = Decimal('.000001')


def amount_sum(values):
    values = list(values)
    return None if any(value is None for value in values) else sum(values, ZERO)


def contribution(run, trade, component, signal_price):
    tid, side = trade['trade_id'], trade['side']
    units = number(trade['quantity_units'])
    if side not in {'CREATE', 'REDEEM'} or units is None or units <= 0 or units != units.to_integral_value():
        raise ValueError(f'Invalid trade side/units: {tid}')
    quantity = component.quantity*units
    fills = run.fills[tid, component.symbol]
    orders = run.orders[tid, component.symbol]
    valued = run.pricing[tid, component.symbol]
    route = ('fixed_cash' if component.flag == '2' else
             'cash_proxy' if any(o['purpose'] == 'MANAGER' for o in orders) else 'physical')
    baseline = (component.creation_cash*units if component.creation_cash is not None else None
                ) if route == 'fixed_cash' else signal_price*quantity if signal_price is not None else None
    expected_side = 'BUY' if side == 'CREATE' else 'SELL'
    relevant = [r for r in fills if r['purpose'] == 'MANAGER'] if route == 'cash_proxy' else [
        r for r in fills if r['purpose'] != 'MANAGER']
    if any(r['side'] != expected_side for r in relevant):
        raise ValueError(f'Unexpected stock fill side: {tid}/{component.symbol}')
    fee_values = [number(r.get('fee')) for r in relevant]
    fees = amount_sum(fee_values)
    if route == 'cash_proxy' and run.fees_file_present:
        recorded = run.proxy_fees[tid, component.symbol]
        if fees is not None and fees != recorded:
            raise ValueError(f'Proxy fee sources disagree: {tid}/{component.symbol}')
        fees = recorded
    actual_quantity = sum((number(r['quantity']) for r in relevant), ZERO)
    valued_quantity = sum((number(r['quantity']) for r in valued), ZERO)
    gross = sum((number(r['price'])*number(r['quantity']) for r in relevant), ZERO)
    value = sum((number(r['price'])*number(r['quantity']) for r in valued), ZERO)
    if actual_quantity+valued_quantity > quantity:
        raise ValueError(f'Duplicate/oversized stock execution: {tid}/{component.symbol}')
    priced = actual_quantity+valued_quantity == quantity
    actual = None
    cash_pnl = None
    physical_effect = None
    if route == 'fixed_cash':
        fixed = component.creation_cash if side == 'CREATE' else component.redemption_cash
        actual = fixed*units if fixed is not None else None
        priced = actual is not None
        fees = ZERO
    elif route == 'cash_proxy' and priced and fees is not None:
        actual = gross+value+(fees if side == 'CREATE' else -fees)
    elif route == 'physical':
        cash_pnl = ZERO
        if priced and baseline is not None:
            physical_effect = baseline-gross if side == 'CREATE' else gross-baseline
    if route != 'physical' and baseline is not None and actual is not None:
        cash_pnl = baseline-actual if side == 'CREATE' else actual-baseline
    return dict(route=route, stock_quantity=quantity, signal_cash_reference=baseline,
        actual_cash_cost_or_receipt=actual, cash_substitution_pnl=cash_pnl,
        proxy_fees=fees if route == 'cash_proxy' else ZERO,
        physical_price_effect=physical_effect, filled_stock_quantity=actual_quantity,
        valued_stock_quantity=valued_quantity, cash_priced=priced if route != 'physical' else None,
        fill_times='; '.join(sorted(set(r['ts'] for r in relevant))),
        valuation_times='; '.join(sorted(set(r['ts'] for r in valued))))


def summarize_trade(trade, attribution, rows):
    original = number(attribution.get('cash_substitution_pnl'))
    cash_rows = [row for row in rows if row['route'] != 'physical']
    computed = amount_sum(row['cash_substitution_pnl'] for row in cash_rows)
    residual = original-computed if original is not None and computed is not None else None
    if residual is not None and abs(residual) > TOLERANCE:
        raise ValueError(f"Stock cash-substitution PnL does not reconcile: {trade['trade_id']}: {residual}")
    result = dict(trade_id=trade['trade_id'], opened_at=trade['opened_at'],
        applied_at=trade.get('applied_at'), side=trade['side'],
        final_net_pnl=number(trade.get('net_pnl')) if trade.get('state') == 'CLOSED' else None,
        cash_substitution_pnl=original, computed_cash_substitution_pnl=computed,
        reconciliation_residual=residual,
        reconciliation='matched' if residual is not None else 'incomplete',
        cash_substitution_principal=number(attribution.get('cash_substitution_cash_amount')),
        estimated_cash_pnl=number(attribution.get('estimated_cash_pnl')),
        estimated_cash=number(attribution.get('estimated_cash')), actual_cash=number(attribution.get('actual_cash')),
        cash_unpriced_stock_count=sum(row['cash_substitution_pnl'] is None for row in cash_rows),
        unknown_limit_stock_count=sum(row['limit_evidence'] == 'unknown' and row['stock_quantity'] > 0 for row in rows))
    for scope in ('signal', 'before_signal', 'day'):
        selected = [row for row in rows if row[f'{scope}_touched'] is True and row['stock_quantity'] > 0]
        result[f'{scope}_stock_count'] = len(selected)
        result[f'{scope}_stock_quantity'] = sum((row['stock_quantity'] for row in selected), ZERO)
        result[f'{scope}_weight'] = amount_sum(row['weight'] for row in selected)
        result[f'{scope}_cash_pnl'] = amount_sum(row['cash_substitution_pnl'] for row in selected)
        result[f'{scope}_physical_effect'] = amount_sum(row['physical_price_effect'] for row in selected
                                                      if row['route'] == 'physical')
    return result


def group_summary(rows):
    groups = defaultdict(list)
    stocks = defaultdict(list)
    for row in rows:
        for scope in ('signal', 'day'):
            if row[f'{scope}_touched'] is True and row['stock_quantity'] > 0:
                groups[scope, row['side'], row['weight_bucket'], row['route']].append(row)
                stocks[scope, row['symbol'], row['side']].append(row)

    def aggregate(selected):
        weights = [row['weight'] for row in selected if row['weight'] is not None]
        pnl = [row['cash_substitution_pnl'] for row in selected]
        return dict(trade_stock_count=len(selected),
            unique_stock_days=len({(row['date'], row['symbol']) for row in selected}),
            unique_trades=len({row['trade_id'] for row in selected}),
            mean_weight=sum(weights, ZERO)/len(weights) if weights else None,
            cash_substitution_pnl=amount_sum(pnl),
            known_cash_pnl=sum((value for value in pnl if value is not None), ZERO),
            unknown_cash_pnl_count=sum(value is None for value in pnl),
            physical_price_effect=amount_sum(row['physical_price_effect'] for row in selected
                                            if row['route'] == 'physical'))
    buckets = [dict(scope=key[0], side=key[1], weight_bucket=key[2], route=key[3], **aggregate(selected))
               for key, selected in sorted(groups.items())]
    per_stock = [dict(scope=key[0], symbol=key[1], side=key[2], stock_name=selected[0]['stock_name'],
                      **aggregate(selected)) for key, selected in sorted(stocks.items())]
    return buckets, per_stock


def analyze(run_dir, output_dir, *, limits_csv=None, allow_estimated=True, start=None, end=None,
            minute_root=None, pcf_root=None, progress=None):
    progress = progress or (lambda message: print(message, flush=True))
    run_dir, output_dir = Path(run_dir).resolve(), Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('Analysis output is not empty; choose a fresh output directory')
    run = Run(run_dir)
    pcf_override = pcf_root
    minute_root = Path(minute_root or run.config['minute_root']).resolve()
    pcf_root = Path(pcf_root or run.config['pcf_root']).resolve()
    for source in (minute_root, pcf_root):
        if output_dir == source or source in output_dir.parents:
            raise ValueError('Analysis output must stay outside raw data directories')
        if not source.is_dir():
            raise FileNotFoundError(source)
    run.config['pcf_root'] = str(pcf_root)
    if pcf_override is not None:
        run.override_pcf_root = pcf_root
    selected = [trade for trade in run.trades if
                (start is None or moment(trade['opened_at']).date() >= start) and
                (end is None or moment(trade['opened_at']).date() <= end)]
    selected.sort(key=lambda trade: moment(trade['opened_at']))
    output_dir.mkdir(parents=True, exist_ok=True)
    market = Market(minute_root)
    rules = LimitRules(limits_csv, allow_estimated=allow_estimated)
    source_files = {p.name: dict(size=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns)
                    for p in run.root.glob('*') if p.is_file()}
    dump_json(output_dir/'analysis_status.json', dict(state='running', source_run=str(run_dir)))
    try:
        pcfs, events, observations = {}, {}, {}
        stock_rows, summaries = [], []
        days = sorted({moment(trade['opened_at']).date() for trade in selected})
        day_rank = {value: index+1 for index, value in enumerate(days)}
        last_day = None
        for trade in selected:
            at = moment(trade['opened_at'])
            path = run.pcf_path(trade)
            if path not in pcfs:
                pcfs[path] = read_pcf(path)
            pcf = pcfs[path]
            if pcf.trading_day != at.date():
                raise ValueError(f"PCF date mismatch: {trade['trade_id']}")
            symbols = [component.symbol for component in pcf.components if component.quantity > 0]
            quotes = market.load(pcf.trading_day, symbols)
            previous = market.load(pcf.previous_day, symbols) if pcf.previous_day else None
            if pcf.trading_day != last_day:
                last_day = pcf.trading_day
                progress(f'Analysis day {day_rank[last_day]}/{len(days)}: {last_day}')
                dump_json(output_dir/'progress.json', dict(processed_days=day_rank[last_day]-1,
                    total_days=len(days), current_day=str(last_day), completed_trades=len(summaries)))
            signal = run.signals.get(at, {})
            iopv = number(signal.get('iopv'))
            etf_shares = number(trade['etf_shares'])
            denominator = iopv*etf_shares if iopv is not None and iopv > 0 else None
            if etf_shares != pcf.creation_unit*number(trade['quantity_units']):
                raise ValueError(f"PCF units/ETF shares mismatch: {trade['trade_id']}")
            rows = []
            applied = moment(trade['applied_at']) if trade.get('applied_at') else None
            for component in pcf.components:
                key = pcf.code, pcf.trading_day, component.symbol
                if key not in observations:
                    base_bounds = rules.resolve(component, pcf, previous)
                    observation = observe_day(component, pcf, quotes, base_bounds)
                    observations[key] = (*observation, base_bounds)
                    events[key] = observation[0]
                event, bounds, hits, base_bounds = observations[key]
                signal_price = quotes.asof(component.symbol, at)
                details = contribution(run, trade, component, signal_price)
                quantity = details['stock_quantity']
                value = quantity*signal_price if signal_price is not None else ZERO if quantity == 0 else None
                weight = value/denominator if value is not None and denominator is not None else None
                signal_status, before, signal_bounds = observe_at(component.symbol, quotes, at, base_bounds)
                application_status = observe_at(component.symbol, quotes, applied, base_bounds)[0] if applied else 'UNKNOWN'
                row = dict(trade_id=trade['trade_id'], opened_at=at, applied_at=applied,
                    date=pcf.trading_day, etf_code=pcf.code, side=trade['side'], symbol=component.symbol,
                    stock_name=component.name, pcf_stock_quantity=component.quantity,
                    quantity_units=number(trade['quantity_units']), signal_stock_price=signal_price,
                    signal_stock_value=value, signal_basket_value=denominator, weight=weight,
                    weight_bucket=weight_bucket(weight), signal_status=signal_status,
                    application_status=application_status,
                    signal_touched=signal_status in {'UP', 'DOWN'} if signal_status != 'UNKNOWN' else None,
                    before_signal_touched=before,
                    day_touched=event['day_touched'], day_up_minutes=event['up_minutes'],
                    day_down_minutes=event['down_minutes'], day_up_spans=event['up_spans'],
                    day_down_spans=event['down_spans'], limit_evidence=signal_bounds.evidence,
                    limit_note=signal_bounds.note, day_limit_evidence=event['evidence'],
                    day_limit_note=event['limit_note'], **details)
                rows.append(row)
            attribution = run.attribution.get(trade['trade_id'])
            if attribution is None:
                raise ValueError(f"Missing saved attribution: {trade['trade_id']}; rebuild original report first")
            summaries.append(summarize_trade(trade, attribution, rows))
            stock_rows.extend(rows)
        buckets, stocks = group_summary(stock_rows)
        limit_rows = [row for row in stock_rows if row['stock_quantity'] > 0 and
                      any(row[key] is True for key in ('signal_touched', 'before_signal_touched', 'day_touched'))]
        write_csv(output_dir/'stock_contributions.csv', stock_rows)
        write_csv(output_dir/'limit_exposures.csv', limit_rows)
        write_csv(output_dir/'trade_summary.csv', summaries)
        write_csv(output_dir/'daily_limit_events.csv', list(events.values()))
        write_csv(output_dir/'weight_groups.csv', buckets)
        write_csv(output_dir/'stock_summary.csv', stocks)
        meta = dict(source_run=str(run_dir), minute_root=str(minute_root), pcf_root=str(pcf_root),
            trade_count=len(summaries), stock_trade_count=len(stock_rows), limit_stock_trade_count=len(limit_rows),
            unique_limit_stock_days=len({(row['date'], row['symbol']) for row in limit_rows}),
            matched_trades=sum(row['reconciliation'] == 'matched' for row in summaries),
            incomplete_trades=sum(row['reconciliation'] != 'matched' for row in summaries),
            cash_substitution_pnl=amount_sum(row['cash_substitution_pnl'] for row in summaries),
            signal_limit_cash_pnl=amount_sum(row['signal_cash_pnl'] for row in summaries),
            day_limit_cash_pnl=amount_sum(row['day_cash_pnl'] for row in summaries),
            estimated_cash_pnl=amount_sum(row['estimated_cash_pnl'] for row in summaries),
            provided_limit_stock_days=sum(e['evidence'] == 'provided' for e in events.values()),
            estimated_limit_stock_days=sum(e['evidence'] == 'estimated' for e in events.values()),
            unknown_limit_stock_days=sum(e['evidence'] == 'unknown' for e in events.values()),
            parquet_day_reads=market.read_count, source_files=source_files,
            policy='Close touches are observations, not order-book locks or causal estimates; basket E/A is not allocated to stocks.')
        from etf_limit_impact.report import render_report
        render_report(output_dir, run_dir, summaries, limit_rows, buckets, stocks, meta)
        dump_json(output_dir/'summary.json', meta)
        current = {p.name: dict(size=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns)
                   for p in run.root.glob('*') if p.is_file()}
        if current != source_files:
            raise ValueError('Original backtest outputs changed during analysis; use a completed stable run')
        dump_json(output_dir/'progress.json', dict(processed_days=len(days), total_days=len(days),
                  completed_trades=len(summaries)))
        dump_json(output_dir/'analysis_status.json', dict(state='completed', trade_count=len(summaries)))
        return output_dir/'report.html'
    except Exception as error:
        dump_json(output_dir/'analysis_status.json', dict(state='failed', error=str(error)))
        raise
