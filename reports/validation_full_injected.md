# Validation report: full scale, injected dataset

Seed 20261022; horizon 2026-11-02 for 28 days.

**Overall: PASS**  (18 passed, 0 failed, 0 skipped)

| Category | Check | Status | Issues |
|---|---|---|---|
| Primary-key uniqueness | Primary keys and unique fields have no duplicates | PASS | 0 |
| Foreign-key integrity | Every foreign key resolves to a parent row | PASS | 0 |
| Allowed enums | Enum fields hold only allowed values | PASS | 0 |
| Field validity | Required fields filled; types, ranges and ID patterns hold | PASS | 0 |
| Date ordering | Dates and timestamps are in a valid order and inside the horizon | PASS | 0 |
| Arithmetic reconciliation | Derived values recompute correctly from source records | PASS | 0 |
| Arithmetic reconciliation | Totals reconcile across tables | PASS | 0 |
| Lifecycle transitions | Status histories follow allowed transitions | PASS | 0 |
| Duplicates | No duplicate rows or duplicate business records | PASS | 0 |
| Business rules | Master-data rules (routings, eligibility, changeovers) | PASS | 0 |
| Business rules | Schedule integrity (IDs, statuses, durations, histories) | PASS | 0 |
| Schedule hard constraints | Hard constraints hold (or match the labelled anomalies) | PASS | 0 |
| Coverage and variation | Row counts match the configured volumes | PASS | 0 |
| Coverage and variation | Required scenarios and edge cases are present | PASS | 0 |
| Coverage and variation | Distributions show meaningful variation | PASS | 0 |
| Ground-truth separation | Ground truth conforms to its schema | PASS | 0 |
| Ground-truth separation | Ground truth is not leaked into input tables | PASS | 0 |
| Ground-truth separation | Labels reference real rows and are consistent | PASS | 0 |
