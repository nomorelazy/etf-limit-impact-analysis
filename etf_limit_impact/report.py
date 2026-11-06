"""Small standalone HTML overview and per-trade detail pages."""

from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
from html import escape
import os
from pathlib import Path
from urllib.parse import quote


STYLE = '''
body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:28px;color:#243247;background:#f7f9fc;line-height:1.6}
h1{margin-bottom:4px}h2{margin-top:34px}.note{color:#58687c}.cards{display:flex;gap:12px;flex-wrap:wrap}
.card{background:white;border:1px solid #dce4ee;border-radius:10px;padding:15px;flex:1;min-width:170px}
.card b{display:block;font-size:23px;color:#154d78}.card small{color:#58687c}
table{border-collapse:collapse;width:100%;table-layout:fixed;background:white;margin:16px 0;font-size:12px}
th,td{border-bottom:1px solid #dce4ee;padding:9px 6px;text-align:right;overflow-wrap:anywhere;vertical-align:top}
th{background:#eaf0f7}th:first-child,td:first-child{text-align:left}a{color:#155c9a}
.positive{color:#067647}.negative{color:#b42318}.notice{background:#fff6df;padding:14px;border-radius:8px}
img{max-width:1100px;width:100%;height:auto}select,input{padding:7px;margin:5px}tr[hidden]{display:none}
@media(max-width:700px){body{margin:12px}th,td{padding:6px 3px;font-size:10px}}
'''


def money(value):
    return '未确定' if value is None else f'{Decimal(str(value)):,.2f}'


def pct(value):
    return '未确定' if value is None else f'{Decimal(str(value)):.4%}'


def translated(value):
    return {'CREATE': '申购', 'REDEEM': '赎回', 'UP': '涨停触价', 'DOWN': '跌停触价',
            'NONE': '未触价', 'UNKNOWN': '无法确认', 'NO_LIMIT': '无涨跌幅限制',
            'cash_proxy': '现金代理', 'fixed_cash': '固定现金替代', 'physical': '实物买卖',
            'estimated': '研究推算', 'provided': '提供的每日限价', 'unknown': '无法确认',
            'matched': '已对账', 'incomplete': '未完整定价／缺基准', 'signal': '信号时刻',
            'day': '全天曾触价（事后）'}.get(value, value)


def detail_name(tid):
    return 'trade_'+sha256(tid.encode()).hexdigest()[:20]+'.html'


def relative_link(target, base):
    return quote(Path(os.path.relpath(target, base)).as_posix(), safe='/.:')


def table(rows, columns, *, trade_links=False, attrs=''):
    document = f'<table {attrs}><thead><tr>'+''.join(f'<th>{escape(label)}</th>' for _, label, _ in columns)
    document += '</tr></thead><tbody>'
    parts = [document]
    for row in rows:
        parts.append(f'<tr data-side="{escape(str(row.get("side", "")), quote=True)}">')
        for name, _, formatter in columns:
            value = row.get(name)
            shown = formatter(value) if formatter else '未确定' if value is None else translated(value)
            cell = escape(str(shown))
            if trade_links and name == 'trade_id':
                cell = f'<a href="trades/{detail_name(value)}">{cell}</a>'
            css = (' class="negative"' if value is not None and isinstance(value, Decimal) and value < 0 else '')
            parts.append(f'<td{css}>{cell}</td>')
        parts.append('</tr>')
    parts.append('</tbody></table>')
    return ''.join(parts)


def page(title, body):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(title)}</title><style>{STYLE}</style><body>{body}</body></html>')


def scatter(output, rows):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for axis, scope, title in zip(axes, ('signal', 'day'), ('At signal time', 'Touched during opening day (post-hoc)')):
        for side, label, color in [('CREATE', 'Creation', '#1769aa'), ('REDEEM', 'Redemption', '#b65b2f')]:
            selected = [r for r in rows if r[f'{scope}_touched'] is True and r['side'] == side
                        and r['route'] != 'physical' and r['weight'] is not None
                        and r['cash_substitution_pnl'] is not None]
            if selected:
                axis.scatter([float(r['weight']*100) for r in selected],
                             [float(r['cash_substitution_pnl']) for r in selected],
                             s=13, alpha=.5, label=label, color=color)
        axis.axhline(0, color='#8894a4', linewidth=.7)
        axis.set_title(title, fontsize=10)
        axis.set_xlabel('Stock weight in ETF basket (%)')
        axis.set_ylabel('Cash-substitution price PnL (CNY)')
        axis.grid(alpha=.15)
        if axis.get_legend_handles_labels()[0]:
            axis.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output/'weight_vs_cash_pnl.svg', bbox_inches='tight')
    plt.close(fig)


