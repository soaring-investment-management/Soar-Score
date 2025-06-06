import json
import requests
from statistics import mean, stdev
from scipy.stats import linregress
from shiny import App, ui, render, reactive
import pandas as pd

# Load user settings
with open("soar_score_user_settings.json", "r") as f:
    USER_SETTINGS = json.load(f)

def scale_stretch_score(value, low, mid, high, reverse=False):
    if reverse:
        if value >= low:
            return 0
        elif value <= high:
            return 100
        else:
            return max(0, min(100, (1 - (value - high) / (low - high)) * 100))
    else:
        if value <= low:
            return 0
        elif value >= high:
            return 100
        elif value == mid:
            return 75
        else:
            return max(0, min(100, ((value - low) / (high - low)) * 100))

def fetch_data(symbol, api_key, function):
    url = "https://www.alphavantage.co/query"
    params = {"function": function, "symbol": symbol, "apikey": api_key}
    response = requests.get(url, params=params)
    return response.json().get("annualReports", [])

def get_years_used(data_list):
    return len(data_list)

def score_fcf_metrics(cashflow):
    fcf = []
    revenue = []
    for r in cashflow:
        try:
            ocf = float(r.get("operatingCashflow", 0))
            capex = float(r.get("capitalExpenditures", 0))
            total_rev = float(r.get("totalRevenue", 1))
            fcf_val = ocf - capex
            fcf.append(fcf_val)
            revenue.append(total_rev)
        except:
            continue
    slope = linregress(range(len(fcf)), fcf).slope if len(fcf) >= 2 else 0
    margin = mean([(fcf[i] / revenue[i]) if revenue[i] else 0 for i in range(len(fcf))])
    inc = all(x <= y for x, y in zip(fcf, fcf[1:])) and all(x > 0 for x in fcf)
    score_inc = 100 if inc else 50
    slope_score = scale_stretch_score(slope / 1e6, **USER_SETTINGS["Profitability"]["Free Cash Flow Slope"])
    margin_score = scale_stretch_score(margin * 100, **USER_SETTINGS["Efficiency & Returns"]["FCF Margin"])
    return score_inc, slope_score, margin_score

def score_growth(metric_key, data, category, subkey):
    values = [float(r.get(metric_key, 0)) for r in data if r.get(metric_key)]
    if len(values) < 2: return 50
    growth = [(values[i] - values[i - 1]) / abs(values[i - 1]) for i in range(1, len(values)) if values[i - 1] != 0]
    avg = mean(growth)
    return scale_stretch_score(avg * 100, **USER_SETTINGS[category][subkey])

def score_ratio(numer_key, denom_key, data, category, subkey, reverse=False):
    values = []
    for r in data:
        try:
            numer = float(r.get(numer_key, 0))
            denom = float(r.get(denom_key, 1))
            if denom != 0:
                values.append(numer / denom)
        except:
            continue
    if not values:
        return 50
    avg = mean(values)
    return scale_stretch_score(avg, **USER_SETTINGS[category][subkey], reverse=reverse)

def score_trend(values, category, subkey):
    if len(values) < 2:
        return 50
    slope = linregress(range(len(values)), values).slope
    return scale_stretch_score(slope, **USER_SETTINGS[category][subkey])

# App UI
app_ui = ui.page_fluid(
    ui.panel_title("📊 Soar Score Analyzer"),
    ui.input_text("symbol", "Ticker Symbol (e.g., AAPL)", placeholder="AAPL"),
    ui.input_text("api_key", "Alpha Vantage API Key", placeholder="Your API Key"),
    ui.output_text_verbatim("results")
)

