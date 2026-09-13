# Joint factor and strategy exploration

Read before proposing hypotheses, allocating trials or closing search. Factor discovery
and strategy design are recurring responsibilities of one researcher. The first admitted
library is a starting point; it does not close factor research.

## Explore mechanisms, then refine separate routes

Build a small portfolio of research routes from training observations and economic questions.
A route links a falsifiable mechanism, factor versions, an executable strategy hypothesis,
expected failure conditions and a bounded next experiment. Invent routes and implementations;
task examples are invitations, not a catalog. Stay within available lawful inputs and the
study's execution model. A route is a work record, not a child agent.

Draw proposals from unexplored mechanisms, diagnosed weaknesses, and transfers/combinations
with testable incremental value. Choose by expected information, unresolved requirements,
novelty and cost. Keep room for new ideas after the first batch instead of spending all
trials on lookbacks and position sizes around an early leader.

Evaluate the study's minimum distinct factor and strategy hypotheses before concentrating
search. Baselines and parameter variants consume trials but do not count toward mechanism
coverage. Count a hypothesis only after a valid numerical evaluation; an error or renamed
expression is not coverage. Record unmet coverage if data, budget or capability limits
prevent it. Do not invent successful routes to satisfy it.

Keep promising representatives with different tradeoffs in net return, risk, robustness,
cost and behavior. This frontier guides exploration; final ranking still follows the frozen
protocol. Review every shortlisted route, including those below the current leader. Give
each a diagnosis and a bounded refinement or an evidence-based park/reject decision. Weak
routes need not receive equal budgets or survive to final selection.

## Revise factors and their consumers together

Inspect factor evidence and the signal → target → order → return chain. Choose changes
from the diagnosed cause rather than a fixed sequence of tuning operators:

- Revise or invent a factor when its mechanism, normalization, availability, horizon or
  conditional usefulness is inadequate. Expressions and fitted policies get new versions.
- Revise a strategy when useful information is lost by entry/exit, holding, sizing,
  combination or costs. Different factors can require different signal-to-position mappings.
- Revise both when a new mechanism needs a different consumer. Explain the dependency;
  separately test compatible components where attribution informs the next decision.
- Transfer a factor between routes as an experiment against the recipient's baseline.
  Shared factors are allowed; a global top-k list is not every strategy's required input.

Compare parent/candidate on the same research dates, folds, costs and fitting policy.
Where compatible, hold the strategy fixed to test a factor revision and hold factors fixed
to test a rule revision. A small crossed comparison can resolve interactions; avoid full
Cartesian searches. Fit trainable parts only on training observations. Record regressions
as well as gains. Count new factor definitions/policies and new strategy bindings/rules;
a joint change can consume both budgets.

Keep exact factor bindings per strategy, including role and qualification scope. Never
overwrite factors or resolve a historical strategy against `latest`. Preserve parents,
implementation/fitted-state hashes and results. Updating a shared factor does not update
other consumers silently. Rejected versions remain rejected; a new role or hypothesis needs
a prospectively specified new trial, not retrospective relabeling.

For joint-research closure, show an executed factor revision motivated by research evidence
and its downstream strategy comparison, plus a strategy refinement comparison. These can
belong to different routes and can fail. An incompatible/rejected factor revision instead
needs numerical rejection evidence and an explanation of why consumer testing is invalid.
A library followed only by strategy parameter permutations is unfinished joint exploration.
Record unfulfilled work when an applicable limit prevents these checks.

## Diversity in definitions and behavior

| Level | Evidence |
| --- | --- |
| Hypotheses | Mechanism, inputs, role, expected response and failure regime. Affine renaming, lookback changes and resized copies remain variants of the parent hypothesis. |
| Factors | Aligned correlations, sample support, training-defined conditional behavior and role-specific diagnostics. Distinguish redundancy within a role from complementary roles. |
| Strategies | Correlations of aligned net returns/target exposures, active-session and entry overlap, holding behavior and fold/regime losses. Constant exposure makes correlation undefined, not evidence of diversity. |

Use [metrics-and-evaluation.md](metrics-and-evaluation.md) for definitions and admission.
Low correlation alone is not an economic explanation. Investigate redundancy without
manufacturing noisy formulas to pass diversity checks.

## Budget a portfolio of investigations

Before validation, allocate the supplied ceilings to initial mechanism coverage, route
refinement including new/revised factors, and paired comparisons/robustness/final preparation.
Reserve strategy trials to consume factor revisions and factor trials to investigate
downstream weaknesses. Baselines, ablations and failed attempts count. Reservations are
within total limits, not extra budgets. Update allocations with reasons; do not raise ceilings
or clear trial history. Avoid exhausting strategy trials while useful factor revisions have
no budgeted consumer evaluation.

A round is a bounded scheduled set of experiments followed by a portfolio review. Before
its first call, record routes, trial/submission slots and questions. Respect batch ceilings;
close the round when scheduled attempts finish/fail. Do not leave it open to evade limits.
Where the study defers patience until initial diversity review, that review occurs once
coverage is met or found infeasible; all other ceilings apply throughout. Adding routes
cannot defer it indefinitely. Thereafter apply the frozen validation objective's global
non-improvement rule. Branch-local gains guide allocation but an IC improvement or report
change alone does not reset global patience. Factor results without a budgeted consumer
comparison remain partial research, not qualified strategy improvements.

Before final freeze reconcile coverage, every shortlisted route's disposition, factor and
strategy revision evidence, binding-specific admission and robustness with saved results.
Apply the protocol's eligibility/readiness/test rules. Coverage measures work, not a
guarantee of diverse profitable survivors. After test exposure, further work is explicitly
exploratory; previous counts and exposure persist across versions and sessions.
