# Capacity Planning: decisions

Decisions taken before the remaining Capacity work (checklist CP-R03, CP-R23, CP-R28, CP-R32 in the
parity list). Each one states what was decided, why, and what it means for the code.

## D1. One planning threshold, from the tenant setting

**Decided:** the planning utilization (default 80%, set per session in `capacity/settings`) is the only
threshold. Every status uses the same ladder:

- Within Plan: demand ≤ Total × utilization
- Additional Demand: demand ≤ Total
- Capacity Up Required: demand > Total

This applies to SKU rows on the Capacity page, to the machine-group status on the Planning page, and to
what the agents say about capacity.

**Why:** two thresholds for the same question (80% on Capacity, 95% on Planning) make the same load read
as "Within Plan" in one place and "Additional Demand" in the other. The buffer between planning and total
is deliberate: it covers disruptions nobody sees in the plan (breakdowns, scrap, overtime). The agents must
quote the same numbers the page shows.

**Consequences:**
- `backend/app/tools/planning.py` (`get_capacity`, group status) changes from the fixed 95% to the
  session's utilization. Callers pass it in; `modules.py` and `agents/` pass the session value.
- Status colours on the Planning page change from the previous demo. Note this in the PR.

## D2. Strict process sequence is a tenant policy, enforced in one place

**Decided:** `strictProcessSequence` is a flag in the planning config, default **off**. When on, a work
order cannot advance to a step before the previous routing step of the same line is complete. The guard
lives in `advance_work_order` only.

**Why:** the rule is a policy of the plant, so it belongs in config, not scattered in screens. Default off
keeps the seed and existing demo behaviour unchanged; it is switched on for the strict demo.

**Consequences:**
- Add the flag to the capacity settings (or a planning config endpoint). `.env.example` is not affected;
  this is runtime config, not an environment variable.
- Tests cover both values: off allows any order, on rejects a skipped step with a field error.

## D3. Capacity Up is an agent proposal, not a separate request table

**Decided:** no `capacity_up_request` table. A need for more capacity becomes a proposal in Action Center
(add a shift, move a mold, schedule maintenance), and a person approves it there.

**Why:** Lite already has one approval path, and every change goes through it with an audit trail. A second
request workflow would split that trail and duplicate approval logic for the same decision.

**Consequences:**
- Recorded as a deliberate deviation from the production system in the PR.
- The Capacity page links a Capacity Up Required SKU to the matching proposal, when one exists.

## Not decided here (defaults chosen)

- **DRAFT snapshot status:** not added. Snapshots are Computed or Superseded. Revisit only if a real
  workflow needs a draft.
- **Recalculation mode:** stays synchronous. The production job queue is not needed at the demo's scale.

## Status

Accepted for implementation on `feat/capacity-utilization-threshold`. Changes to these decisions need a new entry
here, not an edit to the old one.
