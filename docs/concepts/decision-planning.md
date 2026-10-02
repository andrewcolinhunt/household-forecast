(decision-planning)=
# Planning Decisions and Their Future Impact

The purpose of this tool is to understand how household decisions change the
family's financial future. A move, job change, or spending choice should connect
to both the cash available now and the wealth and retirement options available
later.

The intended calculation follows this sequence:

**Decision → compensation, taxes, and expenses → saving by account and asset
type → future balances → retirement options.**

The current app implements the annual comparison at the beginning of this
sequence. It also saves reusable assumptions and named combinations in Scenarios.
Multi-year balances and retirement outcomes remain planned work.

## Compare decisions from the same starting point

Consider staying in the current job and location versus accepting a different
job in another state. Both alternatives begin with the same household assets
and debts. Each changes the relevant assumptions: pay, equity compensation,
employer retirement contributions, housing, childcare, state, and the date
the change takes effect.

The annual calculation estimates the resulting taxes, cash surplus or shortfall,
retirement contributions, and retained shares. The planned projection will
carry these amounts forward to compare future balances under the same economic
assumptions. This connects the difference in outcomes to the decision being
compared.

A higher compensation total need not provide more spendable cash. Employer
shares and retirement contributions must remain distinguishable from cash
throughout the comparison. Future projections also need explicit assumptions
about investment returns, share sales, and withdrawals before those assets can
fund spending.

## The role of Simplifi

Simplifi remains the household's tool for recording transactions, categorizing
expenses, and tracking accounts. Its data can establish observed spending and
starting balances for a plan. A manual snapshot or CSV import is useful when it
improves those inputs; transaction tracking and bank synchronization are not
objectives of this application.

Simplifi also provides a retirement planner with adjustable contributions,
expenses, returns, retirement age, and assumed tax rates. Its documented inputs
let you explore the effect of changing those quantities. They do not document
an integrated calculation from a new job and location through compensation,
state taxes, living costs, and annual saving to retirement outcomes.
[Simplifi retirement planner](https://support.simplifi.quicken.com/en/articles/10339048-retirement-planner)

That connected calculation is the reason to develop this tool. A useful
projection explains which decision changed an outcome and through which
income, expense, tax, or asset assumption.

## Current and planned capabilities

| Planning question | Status |
| --- | --- |
| How do supported job and full-year CA/WA alternatives change annual cash, taxes, saving, and equity? | Implemented in the annual model. |
| Can we save and mix work, spending, retirement, portfolio and economic assumptions from a chosen Wealth snapshot? | Implemented as input collection and saved combinations; no projection is calculated. |
| How do those choices change assets and available cash after five or ten years, or at a selected retirement age? | Planned: projecting from captured balances, dated changes and growth assumptions, with multi-year tax treatment. |
| Can each scenario fund a specified retirement lifestyle, and for how long? | Planned: retirement income, account withdrawals, taxes, and spending over time. |
| How sensitive is the comparison to returns, inflation, employment changes, or employer-share values? | Planned: comparable alternative assumptions, followed by simulations where useful. |

See [how the annual model works](index.md) for implemented accounting and tax
coverage. In particular, an annual full-year state comparison does not calculate
the transition year of a move. Existing inputs also do not cover future tax
years, retirement withdrawals, or stock sales.

## What makes a result useful

The intended comparison should show both the change from a baseline and the
assumptions responsible for it. Near-term cash shortfalls remain visible even
when total estimated wealth rises. Cash, taxable investments, retirement
accounts, employer equity, and debts retain their separate meanings.

Projected amounts must identify their dates and whether they are nominal
dollars or expressed in today's purchasing power. Retirement feasibility
requires a spending target, income and withdrawal assumptions, and a planning
horizon; reaching an asset total alone does not establish that someone can
retire.

A combined Plan can hold categories fixed while varying others: two Work choices
and three Spending choices create six combinations under the same retirement and
economic assumptions. The current app saves and previews these combinations.
Future forecasts will show wealth over time and whether funds last through both
lifetimes.

A combined Plan embeds its selected inputs. A later library edit does not change
that plan; replacing a selection is explicit. This makes alternatives reproducible
while allowing the same source assumptions to be reused. Household, Work, Spending,
Retirement, Investments, and Economy retain separate meanings. Saving instructions
allocate additions to goals; portfolio targets and cash reserves describe future choices; Wealth records
named account balances, tax treatment, and starting stock/bond/cash mixes. Balances
are pooled for household planning; person-specific withdrawal rules remain future
work.

Missing values remain unknown. Input completeness does not establish retirement
feasibility. Captured lifespan assumptions are different from a future calculated
age of funding exhaustion, and exploratory ranges are different from calibrated
probability distributions.

The next calculation capability is a reconciled projection from today's balances and
the existing annual scenarios. Detailed implementation choices and the proposed
sequence belong in the private development notes under `_dev`.
