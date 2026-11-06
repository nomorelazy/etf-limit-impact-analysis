"""Analyze completed runs or launch a backtest followed by automatic analysis."""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from etf_limit_impact.analysis import analyze
from etf_limit_impact.data import dump_json


def run_id():
    return datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]


def latest_run(root):
    candidates = []
    for status in Path(root).glob('*/run_summary.json'):
        value = json.loads(status.read_text(encoding='utf-8-sig'))
        if value.get('state') == 'completed' and (status.parent/'trade_attribution.csv').is_file():
            candidates.append(status)
    if not candidates:
        raise ValueError('No completed backtest with per-trade attribution found')
    return max(candidates, key=lambda p: p.stat().st_mtime).parent


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('analyze', 'latest', 'run'):
        p = sub.add_parser(command)
        p.add_argument('--limits-csv', type=Path, help='daily date,symbol,upper_limit,lower_limit,no_limit,source CSV')
        p.add_argument('--official-only', action='store_true', help='disable all derived bounds; use supplied daily bounds only')
        if command == 'run':
            p.add_argument('--backtest-root', type=Path, required=True)
            p.add_argument('--config', type=Path, required=True)
            p.add_argument('--output-root', type=Path, default=Path('reports'))
            p.add_argument('--premium-threshold', type=Decimal)
            p.add_argument('--direction', choices=('both', 'creation', 'redemption'),
                           help='requires original backtest CLI support for --direction')
        else:
            p.add_argument('--run-dir' if command == 'analyze' else '--reports-root', type=Path, required=True)
            p.add_argument('--output', type=Path)
            p.add_argument('--start', type=date.fromisoformat)
            p.add_argument('--end', type=date.fromisoformat)
            p.add_argument('--minute-root', type=Path)
            p.add_argument('--pcf-root', type=Path)
    return parser


def launch(args):
    backtest = args.backtest_root.resolve()
    config_path = args.config.resolve()
    if not (backtest/'etf_arb/cli/__init__.py').is_file():
        raise ValueError('backtest-root must point to the original ETF backtest project')
    raw = json.loads(config_path.read_text(encoding='utf-8-sig'))
    bundle = args.output_root.resolve()/('backtest_with_limits_'+run_id())
    for key in ('pcf_root', 'minute_root', 'rules_path', 'decisions_path', 'cash_payment_dates_path',
                'trading_days', 'settlement_business_days'):
        if isinstance(raw.get(key), str):
            value = Path(raw[key]).expanduser()
            raw[key] = str(value.resolve() if value.is_absolute() else (config_path.parent/value).resolve())
    for name in ('pcf_root', 'minute_root'):
        source = Path(raw[name]).resolve()
        if bundle == source or source in bundle.parents:
            raise ValueError('Output must stay outside raw data')
    if raw.get('mode') != 'execute' or raw.get('simulation') is None:
        raise ValueError('Automatic analysis requires a full-flow execute configuration')
    if args.direction is not None:
        help_result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'etf_arb.cli', 'backtest', '--help'],
                                     cwd=backtest, capture_output=True, text=True, encoding='utf-8')
        if help_result.returncode or '--direction' not in help_result.stdout:
            raise ValueError('Original backtest does not support --direction; use a compatible version or omit it')
    bundle.mkdir(parents=True, exist_ok=False)
    raw['output_dir'] = str(bundle/'backtest')
    snapshot = bundle/'backtest_config.json'
    dump_json(snapshot, raw)
    command = [sys.executable, '-X', 'utf8', '-m', 'etf_arb.cli', 'backtest', '--config', str(snapshot)]
    if args.premium_threshold is not None:
        command += ['--premium-threshold', str(args.premium_threshold)]
    if args.direction is not None:
        command += ['--direction', args.direction]
    print(f'Backtest output: {bundle/"backtest"}', flush=True)
    result = subprocess.run(command, cwd=backtest)
    if result.returncode:
        raise ValueError(f'Original backtest failed (exit {result.returncode}); analysis was not started')
    output = analyze(bundle/'backtest', bundle/'limit_analysis', limits_csv=args.limits_csv,
                     allow_estimated=not args.official_only)
    bundle.joinpath('report.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>回测与涨跌停归因</title>'
        '<h1>回测与涨跌停归因</h1><p><a href="backtest/report/report.html">原套利回测报告</a></p>'
        '<p><a href="limit_analysis/report.html">涨跌停股票、权重与现金替代盈亏</a></p>', encoding='utf-8')
    print(f'Combined report: {bundle/"report.html"}', flush=True)
    return output


def main(argv=None):
    args = argument_parser().parse_args(argv)
    try:
        if args.command == 'run':
            report = launch(args)
        else:
            run = args.run_dir.resolve() if args.command == 'analyze' else latest_run(args.reports_root)
            if args.start is not None and args.end is not None and args.start > args.end:
                raise ValueError('start must not be after end')
            output = args.output or run/('limit_analysis_'+run_id())
            report = analyze(run, output, limits_csv=args.limits_csv,
                allow_estimated=not args.official_only, start=args.start, end=args.end,
                minute_root=args.minute_root, pcf_root=args.pcf_root)
        print(f'Analysis report: {report}', flush=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'Analysis failed: {error}', file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
