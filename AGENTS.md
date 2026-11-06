# Development requirements

Read docs/requirements.md before changing attribution or limit classification.
This is a post-trade analysis companion; never change backtest signals, prices,
fills, payment dates, fees, six-column Portfolio APIs or original output files.
Use Decimal for money and reconcile every trade to saved substitution PnL.
Keep cash-substitution price PnL, principal receipts/refunds, and basket E/A cash
differences separate. Never allocate basket E/A to stocks without supporting data.
Minute close touching a price limit does not establish a locked order book or
inability to trade. Derived bounds are research estimates, never official facts.
Signal-time labels must not include later observations. Show missing coverage.
Raw PCF/market data and real backtest results stay local and are gitignored.
Test the CLI and full report path, including missing data and reconciliation.
