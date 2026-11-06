"""Read-only adapters for saved backtests, PCF XML and minute Parquet."""

from bisect import bisect_right
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
import csv
import json
import xml.etree.ElementTree as ET

ZERO = Decimal('0')


def number(value):
    if value is None or value == '':
        return None
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError(f'Non-finite amount: {value}')
    return result


def day(value):
    text = str(value).strip()
    return datetime.strptime(text, '%Y%m%d').date() if len(text) == 8 else date.fromisoformat(text[:10])


def moment(value):
    at = datetime.fromisoformat(str(value))
    if at.tzinfo is not None:
        raise ValueError('Timestamps must be naive Beijing local time')
    return at


def read_csv(path, *, required=False):
    return list(iter_csv(path, required=required))


def iter_csv(path, *, required=False):
    path = Path(path)
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return
    with path.open(encoding='utf-8-sig', newline='') as handle:
        yield from csv.DictReader(handle)


def write_csv(path, rows, columns=None):
    rows = list(rows)
    columns = list(columns or (rows[0] if rows else ['trade_id']))
    with Path(path).open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def dump_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


@dataclass(frozen=True)
class Component:
    symbol: str
    name: str
    quantity: Decimal
    flag: str
    creation_cash: Decimal | None
    redemption_cash: Decimal | None


@dataclass(frozen=True)
class PCF:
    code: str
    trading_day: date
    previous_day: date | None
    creation_unit: Decimal
    components: tuple[Component, ...]


def read_pcf(path):
    root = ET.parse(path).getroot()
    local = lambda node: node.tag.rsplit('}', 1)[-1]
    fields = {local(node): node.text for node in root if not list(node)}
    if not fields.get('SecurityID'):
        raise ValueError(f'Unsupported PCF schema (expected SZ XML): {path}')
    components = []
    seen = set()
    for node in root.iter():
        if local(node) != 'Component':
            continue
        row = {local(child): (child.text or '').strip() for child in node}
        code = row['UnderlyingSecurityID'].zfill(6)
        name = row.get('UnderlyingSymbol', '')
        if code == '159900' or name == '申赎现金':
            continue
        market = row.get('UnderlyingSecurityIDSource')
        suffix = {'101': 'SH', '102': 'SZ', '103': 'BJ'}.get(market)
        if suffix is None:
            raise ValueError(f'Unknown PCF market: {code}/{market}')
        symbol = f'{code}.{suffix}'
        if symbol in seen:
            raise ValueError(f'Duplicate PCF component: {symbol}')
        seen.add(symbol)
        raw_flag = row.get('SubstituteFlag', '').split('-', 1)[0]
        flag = {'禁止': '0', '禁止现金替代': '0', '允许': '1', '允许现金替代': '1',
                '必须': '2', '必须现金替代': '2'}.get(raw_flag, raw_flag)
        if flag not in {'0', '1', '2'}:
            raise ValueError(f'Unsupported substitution flag: {symbol}/{flag}')
        quantity = number(row.get('ComponentShare'))
        if quantity is None or quantity < 0:
            raise ValueError(f'Missing/negative PCF quantity: {symbol}')
        if flag != '2' and quantity != quantity.to_integral_value():
            raise ValueError(f'Non-integral PCF stock quantity: {symbol}')
        if quantity == 0 and flag != '2':
            continue
        components.append(Component(symbol, name, quantity, flag,
            number(row.get('CreationCashSubstitute')), number(row.get('RedemptionCashSubstitute'))))
    return PCF(fields['SecurityID'], day(fields['TradingDay']),
               day(fields['PreTradingDay']) if fields.get('PreTradingDay') else None,
               number(fields['CreationRedemptionUnit']), tuple(components))


class DayQuotes:
    def __init__(self, values=None):
        self.values = values or {}
        self.times = {symbol: sorted(prices) for symbol, prices in self.values.items()}

    def exact(self, symbol, at):
        return self.values.get(symbol, {}).get(at)

    def asof(self, symbol, at, max_age=timedelta(minutes=1)):
        times = self.times.get(symbol, ())
        index = bisect_right(times, at) - 1
        if index < 0 or at-times[index] > max_age:
            return None
        return self.values[symbol][times[index]]


