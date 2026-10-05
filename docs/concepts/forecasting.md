(forecast-method)=
# Forecast Methodology

A forecast applies a household's saved decisions to many possible economic
futures. Household composition, employment, retirement dates, spending rules,
and planning lifetimes stay fixed within a scenario. Markets and inflation vary.
The forecast evaluates those decisions; it does not search for an optimal
retirement age or anticipate future market returns when making withdrawals.

## Annual accounting

The opening Wealth snapshot supplies assets and debts once. Each year begins
with the selected portfolio rebalancing rule. Existing holdings earn that year's
return; pay, contributions, benefits, spending, and withdrawals occur at year end.
New savings first earn returns the following year. Recurring cash flows use the
price level at the beginning of the year; plotted balances use the year-end
price level. A partial first year compounds only the remaining fraction of
annual returns and prorates recurring flows. One-time expenses listed for the
opening calendar year are treated as still planned at year end. Expenses from
earlier years are excluded.

Every annual ledger checks:

**Closing assets = opening assets + external income + investment return
− paid spending − paid taxes − debt payments.**

External income includes employer retirement contributions and vested shares.
Employee contributions, withdrawals, account transfers, and cash reserves move
existing money; they do not create wealth. Net wealth subtracts remaining debt
and unpaid tax liabilities. Debt interest increases the contractual balance;
unpaid tax is carried without estimating penalties or interest.

The model meets required distributions, then funds needs from taxable cash, taxable
investments, eligible pretax accounts, and Roth accounts. Withdrawals generate
additional tax where applicable; the calculation solves for enough gross
withdrawal to cover that tax. Cash reserves can be spent. Taxes receive payment
before debt and living costs. Unpaid amounts stay visible; the model does not
invent a loan or reduce the planned living budget to conceal a shortfall.

Surplus replenishes the cash reserve and follows saved saving priorities.
Retirement saving above payroll contribution limits goes to taxable investments.
Regular 2026 contribution caps rise with the model's price level; catch-up
contributions and new IRA/HSA contributions are outside the current scope.

Named Spending schedules provide household expenses, including any entered
child, healthcare, and care costs. The model does not generate these costs from
household facts or insurance coverage. HSA balances remain invested and count
toward total wealth, but are unavailable for spending.
Retirement benefits follow the Plan's reviewed person mapping. Each Plan supplies
its own benefit and yield assumptions.

## Economic simulations and comparisons

Monte Carlo draws stocks, bonds, cash, and inflation together each year. Their
log gross rates follow a correlated normal distribution; exponentiating gives
lognormal gross returns. Central assumptions are compound growth rates.
Volatility is the standard deviation of simple annual returns, while correlation
describes log gross rates. These distinctions matter when entering assumptions.
Years are independent in this initial parametric model.

