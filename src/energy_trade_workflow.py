"""Load, value, and report on an energy trading portfolio."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

MARKET_MAPPING = {
    "SE3": ("Power", "SE3"),
    "DK1": ("Power", "DK1"),
    "DE-LU": ("Power", "DE-LU"),
    "FI": ("Power", "FI"),
    "TTF": ("Gas", "TTF"),
    "EUA": ("Carbon", "EUA"),
}

JOIN_COLUMNS = ["market", "product", "delivery_start", "price_unit"]
TRADE_COLUMNS = [
    "position_id",
    "market",
    "product",
    "delivery_start",
    "delivery_end",
    "volume",
    "unit",
    "entry_price",
    "price_unit",
]
PRICE_COLUMNS = JOIN_COLUMNS + ["market_price", "observed_at"]
VALUATION_COLUMNS = [
    "position_id",
    "commodity",
    "location",
    "product",
    "delivery_start",
    "delivery_end",
    "volume",
    "unit",
    "entry_price",
    "market_price",
    "price_unit",
    "Unrealized_P&L_€",
]

POWER_STRESS_SCENARIOS = {
    "Nordic adverse": {"SE3": -20, "DK1": 20, "FI": -20, "DE-LU": 0},
    "European price spike": {"SE3": 50, "DK1": 50, "FI": 50, "DE-LU": 50},
    "European price crash": {"SE3": -50, "DK1": -50, "FI": -50, "DE-LU": -50},
    "SE3-DK1 convergence": {"SE3": -20, "DK1": -20, "FI": 0, "DE-LU": 0},
}


def to_numeric(data: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Convert formatted numeric columns and reject invalid values."""
    result = data.copy()
    for column in columns:
        cleaned = (
            result[column]
            .astype("string")
            .str.replace(",", "", regex=False)
            .str.replace("€", "", regex=False)
            .str.strip()
        )
        converted = pd.to_numeric(cleaned, errors="coerce")
        invalid = converted.isna()
        if invalid.any():
            examples = cleaned[invalid].fillna("<missing>").unique().tolist()
            raise ValueError(f"Invalid numeric values in {column}: {examples}")
        result[column] = converted
    return result


def load_trading_book(path: Path) -> pd.DataFrame:
    """Load the trading book and validate its required columns."""
    trades = pd.read_csv(path)
    missing = sorted(set(TRADE_COLUMNS) - set(trades.columns))
    if missing:
        raise ValueError(f"Trading book is missing required columns: {missing}")
    if trades[JOIN_COLUMNS + ["position_id"]].isna().any().any():
        raise ValueError("Trading book contains missing market keys or position IDs")
    return to_numeric(trades, ["volume", "entry_price"])


def load_market_prices(path: Path) -> pd.DataFrame:
    """Load market prices and validate their required columns."""
    prices = pd.read_csv(path)
    missing = sorted(set(PRICE_COLUMNS) - set(prices.columns))
    if missing:
        raise ValueError(f"Market prices are missing required columns: {missing}")
    if prices[JOIN_COLUMNS].isna().any().any():
        raise ValueError("Market prices contain missing trade matching keys")
    prices = to_numeric(prices, ["market_price"])
    prices["observed_at"] = pd.to_datetime(
        prices["observed_at"], errors="coerce", utc=True
    )
    if prices["observed_at"].isna().any():
        raise ValueError("Market prices contain invalid observed_at timestamps")
    duplicates = prices.duplicated(JOIN_COLUMNS, keep=False)
    if duplicates.any():
        keys = prices.loc[duplicates, JOIN_COLUMNS].drop_duplicates()
        raise ValueError(
            "Market prices contain duplicate trade matching keys: "
            f"{keys.to_dict(orient='records')}"
        )
    return prices