TRADE_COLUMNS = [('trade_id', '交易编号', None), ('opened_at', '发起时间', None),
    ('side', '方向', None), ('signal_stock_count', '信号触价股票数', None),
    ('signal_weight', '信号触价股票权重', pct), ('signal_cash_pnl', '信号触价股票替代盈亏（元）', money),
    ('day_stock_count', '全天曾触价股票数（事后）', None), ('day_weight', '全天曾触价权重（事后）', pct),
    ('day_cash_pnl', '全天曾触价股票替代盈亏（元，事后）', money),
    ('cash_substitution_pnl', '全部现金替代盈亏（元）', money),
    ('estimated_cash_pnl', '整篮预估现金E/A影响（元）', money),
    ('final_net_pnl', '原最终净盈亏（元）', money), ('reconciliation', '逐股对账', None)]

STOCK_COLUMNS = [('symbol', '股票代码', None), ('stock_name', '股票名称', None),
    ('pcf_stock_quantity', 'PCF每单位股数', None), ('stock_quantity', '本笔股数', None),
    ('weight', '信号金额权重', pct), ('signal_status', '信号时刻', None),
    ('application_status', '申请时刻', None), ('route', '原回测处理方式', None),
    ('cash_substitution_pnl', '现金替代价格盈亏（元）', money),
    ('proxy_fees', '代理费（已含）', money), ('physical_price_effect', '实物价格影响（元）', money),
    ('fill_times', '原实际成交时间', None), ('limit_evidence', '限价依据', None)]


