
import requests
import json
from statistics import mean, stdev
from scipy.stats import linregress

# === LOAD USER SETTINGS ===
with open("soar_score_user_settings.json", "r") as f:
    user_settings = json.load(f)

# === USER INPUT ===
symbol = input("Enter the Ticker Symbol (e.g., AAPL): ").upper()
api_key = input("Enter your Alpha Vantage API Key: ")

# === FETCH DATA ===
def fetch_alpha_vantage_data(api_key, symbol, function):
    url = 'https://www.alphavantage.co/query'
    params = {"function": function, "symbol": symbol, "apikey": api_key}
    return requests.get(url, params=params).json().get('annualReports', [])

# === SCORING HELPERS ===
def stretch_score(value, low, high, base=50, range_stretch=50):
    if value <= low:
        return base - range_stretch
    elif value >= high:
        return base + range_stretch
    else:
        return base + ((value - low) / (high - low)) * (2 * range_stretch)

def scale_score(value, max_value=100, min_value=0):
    return max(min(value, max_value), min_value)

# === PROFITABILITY SCORING ===
def score_fcf_metrics(cashflow):
    settings = user_settings["Profitability"]["Free Cash Flow"]
    slope_settings = user_settings["Profitability"]["FCF Slope"]
    margin_settings = user_settings["Profitability"]["FCF Margin"]

    fcf_values = []
    revenue_values = []
    for r in cashflow:
        try:
            fcf = float(r.get('operatingCashflow', 0)) - float(r.get('capitalExpenditures', 0))
            rev = float(r.get('totalRevenue', 1))
            fcf_values.append(fcf)
            revenue_values.append(rev)
        except:
            continue

    if len(fcf_values) < 3:
        return 50, 50, 50, 0

    years_of_data = len(fcf_values)
    increasing = all(x <= y for x, y in zip(fcf_values, fcf_values[1:]))
    fcf_score = 100 if increasing and all(v > 0 for v in fcf_values) else 60

    slope, *_ = linregress(range(years_of_data), fcf_values)
    slope_score = stretch_score(slope, slope_settings['min_threshold'], 5e9)

    avg_margin = mean([fcf / rev if rev != 0 else 0 for fcf, rev in zip(fcf_values, revenue_values)])
    margin_score = stretch_score(avg_margin, margin_settings['min_threshold'], margin_settings['target_threshold'])

    return fcf_score, slope_score, margin_score, years_of_data

# === MAIN CALCULATION ===
def calculate_soar_score(symbol, api_key):
    income = fetch_alpha_vantage_data(api_key, symbol, 'INCOME_STATEMENT')
    cashflow = fetch_alpha_vantage_data(api_key, symbol, 'CASH_FLOW')
    balance = fetch_alpha_vantage_data(api_key, symbol, 'BALANCE_SHEET')

    if not income or not cashflow or not balance:
        print("❌ Missing data.")
        return None

    fcf_score, fcf_slope_score, fcf_margin_score, years = score_fcf_metrics(cashflow)

    profitability_score = round(mean([fcf_score, fcf_slope_score, fcf_margin_score]), 2)

    soar_score = round(
        0.30 * profitability_score
        # Additional category integration would follow
    , 2)

    return {
        "symbol": symbol,
        "Years of Historic Financial Data Used": years,
        "Profitability": profitability_score,
        "Composite Soar Score": soar_score
    }

# === EXECUTION ===
results = calculate_soar_score(symbol, api_key)

# === OUTPUT ===
if results:
    print(f"\n📊 Soar Score Breakdown for {results['symbol']}:")
    print(f"Years of Historic Financial Data Used: {results['Years of Historic Financial Data Used']}")
    for k, v in results.items():
        if k not in ['symbol', 'Years of Historic Financial Data Used']:
            print(f"{k}: {round(v, 2)}")

# === EFFICIENCY & RETURNS ===
def score_return(metric_key, income, balance):
    try:
        return scale_score(mean([
            float(income[i].get('netIncome', 0)) / float(balance[i].get(metric_key, 1))
            for i in range(min(len(income), len(balance)))
            if float(balance[i].get(metric_key, 0)) > 0
        ]) * 100)
    except:
        return 50

def score_roic_vs_wacc(income, balance):
    try:
        roic_vals = []
        for i in range(min(len(income), len(balance))):
            nopat = float(income[i].get('operatingIncome', 0)) * 0.7
            equity = float(balance[i].get('totalShareholderEquity', 0))
            debt = float(balance[i].get('totalLiabilities', 0))
            invested_capital = equity + debt
            if invested_capital > 0:
                roic_vals.append(nopat / invested_capital)
        avg_roic = mean(roic_vals)
        return scale_score((avg_roic - 0.08) * 100 + 50)
    except:
        return 50