New Economy scenarios include editable annual volatility defaults: **18 percentage
points for stocks, 6 for bonds, 1 for cash returns, and 2 for inflation**. These are
rounded planning assumptions informed by [Vanguard's 2026 capital-market
forecasts](https://www.vanguardsouthamerica.com/en/home/insights/economic-market-outlook/vanguard-capital-markets-model-forecasts-2026),
which list 15.1% volatility for U.S. stocks, 18.9% for non-U.S. stocks, 6.2% for
U.S. aggregate bonds, 1.1% for cash, and 1.7% for inflation. The defaults describe
diversified markets; they do not add individual-company risk to employer shares.

The correlation preset adapts the U.S. large-cap, aggregate-bond, cash, and
inflation relationships in [J.P. Morgan's 2026 USD assumptions, page
82](https://am.jpmorgan.com/content/dam/jpm-am-aem/global/en/insights/portfolio-insights/ltcma/noindex/ltcma-full-report.pdf#page=82).
The stored correlations of log gross rates are:

| | Stocks | Bonds | Cash | Inflation |
|---|---:|---:|---:|---:|
| Stocks | 1 | 0.302 | 0.010 | −0.010 |
| Bonds | 0.302 | 1 | 0.100 | −0.240 |
| Cash | 0.010 | 0.100 | 1 | −0.030 |
| Inflation | −0.010 | −0.240 | −0.030 | 1 |

We treat the published correlations as simple-return targets and convert them
to log correlations at compound rates of 6%, 4.5%, 3%, and 2.5%, using the
volatilities above. The six published pairwise targets, in table order, are
0.30, 0.01, −0.01, 0.10, −0.24, and −0.03. After rounding to three decimals, the
matrix remains positive definite and its implied simple-return correlations
differ from those targets by less than 0.001. This is an approximate planning
preset, not a reproduction of J.P. Morgan's model: its
[methodology](https://am.jpmorgan.com/content/dam/jpm-am-aem/global/en/insights/portfolio-insights/ltcma/noindex/ltcma-methodology-handbook.pdf#page=25)
uses monthly observations and weights economic regimes. The stored log
correlations remain fixed when you change an outlook; they are not recalibrated
automatically.

The defaults fill statistical inputs only. They do not choose expected returns,
change exploration ranges, or replace explicitly saved custom or missing values.
Saved Plans and forecast runs retain their actual numerical assumptions.

Selected alternatives use common random shocks. If their economic assumptions
differ, each transforms those shocks using its own growth, volatility, and
correlation inputs. The same run retains failed paths. Each path is converted
to reference-year purchasing power using its own inflation before calculating
percentiles or paired scenario differences.

Compared Plans share the same dated accounts and debts, adults in the same
order, adult ages and tax facts, and reference year. Children, their birth years,
and residence may differ. Each Plan resolves child-age expenses and dependent
tax treatment from its own Household copy. Children aged zero through 17 enter
the tax calculation; college spending does not extend that dependency period.

The 10th–90th percentile band contains the middle 80% of simulated balances
at each date. Its edges are not complete individual lives or worst cases.
Historical windows, jointly sampled historical blocks, and named stresses test
different assumptions. More samples reduce simulation noise; they do not prove
that the economic model describes every possible future. Low/high input ranges
remain alternative assumptions and are never silently converted into volatility.

## Tax and retirement assumptions

Forecasts reuse the application's pinned, corrected PolicyEngine tax engine.
The explicit policy is **2026 rules held constant in purchasing power**. Dollar
thresholds rise with simulated inflation, including thresholds that current law
does not index. Temporary 2026 deductions continue. Later changes, including
Washington's 2028 income tax, are excluded. This is a planning assumption, not
a calculation of enacted tax law for every future year.

Supported households begin married filing jointly, with full-year California
or Washington residence; the surviving person files single. The calculation
includes payroll taxes, ordinary retirement income, taxable Social Security,
qualified dividends, long-term gains, and separate federal and state capital
loss carryforwards. Opening capital-loss and AMT-credit carryforwards are assumed
zero. Partial-year taxes
annualize modeled income and prorate the resulting liability because prior
year-to-date tax records are unavailable. Dependent-care and renter credits
are not inferred from a general living budget.

Taxable sales use proportional average basis and long-term treatment. Taxable
cash has basis equal to its balance. All stocks, including employer shares,
follow the broad stock return series. This omits individual stock risk and
short-term holding-period effects. California HSA earnings and sales use a
separate state basis. HSA withdrawals are outside the current scope.

Pretax retirement balances are fully taxable. Ordinary retirement access starts
at age 60 in this annual model, and Roth withdrawals are assumed qualified.
Early withdrawal exceptions, Roth contribution-basis access, conversions, and
option exercise/sale accounting are excluded. The model transfers a deceased
person's retirement accounts to the surviving spouse, preserves taxable basis,
and resets unattributed capital loss carryforwards. It does not calculate estate
taxes or a taxable basis step-up. Benefits use entered amounts at entered ages;
survivor benefits are an approximation, not a Social Security claiming optimizer.

Required distributions use the applicable cohort start age and the IRS Uniform
Lifetime table. A sole-beneficiary spouse more than ten years younger needs a
different table, so affected cases are rejected. A partial opening year already
subject to required distributions needs a prior December balance and is also
rejected. See [IRS distribution rules and tables](https://www.irs.gov/publications/p590b).

Use [Compare Future Wealth](#run-forecast) to run a forecast and inspect its
annual accounting.
