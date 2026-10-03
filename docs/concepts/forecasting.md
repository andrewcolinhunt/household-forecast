(forecast-method)=
# Forecast Methodology

A forecast applies a household's saved decisions to many possible economic
futures. Employment, retirement dates, spending rules, and planning lifetimes
stay fixed within a scenario. Markets and inflation vary. The forecast evaluates
those decisions; it does not search for an optimal retirement age or anticipate
future market returns when making withdrawals.

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

Separate health-expense modeling is deferred. Household living budgets provide
all recurring spending; the model adds no separate health costs. HSA balances
remain invested and count toward total wealth, but are unavailable for spending.
Retirement benefits follow the Plan's reviewed person mapping. Each Plan supplies
its own benefit and yield assumptions.

## Economic simulations and comparisons

Monte Carlo draws stocks, bonds, cash, and inflation together each year. Their
log gross rates follow a correlated normal distribution; exponentiating gives
lognormal gross returns. Central assumptions are compound growth rates.
Volatility is the standard deviation of simple annual returns, while correlation
describes log gross rates. These distinctions matter when entering assumptions.
Years are independent in this initial parametric model.

Selected alternatives use common random shocks. If their economic assumptions
differ, each transforms those shocks using its own growth, volatility, and
correlation inputs. The same run retains failed paths. Each path is converted
to reference-year purchasing power using its own inflation before calculating
percentiles or paired scenario differences.

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
