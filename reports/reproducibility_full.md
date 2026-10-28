# Reproducibility report: full scale

Seed 20261022. The whole pipeline was run twice in separate processes with different PYTHONHASHSEED values, into separate folders.

**Result: PASS**  (43 files compared, 0 differences)

Delivered data in data/full: MATCH (all clean CSVs identical)


| File | Rows | SHA-256 (first 16) |
|---|---|---|
| clean/changeover_matrix.csv | 252 | 228dcd87c621ccde |
| clean/constraint_violation.csv | 3 | 38f0cb8726aae216 |
| clean/inventory_lot.csv | 348 | 2a367e9f0ccb1f14 |
| clean/machine.csv | 40 | fe2b08198fd6defa |
| clean/machine_calendar.csv | 1908 | f660bec56d93f213 |
| clean/material.csv | 200 | 276331ad8d171d18 |
| clean/material_requirement.csv | 2076 | bae3da43a6d7aac9 |
| clean/operation.csv | 2000 | 9b0472cea36f206c |
| clean/operation_schedule.csv | 2000 | 1ca89dcf2f82f347 |
| clean/order_status_event.csv | 1000 | 4a0cc68981e2a6b7 |
| clean/product.csv | 100 | 34278f66dc529485 |
| clean/production_order.csv | 500 | 440f1bb2e0dacd3b |
| clean/routing.csv | 100 | b856ad35a9a6b71d |
| clean/routing_step.csv | 400 | 1a2b4b2bfe918167 |
| clean/routing_step_machine.csv | 1738 | cfb456c395115661 |
| clean/routing_step_material.csv | 416 | 576cf25e24826efa |
| clean/schedule_event.csv | 2000 | 7b59523875e56cfc |
| ground_truth/ground_truth.csv | 80 | 31222723ab94ac30 |
| injected/changeover_matrix.csv | 252 | 228dcd87c621ccde |
| injected/constraint_violation.csv | 3 | 38f0cb8726aae216 |
| injected/inventory_lot.csv | 348 | 3c9ec2706f4f15cf |
| injected/machine.csv | 40 | fe2b08198fd6defa |
| injected/machine_calendar.csv | 1908 | f660bec56d93f213 |
| injected/material.csv | 200 | 276331ad8d171d18 |
| injected/material_requirement.csv | 2076 | bae3da43a6d7aac9 |
| injected/operation.csv | 2000 | 9b0472cea36f206c |
| injected/operation_schedule.csv | 2000 | 95cfa9a612993dba |
| injected/order_status_event.csv | 1000 | 4a0cc68981e2a6b7 |
| injected/product.csv | 100 | 34278f66dc529485 |
| injected/production_order.csv | 500 | 440f1bb2e0dacd3b |
| injected/routing.csv | 100 | b856ad35a9a6b71d |
| injected/routing_step.csv | 400 | 1a2b4b2bfe918167 |
| injected/routing_step_machine.csv | 1738 | cfb456c395115661 |
| injected/routing_step_material.csv | 416 | 576cf25e24826efa |
| injected/schedule_event.csv | 2000 | 7b59523875e56cfc |
| metrics/machine_metrics.csv | 40 | 500eea549a8d4f68 |
| metrics/operation_waits.csv | 2000 | 7de83e8cd3e8d48e |
| metrics/order_metrics.csv | 500 | a3cd463a117b77a2 |