def render_report(output, run_dir, summaries, limit_rows, buckets, stocks, meta):
    scatter(output, limit_rows)
    by_trade = defaultdict(list)
    for row in limit_rows:
        by_trade[row['trade_id']].append(row)
    detail_dir = output/'trades'
    detail_dir.mkdir()
    for summary in summaries:
        tid = summary['trade_id']
        rows = by_trade[tid]
        body = f'<p><a href="../report.html">返回分析总览</a></p><h1>{escape(tid)} · {translated(summary["side"])}</h1>'
        body += f'<p>发起时间：{escape(str(summary["opened_at"]))}。原现金替代盈亏：{money(summary["cash_substitution_pnl"])} 元。</p>'
        body += '<p class="notice">下表包含申请日内曾触价的成分股；信号后发生的触价仅供事后描述。实物买卖股票的现金替代贡献为零，其价格变化另列。</p>'
        body += table(rows, STOCK_COLUMNS) if rows else '<p>当前有效限价数据中，未观察到申请日内触价股票。请同时查看缺失覆盖。</p>'
        body += (f'<p>无法确认限价的股票：{summary["unknown_limit_stock_count"]} 只；'
                 f'现金替代未完整定价／缺信号基准：{summary["cash_unpriced_stock_count"]} 只。'
                 f'对账：{translated(summary["reconciliation"])}。</p>')
        body += f'<p>现金替代退补／应收本金：{money(summary["cash_substitution_principal"])} 元；整篮 E/A 影响：{money(summary["estimated_cash_pnl"])} 元。两者不分摊成个股价格盈亏。</p>'
        body += '<p><a href="../stock_contributions.csv">全部成分股贡献 CSV（含未触价股票）</a></p>'
        detail_dir.joinpath(detail_name(tid)).write_text(page(tid+' 涨跌停股票归因', body), encoding='utf-8')
    cards = [('本轮套利笔数', str(meta['trade_count'])),
             ('信号触价股票对应替代盈亏（元）', money(meta['signal_limit_cash_pnl'])),
             ('全天曾触价股票对应替代盈亏（元，事后）', money(meta['day_limit_cash_pnl'])),
             ('全部现金替代价格盈亏（元）', money(meta['cash_substitution_pnl']))]
    body = '<h1>涨跌停与 ETF 现金替代盈亏</h1><p class="note">股票股数、信号金额权重与原回测逐股归因</p>'
    body += '<div class="cards">'+''.join(f'<div class="card"><small>{escape(label)}</small><b>{escape(value)}</b></div>' for label, value in cards)+'</div>'
    original = run_dir/'report/report.html'
    if original.is_file():
        body += f'<p><a href="{relative_link(original, output)}">打开原回测报告</a></p>'
    body += (f'<p class="notice">已对账 {meta["matched_trades"]} 笔，未完整定价／缺基准 {meta["incomplete_trades"]} 笔。'
             f'股票日限价依据：提供数据 {meta["provided_limit_stock_days"]} 条，研究推算 {meta["estimated_limit_stock_days"]} 条，'
             f'无法确认 {meta["unknown_limit_stock_days"]} 条。研究推算不能替代除权、新股及临时调整后的正式限价；'
             '分钟收盘触价不能证明封单或无法成交。</p>')
    body += '<h2>权重与现金替代价格盈亏</h2><p>每个点为一笔交易中的一只现金替代股票。金额已经包含代理费；这是一项关联与归因分析，不是涨跌停的因果效应。</p>'
    body += '<img src="weight_vs_cash_pnl.svg" alt="信号时刻与全天事后触价股票的权重及现金替代盈亏散点图">'
    body += '<h2>逐笔申赎</h2><p>权重＝信号股票估值／本笔信号 IOPV 对应的篮子金额。股数随 PCF 和单位倍数计算。全天曾触价为事后信息。点击编号查看股票。</p>'
    body += '<label>方向：<select id="side-filter"><option value="">全部</option><option value="CREATE">申购</option><option value="REDEEM">赎回</option></select></label>'
    body += table(summaries, TRADE_COLUMNS, trade_links=True, attrs='id="trade-table"')
    body += '<h2>按股票金额权重分组</h2>'
    body += table(buckets, [('scope', '观察时点', None), ('side', '方向', None), ('weight_bucket', '权重区间', None),
        ('route', '处理方式', None), ('trade_stock_count', '交易×股票记录数', None),
        ('unique_stock_days', '去重股票日数', None), ('mean_weight', '平均信号权重', pct),
        ('cash_substitution_pnl', '替代盈亏合计（元）', money), ('unknown_cash_pnl_count', '未确定金额数', None)])
    body += '<h2>股票汇总</h2>'
    body += table(stocks, [('symbol', '股票代码', None), ('stock_name', '名称', None), ('scope', '观察时点', None),
        ('side', '方向', None), ('unique_stock_days', '去重股票日数', None), ('unique_trades', '套利笔数', None),
        ('mean_weight', '平均权重', pct), ('cash_substitution_pnl', '现金替代盈亏（元）', money),
        ('physical_price_effect', '实物价格影响（元）', money)])
    body += '<h2>导出与口径</h2><p>'+ ' · '.join(f'<a href="{name}">{label}</a>' for name, label in [
        ('trade_summary.csv', '逐笔摘要'), ('limit_exposures.csv', '触价股票明细'),
        ('stock_contributions.csv', '全部成分股贡献'), ('daily_limit_events.csv', '每日触价区间'),
        ('weight_groups.csv', '权重分组'), ('stock_summary.csv', '股票汇总'), ('summary.json', '核对与数据覆盖')])+'</p>'
    body += '<p>现金替代贡献：申购为信号基准减实际替代成本，赎回为实际净应收减信号基准；代理费只计一次。未成交期限估值沿用原回测，不伪造成交。现金退补本金不算利润。整篮预估现金 E 与实际现金 A 的差额无法凭这些数据精确归因到单只股票，单独保留。</p>'
    body += '''<script>document.getElementById('side-filter').addEventListener('change',function(){
const side=this.value;document.querySelectorAll('#trade-table tbody tr').forEach(function(row){row.hidden=!!side&&row.dataset.side!==side;});});</script>'''
    output.joinpath('report.html').write_text(page('涨跌停与 ETF 现金替代盈亏', body), encoding='utf-8')
