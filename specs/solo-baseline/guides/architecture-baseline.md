# Architecture Baseline

Keep this document factual and compact. Replace bracketed prompts with evidence
from the repository; use `Unknown` when the current state has not been verified.

## Current State

- **System purpose:** [What user outcome does this system deliver?]
- **Runtime and deployment:** [Processes, runtimes, devices, and deployment shape in use now.]
- **Module boundaries:** [Major modules/packages and what each owns.]
- **Dependency direction:** [Allowed dependency flow and any known violations.]
- **Critical data flow:** [Entry point -> transformations -> storage/external effects.]
- **External dependencies:** [Services, protocols, data stores, and availability assumptions.]
- **Quality evidence:** [Tests, CI checks, observability, recovery drills, or measured gaps.]

## 6-12 Month Verifiable Targets

| Target | Current evidence | Verification measure | Target window |
|---|---|---|---|
| [Concrete architecture outcome] | [Repository/runtime evidence] | [Observable pass condition] | [6-12 month date] |

Targets describe observable end states, not implementation projects. Remove any
target whose completion cannot be demonstrated from code, runtime evidence, or a
repeatable validation command.

## Risk Gaps

| Risk gap | Evidence | Impact if unchanged | Mitigation trigger |
|---|---|---|---|
| [Current-state weakness] | [File, metric, incident, or unknown] | [Concrete failure mode] | [Condition that requires action] |

Rank gaps by likely user impact and recoverability. Do not convert hypothetical
future scale into a current requirement without evidence.

## ADR-Lite Decision Log

| Date | Decision | Context and evidence | Alternatives considered | Consequences | Revisit trigger |
|---|---|---|---|---|---|
| YYYY-MM-DD | [Chosen boundary or direction] | [Why this decision is needed now] | [Viable alternatives] | [Tradeoffs accepted] | [Evidence that invalidates it] |

Add a row when module ownership, dependency direction, critical data flow, or a
target architecture constraint changes. Keep superseded rows and point to the
new decision so the reasoning remains auditable.
