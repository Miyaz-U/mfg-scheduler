# Manufacturing Scheduling: Synthetic Data, Scheduler and Validator

Generates reproducible synthetic manufacturing data (100 SKUs, 40 machines, 500
orders, 2,000 operations, 200 materials), builds a schedule that respects
routing, machine capacity, shift calendars, labor, changeovers, shared tooling
and material availability, injects labelled edge cases, validates everything,
and exports CSV, JSON and SQL seed data. All data is synthetic.

## Setup
```
python -m venv .venv
# activate the venv (Windows: .venv\Scripts\activate), then:
pip install -e ".[dev]"
python scripts/check_env.py
```

## One command: rebuild everything
```
python scripts/build_all.py --scale full      # required volumes
python scripts/build_all.py --scale starter   # small, fast, same generator
```
Fixed seed: **20261022** (set in `src/mfgsched/config.py`). Same seed, same files.

`build_all.py` runs these steps in order and stops at the first failure:

| # | Command | What it does |
|---|---|---|
| 1 | `scripts/run_schedule.py` | generate master + transaction data, build the schedule, verify it, write `data/<scale>/` |
| 2 | `scripts/compute_metrics.py` | makespan, lateness, utilization, changeover and material metrics, written to `data/<scale>/metrics/` |
| 3 | `scripts/inject_anomalies.py` | add labelled edge cases: `data/<scale>_injected/` plus `data/<scale>_ground_truth/` |
| 4 | `scripts/validate.py` | 18-check validation suite on both datasets, written to `reports/` |
| 5 | `scripts/build_docs.py` | data dictionary and ER diagram, written to `docs/` |
| 6 | `scripts/export_data.py` | package everything into `exports/<scale>/` |
| 7 | `scripts/check_reproducibility.py` | rebuild twice from the seed and compare checksums |

## Tests
```
pytest
```

## Deliverables: `exports/<scale>/`
| Path | Contents |
|---|---|
| `csv/clean/`, `csv/injected/` | 17 tables each (clean schedule, and schedule with injected edge cases) |
| `json/clean/`, `json/injected/` | the same tables as JSON arrays (empty values are `null`) |
| `sql/schema.sql` | SQLite DDL: primary keys, foreign keys, enum and range CHECKs |
| `sql/seed_clean.sql`, `sql/seed_injected.sql` | INSERT seed data, parents first |
| `ground_truth/` | labels as CSV, JSON and SQL, **kept separate** from the input tables |
| `metrics/` | order, machine and operation metrics plus `summary.json` |
| `validation/` | validation summaries (Markdown and JSON) for both datasets |
| `docs/` | data dictionary (Markdown and CSV) and ER diagram |
| `SUMMARY.md` | schedule results, validation status, edge-case detection |
| `source_manifest.json`, `checksums.sha256` | provenance, row counts, generator commands, file checksums |

Load the SQL into SQLite:
```
sqlite3 mfg.db < exports/full/sql/schema.sql
sqlite3 mfg.db < exports/full/sql/seed_clean.sql
```
Check the package is intact (from `exports/full/`): `sha256sum -c checksums.sha256`

## Reproducibility
`python scripts/check_reproducibility.py --scale full` rebuilds the whole dataset
twice from seed 20261022, in separate processes with different `PYTHONHASHSEED`
values, and compares row counts and SHA-256 checksums of every output file (CSV,
JSON, SQL, metrics, ground truth). It also confirms the result matches the delivered
`data/<scale>/` files. The result is written to `reports/reproducibility_<scale>.md`.
Each generator uses its own random stream derived from the seed (`get_rng`), so
changing one generator does not shift the numbers of another.

## Data model
17 input tables: the 10 core entities (Product, Routing, Operation, Machine,
MachineCalendar, Material, InventoryLot, ProductionOrder, OperationSchedule,
ChangeoverMatrix) plus bridge and event tables (RoutingStep, RoutingStepMachine,
RoutingStepMaterial, MaterialRequirement, OrderStatusEvent, ScheduleEvent,
ConstraintViolation). `GroundTruth` is separate. Keys, relationships,
cardinality, enums and design decisions D1 onward are in `docs/data_dictionary.md`
and `docs/er_diagram.md`, generated from `src/mfgsched/schema.py`.

IDs follow the required patterns: `PROD-2026-000001`, `ROUTE-<sku>-01`,
`OP-<order-id>-01`, `MACH-001`, `SCH-2026-000001`.

## How the scheduler works
A rule-based dispatcher takes operations by priority (urgent first), then earliest due date,
and puts each on the eligible machine where it finishes earliest. Every placement satisfies: routing precedence,
shift calendar and available minutes, operator staffing, sequence-dependent
changeover, shared-tool exclusivity and material availability. Operations that
cannot be placed are left unscheduled with a `ConstraintViolation` row that
explains why. An independent checker (`validation/schedule_checks.py`) re-derives
every hard constraint from the finished schedule.

## Edge cases and ground truth
Five required edge cases are injected (10 each at full scale): machine overlap,
precedence violated, material consumed before availability, schedule outside shift
calendar, changeover omitted. Matching valid boundary cases (which must not be
flagged) are added too. Labels live only in `ground_truth/`, and the validator
checks they never appear in the input tables.

## Provenance
No external or public data is used. See `source_manifest.json`.