class Market:
    def __init__(self, root):
        self.root = Path(root)
        self.cache = OrderedDict()
        self.read_count = 0

    def load(self, trading_day, symbols):
        symbols = set(symbols)
        cached = self.cache.get(trading_day)
        if cached is not None and symbols <= cached[0]:
            self.cache.move_to_end(trading_day)
            return cached[1]
        import pyarrow.dataset as ds
        requested = symbols | (cached[0] if cached else set())
        values = defaultdict(dict)
        files = sorted((self.root / trading_day.strftime('%Y-%m')).glob('*.parquet'))
        if files and requested:
            start = datetime.combine(trading_day, time.min)
            table = ds.dataset([str(p) for p in files], format='parquet').to_table(
                columns=['time', 'thscode', 'close'],
                filter=(ds.field('time') >= start) & (ds.field('time') < start+timedelta(days=1))
                       & ds.field('thscode').isin(sorted(requested)))
            self.read_count += 1
            for at, symbol, raw in zip(*(table[name].to_pylist() for name in ('time', 'thscode', 'close'))):
                if raw is None:
                    continue
                value = number(raw)
                if value <= 0 or at.tzinfo is not None:
                    raise ValueError(f'Invalid minute quote: {symbol}/{at}/{raw}')
                if at in values[symbol] and values[symbol][at] != value:
                    raise ValueError(f'Conflicting minute quotes: {symbol}/{at}')
                values[symbol][at] = value
        quotes = DayQuotes(dict(values))
        self.cache[trading_day] = requested, quotes
        self.cache.move_to_end(trading_day)
        while len(self.cache) > 3:
            self.cache.popitem(last=False)
        return quotes


class Run:
    """Index large CSVs once; no per-trade full-history scans."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.config = json.loads((self.root/'config.json').read_text(encoding='utf-8-sig'))
        for name in ('pcf_root', 'minute_root'):
            path = Path(self.config[name]).expanduser()
            self.config[name] = str(path.resolve() if path.is_absolute() else (self.root/path).resolve())
        self.trades = read_csv(self.root/'trades.csv', required=True)
        ids = [row['trade_id'] for row in self.trades]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate trade IDs')
        attribution = read_csv(self.root/'trade_attribution.csv', required=True)
        self.attribution = {row['trade_id']: row for row in attribution}
        if len(attribution) != len(self.attribution):
            raise ValueError('Duplicate trade attribution IDs')
        self.signals = {moment(row['ts']): {k: row.get(k) for k in ('etf_close', 'iopv', 'premium')}
                        for row in iter_csv(self.root/'signals.csv', required=True)}
        self.fills = defaultdict(list)
        self.pricing = defaultdict(list)
        self.orders = defaultdict(list)
        self.legs = {}
        self.proxy_fees = defaultdict(lambda: ZERO)
        self.fees_file_present = (self.root/'fees.csv').is_file()
        for row in iter_csv(self.root/'fees.csv'):
            if row.get('purpose') == 'MANAGER' or row.get('charged_to') == 'cash_substitution':
                fee = number(row.get('total'))
                if fee is None or fee < 0:
                    raise ValueError('Missing/negative saved proxy fee')
                self.proxy_fees[row['trade_id'], row['symbol']] += fee
        for filename, target in [('fills.csv', self.fills), ('orders.csv', self.orders),
                                 ('substitution_pricing.csv', self.pricing)]:
            for row in iter_csv(self.root/filename, required=filename != 'substitution_pricing.csv'):
                keep = ('ts', 'side', 'quantity', 'price', 'purpose', 'fee', 'value', 'filled', 'remaining', 'basis')
                target[row['trade_id'], row['symbol']].append({k: row.get(k) for k in keep if k in row})
        for row in read_csv(self.root/'cash_settlements.csv', required=True):
            if (row['trade_id'], row['kind']) in self.legs:
                raise ValueError('Duplicate cash settlement leg')
            self.legs[row['trade_id'], row['kind']] = row
        self.override_pcf_root = None

    def pcf_path(self, trade):
        path = Path(trade['pcf_path'])
        if self.override_pcf_root is None and path.is_file():
            return path
        code = str(self.config.get('etf_codes', ['159338'])[0])
        path = Path(self.config['pcf_root']) / code / f"{moment(trade['opened_at']):%Y%m%d}.xml"
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
