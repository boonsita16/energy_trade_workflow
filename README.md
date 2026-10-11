# energy_trade_workflow

A Python workflow for valuing a European energy trading book and summarising portfolio P&L, exposure, price sensitivity, and power stress scenarios.

## Workflow

```text
                    TRADING BOOK
                         │
                         ▼
                 MARKET PRICE DATA
                         │
                         ▼
                  ┌─────────────┐
                  │    MTM/P&L  │
                  └──────┬──────┘
                         │
             ┌───────────┼────────────┐
             ▼           ▼            ▼
         Exposure      Spread      Commodity
          by hour      exposure     exposure
             │           │            │
             └───────────┼────────────┘
                         ▼
                Price Sensitivity
                         │
                         ▼
                  Stress Testing
                         │
                         ▼
                 PORTFOLIO REPORT
```

The source workflow loads and validates the trading book and market prices, matches prices to trades, calculates mark-to-market P&L, and then produces the exposure, sensitivity, stress, and report outputs shown above. The sample includes power, gas, and carbon positions. Current stress scenarios are limited to power positions measured in MWh.

## Repository structure

```text
.
├── notebook/
│   ├── energy_trade_workflow.ipynb
│   ├── energy_trading_data_example.csv
│   └── energy_market_price_example.csv
└── src/
    └── energy_trade_workflow.py
```

Reusable workflow functions and the command-line entry point live in `src/energy_trade_workflow.py`. The notebook is a thin interactive wrapper around that source code; example input files are kept alongside it in `notebook/`.

## Run

Install pandas if it is not already available:

```bash
python -m pip install pandas
```

Run the complete workflow with the example data:

```bash
python src/energy_trade_workflow.py
```

Optional arguments can be used to provide different input files or change the uniform price shock for price sensitivity:

```bash
python src/energy_trade_workflow.py --trades path/to/trading_book.csv --prices path/to/market_prices.csv --price-shock 10
```

Open `notebook/energy_trade_workflow.ipynb` in Jupyter to run the same workflow interactively.

## Future improvements

- **Stress testing:** extend scenarios beyond day-ahead power to TTF gas, power futures, and EUA carbon positions. Define product-aware price shocks and units, combined cross-commodity scenarios, and scenario narratives; validate results against contract specifications and historical moves.
- **Portfolio reporting:** add dedicated gas, power-futures, and carbon sections with exposure by contract/delivery period, mark-to-market and sensitivity measures, scenario P&L attribution, and consistent currency/unit presentation. Export the report to a shareable format and make valuation timestamps and market-data freshness prominent.