# === CAPITAL STRUCTURE ===
def score_de_ratio(balance):
    try:
        return mean([
            scale_score(100 - min((float(r.get('totalLiabilities', 0)) / float(r.get('totalShareholderEquity', 1))) * 20, 100))
            for r in balance
        ])
    except:
        return 50

def score_interest_coverage(income):
    try:
        return mean([
            scale_score(min((float(r.get('operatingIncome', 0)) / float(r.get('interestExpense', 1))) * 10, 100))
            for r in income
        ])
    except:
        return 50

def score_net_debt_ebitda(balance, income):
    try:
        return mean([
            scale_score(100 - min(((float(balance[i].get('totalLiabilities', 0)) -
                                    float(balance[i].get('cashAndCashEquivalentsAtCarryingValue', 0))) /
                                   (float(income[i].get('ebit', 0)) +
                                    float(income[i].get('depreciation', 0)))) * 20, 100))
            for i in range(min(len(balance), len(income)))
        ])
    except:
        return 50

# === SHAREHOLDER BEHAVIOR ===
def score_shares_outstanding(balance):
    try:
        shares = [float(r.get('commonStockSharesOutstanding', 0)) for r in balance]
        if len(shares) < 3: return 50
        slope, *_ = linregress(range(len(shares)), shares)
        return scale_score(100 - slope / 1e6)
    except:
        return 50

def score_share_buybacks(cashflow):
    try:
        values = [float(r.get('repurchaseOfStock', 0)) for r in cashflow]
        avg_buyback = abs(mean([v for v in values if v < 0]))
        return scale_score(50 + avg_buyback / 1e9 * 20)
    except:
        return 50

# === GROWTH & SUSTAINABILITY ===
def score_growth_rate(metric_key, data):
    try:
        vals = [float(r.get(metric_key, 0)) for r in data]
        growth = [(vals[i] - vals[i-1]) / abs(vals[i-1]) for i in range(1, len(vals)) if vals[i-1] != 0]
        return scale_score(50 + mean(growth) * 100)
    except:
        return 50

def score_capex_trend(cashflow):
    try:
        capex = [-float(r.get('capitalExpenditures', 0)) for r in cashflow]
        slope, *_ = linregress(range(len(capex)), capex)
        return scale_score(50 + slope / 1e6)
    except:
        return 50

def score_rnd_ratio(income):
    try:
        return scale_score(50 + mean([
            float(r.get('researchAndDevelopment', 0)) / float(r.get('totalRevenue', 1))
            for r in income if float(r.get('totalRevenue', 0)) > 0
        ]) * 100)
    except:
        return 50

# === LIQUIDITY & QUALITY ===
def score_current_ratio(balance):
    try:
        return scale_score(100 - abs(mean([
            float(r.get('totalCurrentAssets', 0)) / float(r.get('totalCurrentLiabilities', 1))
            for r in balance
        ]) - 2) * 25)
    except:
        return 50

def score_quick_ratio(balance):
    try:
        return scale_score(100 - abs(mean([
            (float(r.get('cashAndCashEquivalentsAtCarryingValue', 0)) +
             float(r.get('shortTermInvestments', 0))) /
            float(r.get('totalCurrentLiabilities', 1))
            for r in balance
        ]) - 1.5) * 30)
    except:
        return 50

def score_ocf_liabilities(cashflow, balance):
    try:
        return scale_score(mean([
            float(cashflow[i].get('operatingCashflow', 0)) / float(balance[i].get('totalCurrentLiabilities', 1))
            for i in range(min(len(cashflow), len(balance)))
        ]) * 20)
    except:
        return 50

# === CASH FLOW QUALITY ===
def score_ni_vs_ocf(cashflow, income):
    try:
        return scale_score(100 - abs(mean([
            float(cashflow[i].get('operatingCashflow', 0)) / float(income[i].get('netIncome', 1))
            for i in range(min(len(cashflow), len(income)))
            if float(income[i].get('netIncome', 0)) != 0
        ]) - 1) * 50)
    except:
        return 50

def score_accrual_ratio(cashflow, income, balance):
    try:
        return scale_score(100 - abs(mean([
            (float(income[i].get('netIncome', 0)) - float(cashflow[i].get('operatingCashflow', 0))) /
            float(balance[i].get('totalAssets', 1))
            for i in range(min(len(income), len(cashflow), len(balance)))
        ])) * 500)
    except:
        return 50

# === CONSISTENCY ===
def score_std_dev(values):
    try:
        return scale_score(100 - stdev(values) * 100)
    except:
        return 50

def score_zscore(values):
    try:
        return scale_score(mean(values) / stdev(values) * 10)
    except:
        return 50
