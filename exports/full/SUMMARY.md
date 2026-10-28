# Results summary (full scale)

Seed 20261022; horizon starts 2026-11-02 for 28 days. All data is synthetic.

## Schedule

- Operations scheduled: 2000 of 2000
- Orders planned: 500 of 500
- Makespan: 604.25 h (25.18 days), 2026-11-02 06:00:00 to 2026-11-27 10:15:00
- On-time rate: 99.4% (3 late orders); total tardiness 44.11 h, max 34.25 h
- Utilization: overall 29.55%, busiest machine 47.52%
- Changeovers: 1275 setups, 40873 min (17.99% of busy time)
- Materials: 25 of 348 lots delayed; 372 operations waited for material

## Validation

| Dataset | Overall | Passed | Failed | Skipped |
|---|---|---|---|---|
| clean | PASS | 15 | 0 | 3 |
| injected | PASS | 18 | 0 | 0 |

Skipped checks on the clean dataset are the ground-truth checks (clean data has no labels).

## Edge-case detection (injected dataset)

- Precision 1.0, recall 1.0, F1 1.0
- False positives: 0; boundary-case false alarms: 0

| Issue type | Injected | Detected |
|---|---|---|
| MACHINE_OVERLAP | 10 | 10 |
| PRECEDENCE_VIOLATED | 10 | 10 |
| MATERIAL_NOT_AVAILABLE | 10 | 10 |
| OUTSIDE_SHIFT_CALENDAR | 10 | 10 |
| CHANGEOVER_OMITTED | 10 | 10 |

Package contains 88 files; see `source_manifest.json` and `checksums.sha256`.