def trades_market(
    trades: pd.DataFrame, market_prices: pd.DataFrame
) -> pd.DataFrame:
    """Match each trade to one market price using its market delivery key."""
    merged = trades.merge(
        market_prices,
        on=JOIN_COLUMNS,
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    unmatched = merged["_merge"].eq("left_only")
    if unmatched.any():
        missing = merged.loc[unmatched, JOIN_COLUMNS].drop_duplicates()
        raise ValueError(
            "No market price found for trade keys: "
            f"{missing.to_dict(orient='records')}"
        )
    return merged.drop(columns="_merge")


def split_market(trades: pd.DataFrame) -> pd.DataFrame:
    """Add commodity and location labels for supported markets."""
    result = trades.copy()
    if result["market"].isna().any():
        raise ValueError("Trading book contains a missing market")
    unknown = sorted(set(result["market"].dropna()) - set(MARKET_MAPPING))
    if unknown:
        raise ValueError(f"Unsupported markets in trading book: {unknown}")
    result["commodity"] = result["market"].map(
        lambda market: MARKET_MAPPING[market][0]
    )
    result["location"] = result["market"].map(
        lambda market: MARKET_MAPPING[market][1]
    )
    return result


def valuation(trades: pd.DataFrame) -> pd.DataFrame:
    """Calculate unrealized P&L for every matched trade."""
    result = trades.copy()
    result["Unrealized_P&L_€"] = (
        result["market_price"] - result["entry_price"]
    ) * result["volume"]
    return result[VALUATION_COLUMNS]


def exposure_power(valued_trades: pd.DataFrame) -> pd.DataFrame:
    power = valued_trades.query("commodity == 'Power' and unit == 'MWh'")
    return (
        power.groupby("location")["volume"]
        .agg(
            net_position="sum",
            gross_position=lambda volume: volume.abs().sum(),
        )
        .reset_index()
        .sort_values("gross_position", ascending=False)
    )


def exposure_hourly(valued_trades: pd.DataFrame) -> pd.DataFrame:
    power = valued_trades.query("commodity == 'Power' and unit == 'MWh'").copy()
    power["delivery_start"] = pd.to_datetime(power["delivery_start"])
    return (
        power.groupby("delivery_start")
        .agg(
            net_position=("volume", "sum"),
            gross_position=("volume", lambda volume: volume.abs().sum()),
            number_of_positions=("position_id", "count"),
        )
        .reset_index()
        .sort_values("delivery_start")
    )


def identify_spread_exposure(valued_trades: pd.DataFrame) -> pd.DataFrame:
    """Find potential opposing power positions in different zones per hour."""
    power = valued_trades.query("commodity == 'Power' and unit == 'MWh'").copy()
    power["delivery_start"] = pd.to_datetime(power["delivery_start"])
    spreads = []
    for delivery_time, group in power.groupby("delivery_start"):
        longs = group[group["volume"] > 0]
        shorts = group[group["volume"] < 0]
        for _, long_position in longs.iterrows():
            for _, short_position in shorts.iterrows():
                if long_position["location"] == short_position["location"]:
                    continue
                spreads.append(
                    {
                        "delivery_start": delivery_time,
                        "long_location": long_position["location"],
                        "short_location": short_position["location"],
                        "spread_volume_mwh": min(
                            abs(long_position["volume"]),
                            abs(short_position["volume"]),
                        ),
                        "long_position_id": long_position["position_id"],
                        "short_position_id": short_position["position_id"],
                    }
                )
    return pd.DataFrame(spreads)


def analyse_commodity_exposure(valued_trades: pd.DataFrame) -> pd.DataFrame:
    return (
        valued_trades.groupby(["commodity", "unit"])
        .agg(
            net_position=("volume", "sum"),
            gross_position=("volume", lambda volume: volume.abs().sum()),
            number_of_positions=("position_id", "count"),
        )
        .reset_index()
    )


def calculate_price_sensitivity(
    valued_trades: pd.DataFrame, price_shock: float = 10
) -> pd.DataFrame:
    """Estimate P&L for a uniform absolute price shock in each market."""
    result = valued_trades.copy()
    result["price_shock"] = price_shock
    result["P&L_sensitivity_€"] = result["volume"] * result["price_shock"]
    return (
        result.groupby(["commodity", "location", "unit"])
        .agg(
            net_position=("volume", "sum"),
            price_shock=("price_shock", "first"),
            pnl_sensitivity=("P&L_sensitivity_€", "sum"),
        )
        .reset_index()
    )


def stress_test(
    valued_trades: pd.DataFrame, scenario: dict[str, float]
) -> tuple[pd.DataFrame, float]:
    """Calculate power-only stress P&L from price shocks by bidding zone."""
    power = valued_trades.query("commodity == 'Power' and unit == 'MWh'").copy()
    power["stress_shock"] = power["location"].map(scenario).fillna(0)
    power["stress_pnl"] = power["volume"] * power["stress_shock"]
    detail = (
        power.groupby("location")
        .agg(
            position=("volume", "sum"),
            shock=("stress_shock", "first"),
            stress_pnl=("stress_pnl", "sum"),
        )
        .reset_index()
    )
    return detail, detail["stress_pnl"].sum()


def run_scenarios(
    valued_trades: pd.DataFrame, scenarios: dict[str, dict[str, float]]
) -> pd.DataFrame:
    results = []
    for name, scenario in scenarios.items():
        _, stress_pnl = stress_test(valued_trades, scenario)
        results.append({"scenario": name, "stress_pnl": stress_pnl})
    return pd.DataFrame(results)


def portfolio_report(valued_trades: pd.DataFrame) -> dict[str, object]:
    """Print the portfolio summary and return its aggregate exposure tables."""
    total_pnl = valued_trades["Unrealized_P&L_€"].sum()
    power = valued_trades.query("commodity == 'Power' and unit == 'MWh'")
    net_power = power["volume"].sum()
    gross_power = power["volume"].abs().sum()
    zone_exposure = (
        power.groupby("location")["volume"]
        .agg(net="sum", gross=lambda volume: volume.abs().sum())
    )
    commodity_exposure = (
        valued_trades.groupby(["commodity", "unit"])["volume"]
        .agg(net="sum", gross=lambda volume: volume.abs().sum())
    )
    hourly_exposure = (
        power.groupby("delivery_start")["volume"]
        .agg(net="sum", gross=lambda volume: volume.abs().sum())
    )

    print("=" * 60)
    print("EUROPEAN ENERGY PORTFOLIO RISK REPORT")
    print("=" * 60)
    print(f"\nUnrealised P&L: €{total_pnl:,.2f}")
    print(f"\nPOWER EXPOSURE\n{net_power:+,.0f} MWh net / {gross_power:,.0f} MWh gross")
    print(f"\nPOWER EXPOSURE BY ZONE\n{zone_exposure}")
    print(f"\nCOMMODITY EXPOSURE\n{commodity_exposure}")
    print(f"\nEXPOSURE BY DELIVERY HOUR\n{hourly_exposure}")
    valuation_timestamp = pd.Timestamp.now(tz="Europe/Stockholm")
    print(
        "\nValuation timestamp:",
        valuation_timestamp.strftime("%Y-%m-%d %H:%M:%S %Z"),
    )
    return {
        "total_pnl": total_pnl,
        "zone_exposure": zone_exposure,
        "commodity_exposure": commodity_exposure,
        "hourly_exposure": hourly_exposure,
    }


def get_price_timestamps(market_prices: pd.DataFrame) -> pd.DataFrame:
    timestamps = (
        market_prices.groupby(["product", "market"], dropna=False)
        .agg(
            latest_price_timestamp=("observed_at", "max"),
            price_records=("observed_at", "count"),
        )
        .reset_index()
        .sort_values(["product", "market"])
    )
    timestamps["latest_price_timestamp"] = (
        timestamps["latest_price_timestamp"]
        .dt.tz_convert("Europe/Stockholm")
        .dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    )
    print("\nLATEST MARKET PRICE TIMESTAMPS")
    print(timestamps.to_string(index=False))
    return timestamps


def run_workflow(
    trade_file: Path,
    market_file: Path,
    price_shock: float = 10,
) -> dict[str, object]:
    """Run the complete data-to-report workflow and return its results."""
    trades = load_trading_book(trade_file)
    market_prices = load_market_prices(market_file)
    valued_trades = valuation(split_market(trades_market(trades, market_prices)))

    print("\nMARK-TO-MARKET VALUATION")
    print(valued_trades.to_string(index=False))
    print("\nPOWER EXPOSURE BY ZONE")
    print(exposure_power(valued_trades).to_string(index=False))
    print("\nPOWER EXPOSURE BY DELIVERY HOUR")
    print(exposure_hourly(valued_trades).to_string(index=False))
    print("\nPOTENTIAL CROSS-ZONAL SPREAD EXPOSURE")
    print(identify_spread_exposure(valued_trades).to_string(index=False))
    print("\nCOMMODITY EXPOSURE")
    print(analyse_commodity_exposure(valued_trades).to_string(index=False))
    print(f"\nPRICE SENSITIVITY (+{price_shock:g} per unit)")
    print(
        calculate_price_sensitivity(valued_trades, price_shock).to_string(
            index=False
        )
    )
    print("\nPOWER STRESS SCENARIOS")
    print(run_scenarios(valued_trades, POWER_STRESS_SCENARIOS).to_string(index=False))
    report = portfolio_report(valued_trades)
    get_price_timestamps(market_prices)
    return {
        "valuation": valued_trades,
        "report": report,
        "power_stress_scenarios": POWER_STRESS_SCENARIOS,
    }


def main() -> None:
    default_data_dir = Path(__file__).resolve().parent.parent / "notebook"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trades",
        type=Path,
        default=default_data_dir / "energy_trading_data_example.csv",
        help="Path to the trading book CSV",
    )
    parser.add_argument(
        "--prices",
        type=Path,
        default=default_data_dir / "energy_market_price_example.csv",
        help="Path to the market price CSV",
    )
    parser.add_argument(
        "--price-shock",
        type=float,
        default=10,
        help="Absolute uniform price shock used for the sensitivity report",
    )
    arguments = parser.parse_args()
    run_workflow(arguments.trades, arguments.prices, arguments.price_shock)


if __name__ == "__main__":
    main()
