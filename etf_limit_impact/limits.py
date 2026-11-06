"""Price-limit evidence and close-touch observations; no order-book inference."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from etf_limit_impact.data import day, number, read_csv

TICK = Decimal('.01')


def price_tick(value):
    return value.quantize(TICK, rounding=ROUND_HALF_UP)


def bounds_from_reference(reference, rate):
    up, down = price_tick(reference*(1+rate)), price_tick(reference*(1-rate))
    if up-reference < TICK:
        up = reference+TICK
    if reference-down < TICK:
        down = reference-TICK
    return up, max(TICK, down)


@dataclass(frozen=True)
class Bounds:
    upper: Decimal | None = None
    lower: Decimal | None = None
    evidence: str = 'unknown'
    source: str = ''
    note: str = ''
    no_limit: bool = False


class LimitRules:
    def __init__(self, path=None, *, allow_estimated=True):
        self.allow_estimated = allow_estimated
        self.provided = {}
        for row in read_csv(path, required=True) if path else []:
            key = day(row.get('date') or row.get('trading_day')), row.get('symbol') or row.get('thscode')
            if not key[1] or '.' not in key[1]:
                raise ValueError('Daily limit symbol must include .SZ/.SH/.BJ')
            no_limit = str(row.get('no_limit', '')).lower() in {'true', '1', 'yes'}
            upper, lower = number(row.get('upper_limit')), number(row.get('lower_limit'))
            if not no_limit and (upper is None or lower is None or lower <= 0 or upper < lower):
                raise ValueError(f'Invalid daily limits: {key}')
            value = Bounds(upper, lower, 'provided', row.get('source') or str(path),
                           row.get('note', ''), no_limit)
            if key in self.provided and self.provided[key] != value:
                raise ValueError(f'Conflicting daily limits: {key}')
            self.provided[key] = value

    def resolve(self, component, pcf, previous):
        key = pcf.trading_day, component.symbol
        if key in self.provided:
            return self.provided[key]
        if not self.allow_estimated:
            return Bounds(note='no_provided_daily_limit')
        if pcf.previous_day is None or previous is None:
            return Bounds(note='missing_previous_trading_day')
        reference = previous.exact(component.symbol, datetime.combine(pcf.previous_day, time(15)))
        if reference is None:
            return Bounds(note='missing_exact_previous_1500_close')
        code = component.symbol.split('.')[0]
        if code.startswith(('688', '689')):
            rate = Decimal('.20')
        elif code.startswith(('300', '301')) and pcf.trading_day.isoformat() >= '2020-08-24':
            rate = Decimal('.20')
        elif component.symbol.endswith('.BJ') and code.startswith(('4', '8', '920')):
            rate = Decimal('.30')
        elif code.startswith(('000', '001', '002', '003', '600', '601', '603', '605')):
            rate = Decimal('.05') if 'ST' in component.name.upper() else Decimal('.10')
        else:
            return Bounds(note='unsupported_historical_board')
        up, down = bounds_from_reference(reference, rate)
        return Bounds(up, down, 'estimated', 'previous_exact_close_and_pcf_name_board',
                      'Research estimate; ex-rights/IPO/temporary rules require daily limits')


def session(at):
    return time(9, 30) <= at.time() <= time(11, 30) or time(13) <= at.time() <= time(15)


def touch(price, bounds):
    if price is None or bounds.evidence == 'unknown':
        return 'UNKNOWN'
    if bounds.no_limit:
        return 'NO_LIMIT'
    if abs(price-bounds.upper) <= Decimal('.000001'):
        return 'UP'
    if abs(price-bounds.lower) <= Decimal('.000001'):
        return 'DOWN'
    return 'NONE'


def validate_bounds(bounds, values):
    """Validate using only the observations supplied by the caller."""
    if not bounds.no_limit and bounds.evidence != 'unknown' and any(
        price > bounds.upper+Decimal('.000001') or price < bounds.lower-Decimal('.000001')
        for price in values):
        return Bounds(evidence='unknown', source=bounds.source,
                      note='observed_price_outside_bounds; check corporate actions/IPO/data')
    return bounds


def observe_at(symbol, quotes, at, bounds):
    """Future bars cannot change a signal-time classification."""
    prefix = {ts: price for ts, price in quotes.values.get(symbol, {}).items()
              if ts <= at and session(ts)}
    valid = validate_bounds(bounds, prefix.values())
    status = touch(quotes.exact(symbol, at), valid)
    before = any(touch(price, valid) in {'UP', 'DOWN'} for price in prefix.values())
    return status, before if valid.evidence != 'unknown' else None, valid


def intervals(times):
    result = []
    for at in sorted(times):
        if result and at-result[-1][1] == timedelta(minutes=1):
            result[-1] = result[-1][0], at
        else:
            result.append((at, at))
    return result


def observe_day(component, pcf, quotes, bounds):
    values = {at: price for at, price in quotes.values.get(component.symbol, {}).items() if session(at)}
    valid = validate_bounds(bounds, values.values())
    hits = {direction: [at for at, price in values.items() if touch(price, valid) == direction]
            for direction in ('UP', 'DOWN')}
    if not values:
        valid = Bounds(evidence='unknown', source=bounds.source, note='missing_day_quotes')
    event = dict(date=pcf.trading_day, etf_code=pcf.code, symbol=component.symbol,
        stock_name=component.name, evidence=valid.evidence, limit_source=valid.source,
        limit_note=valid.note, upper_limit=valid.upper, lower_limit=valid.lower,
        quote_minutes=len(values), up_minutes=len(hits['UP']), down_minutes=len(hits['DOWN']),
        up_spans='; '.join(f'{a:%H:%M}~{b:%H:%M}' for a, b in intervals(hits['UP'])),
        down_spans='; '.join(f'{a:%H:%M}~{b:%H:%M}' for a, b in intervals(hits['DOWN'])),
        day_touched=(bool(hits['UP'] or hits['DOWN']) if valid.evidence != 'unknown' else None),
        no_limit=valid.no_limit)
    return event, valid, hits


def weight_bucket(weight):
    if weight is None:
        return '未知权重'
    for upper, label in [(Decimal('.001'), '<0.1%'), (Decimal('.005'), '0.1%–0.5%'),
                         (Decimal('.01'), '0.5%–1%'), (Decimal('.03'), '1%–3%')]:
        if weight < upper:
            return label
    return '>=3%'