# Server logic
def server(input, output, session):
    @output
    @render.text
    def results():
        symbol = input.symbol().upper()
        api_key = input.api_key()
        if not symbol or not api_key:
            return "Please enter a symbol and API key."

        try:
            income = fetch_data(symbol, api_key, "INCOME_STATEMENT")
            balance = fetch_data(symbol, api_key, "BALANCE_SHEET")
            cashflow = fetch_data(symbol, api_key, "CASH_FLOW")

            years = min(get_years_used(income), get_years_used(balance), get_years_used(cashflow))

            fcf_score, fcf_slope, fcf_margin = score_fcf_metrics(cashflow)

            profitability = mean([
                fcf_score,
                fcf_slope,
                score_growth("netIncome", income, "Profitability", "Net Income Growth"),
                score_ratio("grossProfit", "totalRevenue", income, "Profitability", "Gross Margin"),
                score_ratio("operatingIncome", "totalRevenue", income, "Profitability", "Operating Margin")
            ])

            efficiency = mean([
                score_ratio("netIncome", "totalShareholderEquity", income, "Efficiency & Returns", "Return on Equity"),
                score_ratio("netIncome", "totalAssets", income, "Efficiency & Returns", "Return on Assets"),
                fcf_margin
            ])

            capital = mean([
                score_ratio("totalLiabilities", "totalShareholderEquity", balance, "Capital Structure", "Debt-to-Equity Ratio", reverse=True),
                score_ratio("operatingIncome", "interestExpense", income, "Capital Structure", "Interest Coverage Ratio"),
                score_ratio("totalLiabilities", "ebit", income, "Capital Structure", "Net Debt to EBITDA", reverse=True)
            ])

            shareholder = mean([
                score_trend([float(r.get("commonStockSharesOutstanding", 0)) for r in balance], "Shareholder Behavior", "Shares Outstanding Slope"),
                score_trend([float(r.get("repurchaseOfStock", 0)) for r in cashflow], "Shareholder Behavior", "Share Buybacks (Average $)")
            ])

            growth = mean([
                score_growth("totalRevenue", income, "Growth & Sustainability", "Revenue Growth Rate"),
                score_growth("eps", income, "Growth & Sustainability", "EPS Growth Rate"),
                score_trend([-float(r.get("capitalExpenditures", 0)) for r in cashflow], "Growth & Sustainability", "CapEx Trend (Positive Slope)"),
                score_ratio("researchAndDevelopment", "totalRevenue", income, "Growth & Sustainability", "R&D as % of Revenue")
            ])

            liquidity = mean([
                score_ratio("totalCurrentAssets", "totalCurrentLiabilities", balance, "Liquidity & Quality", "Current Ratio"),
                score_ratio("cashAndCashEquivalentsAtCarryingValue", "totalCurrentLiabilities", balance, "Liquidity & Quality", "Quick Ratio"),
                score_ratio("operatingCashflow", "totalCurrentLiabilities", cashflow, "Liquidity & Quality", "OCF to Liabilities")
            ])

            cf_quality = mean([
                score_ratio("operatingCashflow", "netIncome", cashflow, "Cash Flow Quality", "Net Income vs OCF Ratio"),
                score_ratio("netIncome", "totalAssets", income, "Cash Flow Quality", "Accrual Ratio", reverse=True)
            ])

            soar_score = round(0.30 * profitability + 0.25 * efficiency + 0.15 * capital + 0.10 * shareholder + 0.15 * growth + 0.05 * liquidity + 0.05 * cf_quality, 2)

            output_lines = [
                f"\n📊 Soar Score Breakdown for {symbol}:",
                f"Years of Historic Financial Data Used: {years}",
                f"Composite Soar Score: {soar_score}",
                f"Profitability: {round(profitability, 2)}",
                f"Efficiency & Returns: {round(efficiency, 2)}",
                f"Capital Structure: {round(capital, 2)}",
                f"Shareholder Behavior: {round(shareholder, 2)}",
                f"Growth & Sustainability: {round(growth, 2)}",
                f"Liquidity & Quality: {round(liquidity, 2)}",
                f"Cash Flow Quality: {round(cf_quality, 2)}"
            ]
            return "\n".join(output_lines)
        except Exception as e:
            return f"Error fetching data or calculating score: {e}"

# Run app
app = App(app_ui, server)

