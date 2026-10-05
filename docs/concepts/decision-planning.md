(decision-planning)=
# Planning Decisions and Their Future Impact

The purpose of this tool is to understand how household decisions change the
family's financial future. A move, job change, or spending choice should connect
to both the cash available now and the wealth and retirement options available
later.

The intended calculation follows this sequence:

**Decision → compensation, taxes, and expenses → saving by account and asset
type → future balances → retirement options.**

The app implements annual comparisons and multi-year forecasts from saved
Plans. Forecasts connect starting Wealth, work, spending, retirement, investments,
and economic assumptions through both people's planning lifetimes.

Saved work belongs to the SQLite database open in the current browser session.
The household can pass a downloaded database between people or computers. Each
upload creates an independent working copy, so later changes do not synchronize
with other sessions or with the original file. Saving updates the working copy;
downloading preserves it for the next session. Unsaved editor drafts stay outside
the downloaded database.

## Compare decisions from the same starting point

Consider staying in the current job and location versus accepting a different
job in another state. Both alternatives begin with the same household assets
and debts. Each changes the relevant assumptions: pay, equity compensation,
employer retirement contributions, housing, childcare, state, and the date
the change takes effect.

The annual calculation estimates the resulting taxes, cash surplus or shortfall,
retirement contributions, and retained shares. The forecast
carry these amounts forward to compare future balances under the same economic
assumptions. This connects the difference in outcomes to the decision being
compared.

A higher compensation total need not provide more spendable cash. Employer
shares and retirement contributions must remain distinguishable from cash
throughout the comparison. Forecasts use explicit assumptions
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
| Can we reuse household facts and mix work, spending, retirement, portfolio and economic assumptions from a chosen Wealth snapshot? | Implemented as saved copies in combined Plans, selectable in Forecast. |
| How do those choices change assets and available cash after five or ten years, or at a selected retirement age? | Implemented with annual account balances and an explicit future-tax assumption. |
| How do expenses change as children grow up, work ends, or care is needed? | Implemented as named annual expenses with dated amounts and optional end dates. |
| Can each scenario fund a specified retirement lifestyle, and for how long? | Implemented with retirement income, eligible withdrawals, taxes, and visible shortfalls. |
| How sensitive is the comparison to returns and inflation? | Implemented with shared Monte Carlo futures, supplied historical observations, and illustrative stresses. |
| What must change to make a target retirement age feasible? | Planned: parameter search through the same forecast evaluator. |

See [how the annual model works](index.md) for implemented accounting and tax
coverage. In particular, an annual full-year state comparison does not calculate
the transition year of a move. Forecasts also assume full-year residence and
apply a documented planning policy for future taxes. See
[forecast methodology and limits](#forecast-method).

## Reuse household assumptions

Save Household scenarios for family choices such as no children, one child, or
two children. Each holds the adults, their ages in a reference year, children
and birth years, and tax facts. Residence belongs to the combined Plan, so the
same Household can be used for different locations. Shared defaults for annual
calculations remain separate from these saved Household scenarios.

A Plan keeps a copy of its selected Household. Later changes to the source take
effect only when you update that copy and save the Plan. Selecting another
Household preserves the Plan's residence and financial inputs. Its adults must
match the order already used by a Plan with financial inputs or starting Wealth.

Spending scenarios are complete, independent budgets. They do not inherit from
another Spending scenario or follow changes to an annual plan. For two locations
and three family sizes, keep six budgets and three Household scenarios in the
libraries, selecting matching pairs in Plans. Saving those budgets does not
create combined Plans.
Match each Plan's children to its budget: selecting a no-child budget does not
remove tax dependents, and adding a child does not create expenses. Child-age
expense links must refer to children present in that Plan's Household copy.

## Plan expenses through household changes

Spending is the sum of named expenses active in each year. A single household
budget is enough when more detail would not change the decision. Split out
children, healthcare, care, or other costs when their amounts or timing matter.
Categories organize the inputs; they do not estimate costs automatically.

Each recurring expense has one or more dated amounts. A new amount replaces only
that expense's previous amount; other expenses continue on their own schedules.
Use an end date for temporary expenses and a one-time entry for a single payment.
Retirement dates, a person's age, and the first year after one person's death can
anchor a change. A child's name may change without changing their linked dates.

The same Spending schedules cover working years, retirement, and survivor years.
For example, ordinary living costs can fall when both adults retire while a
healthcare expense continues and a temporary care expense begins later. Enter
healthcare and care as household costs you expect to pay. The app does not infer
insurance coverage, illness, care needs, or their probabilities.

All amounts use the profile's stated money year. Optional growth above inflation
runs from that year, including for later phases; entering a new phase does not
restart growth. General inflation is applied once. Starts include their year and
end dates exclude their year.

When healthcare or children are already included in a household total, separate
those costs by reducing the original expense. The reviewed split shows both
amounts and their unchanged combined total. Simply adding another expense would
increase planned spending.

Use a few plausible scenarios to test a decision: higher or longer childcare,
a period of lower earnings, higher healthcare costs, or several years of care.
These describe alternatives, not probabilities. Keep related assumptions together
in complete Plans when a choice changes both income and expenses. Compare
independent category alternatives only when every resulting combination makes
sense.

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
economic assumptions. Every combination also shares that Plan's Household copy.
Compare different family sizes using separate Plans with matching Household and
Spending choices. Select up to four combinations in Forecast to compare wealth
over time and whether funds last through both lifetimes.

A combined Plan embeds its selected inputs. A later library edit does not change
that plan; replacing a selection is explicit. The Plan timeline and its category
views edit the same embedded inputs, while libraries remain separate. This makes alternatives reproducible
while allowing the same source assumptions to be reused. Household, Work, Spending,
Retirement, Investments, and Economy retain separate meanings. Saving instructions
allocate additions to goals; portfolio targets and cash reserves describe future choices; Wealth records
named account balances, tax treatment, and starting stock/bond/cash mixes. Balances
are pooled for household planning; forecast details supply the ownership and cost
basis needed for withdrawals and taxes.

Missing values remain unknown. Input completeness does not establish retirement
feasibility. Captured lifespan assumptions are different from a future calculated
age of funding exhaustion, and exploratory ranges are different from calibrated
probability distributions.

Detailed implementation choices and development notes live under `_dev`.
