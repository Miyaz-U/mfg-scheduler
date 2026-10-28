# Data Dictionary

Generated from `src/mfgsched/schema.py`. Do not edit by hand.

## Design decisions

- **D1**: All datetimes are ISO text in plant-local time (no time zones).
- **D2**: An operation (changeover + run) must fit inside one shift row; no splitting.
- **D3**: A shift's usable window starts at shift_start and lasts available_minutes.
- **D4**: Labor = operators_assigned (calendar) versus operators_required (machine).
- **D5**: Each material requirement is allocated to one inventory lot.
- **D6**: Shared tooling is a tool_group code on a routing step; same code cannot overlap.
- **D7**: Changeover lookup: machine group + (previous family, next family); first op on a machine = 0.
- **D8**: An unschedulable operation gets no OperationSchedule row, only a ConstraintViolation.
- **D9**: Makespan and lateness are computed in the metrics step, not stored.
- **D10**: An order is PLANNED only if all its operations are scheduled; otherwise it stays NEW and violations explain the gap.
- **D11**: An order is late if its last operation ends after the end of due_date (SOFT violation).
- **D12**: In a batch run, NEW -> PLANNED events are stamped 2 hours after the order was received.
- **D13**: Dispatching is greedy by time (urgent first, then due date) with no backfilling.
- **D14**: Each injected case changes exactly one record; neighbouring records are excluded so labels stay unambiguous.
- **D15**: Boundary cases (margin 0) are valid rows labelled ACCEPT, used to measure false alarms.
- **D16**: MATERIAL_NOT_AVAILABLE is injected by moving the availability of a lot used by a single requirement.

## Tables

### Product

*core*: A sellable item (one row per SKU).

- **Primary key:** product_id
- **Rule:** effective_to >= effective_from when present
- **Rule:** OBSOLETE products must have effective_to set

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| product_id | str | yes | pattern `^PROD-\d{4}-\d{6}$` | - | yes | Unique product key. |
| sku | str | yes | pattern `^SKU-\d{4}$` | - | yes | Stock-keeping unit; used inside route IDs. |
| product_name | str | yes | - | - | no | Human-readable name. |
| product_family | str | yes | one of: FAM-A, FAM-B, FAM-C, FAM-D, FAM-E, FAM-F | - | no | Family used for sequence-dependent changeovers. |
| effective_from | date | yes | - | - | no | First date the product may be ordered. |
| effective_to | date | no | - | - | no | Last date it may be ordered; empty means open-ended. |
| lifecycle_state | str | yes | one of: ACTIVE, OBSOLETE | - | no | Lifecycle state. |

### Routing

*core*: A versioned recipe (ordered steps) for making a product.

- **Primary key:** route_id
- **Foreign key:** (product_id) references Product(product_id); cardinality 1 : 1..N; required
- **Rule:** route_id suffix equals route_version zero-padded to 2 digits
- **Rule:** At most one ACTIVE routing per product on any date

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| route_id | str | yes | pattern `^ROUTE-SKU-\d{4}-\d{2}$` | - | yes | Unique routing key: ROUTE-<sku>-<version>. |
| product_id | str | yes | - | - | no | Product this routing makes. |
| route_version | int | yes | >= 1 | - | no | Version number. |
| effective_from | date | yes | - | - | no | First date the routing is valid. |
| effective_to | date | no | - | - | no | Last valid date; empty means open-ended. |
| lifecycle_state | str | yes | one of: DRAFT, ACTIVE, RETIRED | - | no | Lifecycle state. |

### RoutingStep

*child*: One ordered step inside a routing (the template).

- **Primary key:** route_id, step_seq
- **Foreign key:** (route_id) references Routing(route_id); cardinality 1 : 1..N; required
- **Rule:** step_seq values are consecutive starting at 1 within a route

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| route_id | str | yes | - | - | no | Parent routing. |
| step_seq | int | yes | >= 1 | - | no | Position in the routing, starting at 1. |
| op_type | str | yes | one of: CUT, MILL, DRILL, WELD, PAINT, ASSEMBLE, TEST | - | no | Machine group that can do this step. |
| run_minutes_per_unit | float | yes | >= 0.1 | min/unit | no | Run time per unit. |
| fixed_minutes | float | yes | >= 0 | min | no | Fixed time per operation (load/unload). |
| tool_group | str | no | pattern `^TOOL-\d{2}$` | - | no | Shared tooling code; empty if none (D6). |

### Machine

*core*: A production machine belonging to one machine group.

- **Primary key:** machine_id

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| machine_id | str | yes | pattern `^MACH-\d{3}$` | - | yes | Unique machine key. |
| machine_name | str | yes | - | - | no | Human-readable name. |
| machine_group | str | yes | one of: CUT, MILL, DRILL, WELD, PAINT, ASSEMBLE, TEST | - | no | Type of work the machine does; must match RoutingStep.op_type. |
| lifecycle_state | str | yes | one of: ACTIVE, MAINTENANCE, RETIRED | - | no | Lifecycle state. |
| operators_required | int | yes | >= 1; <= 3 | people | no | Crew needed to run the machine. |
| speed_factor | float | yes | >= 0.5; <= 1.5 | - | no | 1.0 = standard speed; 1.2 = 20 percent faster. |
| commissioned_on | date | yes | - | - | no | Date the machine entered service. |
| effective_to | date | no | - | - | no | Retirement date; empty if still in service. |

### RoutingStepMachine

*bridge*: N:M bridge: which machines may run which step.

- **Primary key:** route_id, step_seq, machine_id
- **Foreign key:** (route_id, step_seq) references RoutingStep(route_id, step_seq); cardinality 1 : 1..N; required
- **Foreign key:** (machine_id) references Machine(machine_id); cardinality 1 : 0..N; required
- **Rule:** Machine.machine_group must equal RoutingStep.op_type
- **Rule:** Every step has at least one eligible machine

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| route_id | str | yes | - | - | no | Routing. |
| step_seq | int | yes | >= 1 | - | no | Step number. |
| machine_id | str | yes | - | - | no | Eligible machine. |
| is_preferred | bool | yes | - | - | no | Preferred machine for this step. |

### Material

*core*: A raw material or purchased component.

- **Primary key:** material_id

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| material_id | str | yes | pattern `^MAT-\d{4}$` | - | yes | Unique material key. |
| material_name | str | yes | - | - | no | Human-readable name. |
| material_category | str | yes | one of: STEEL, ALUMINUM, PLASTIC, FASTENER, COATING, ELECTRONICS, PACKAGING | - | no | Category. |
| unit_of_measure | str | yes | one of: KG, EA, L, M | - | no | Unit used for quantities. |
| standard_lead_time_days | int | yes | >= 1; <= 60 | days | no | Typical supplier lead time. |

### RoutingStepMaterial

*bridge*: N:M bridge: material needed per unit for a step.

- **Primary key:** route_id, step_seq, material_id
- **Foreign key:** (route_id, step_seq) references RoutingStep(route_id, step_seq); cardinality 1 : 0..N; required
- **Foreign key:** (material_id) references Material(material_id); cardinality 1 : 0..N; required

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| route_id | str | yes | - | - | no | Routing. |
| step_seq | int | yes | >= 1 | - | no | Step number. |
| material_id | str | yes | - | - | no | Material used. |
| qty_per_unit | float | yes | >= 0.001 | material UoM | no | Material per finished unit. |

### InventoryLot

*core*: A received (or expected) batch of one material.

- **Primary key:** lot_id
- **Foreign key:** (material_id) references Material(material_id); cardinality 1 : 0..N; required
- **Rule:** available_at >= expected_available_at

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| lot_id | str | yes | pattern `^LOT-\d{4}-\d{6}$` | - | yes | Unique lot key. |
| material_id | str | yes | - | - | no | Material in this lot. |
| quantity_received | float | yes | >= 0.001 | - | no | Lot size in the material's unit. |
| expected_available_at | datetime | yes | - | - | no | Originally promised availability. |
| available_at | datetime | yes | - | - | no | Actual availability; equals expected unless the lot is delayed. |
| delay_days | int | yes | >= 0 | days | no | Whole days the lot is late. **Derived:** max(0, whole days between expected_available_at and available_at) |
| is_delayed | bool | yes | - | - | no | True if the lot is late. **Derived:** delay_days > 0 |
| quantity_allocated | float | yes | >= 0 | - | no | Quantity reserved by requirements. **Derived:** sum of MaterialRequirement.required_qty where allocated_lot_id = lot_id |
| quantity_remaining | float | yes | >= 0 | - | no | Unreserved quantity. **Derived:** quantity_received - quantity_allocated |
| lot_status | str | yes | one of: IN_TRANSIT, AVAILABLE, EXHAUSTED | - | no | Snapshot status at the horizon start. **Derived:** EXHAUSTED if quantity_remaining = 0; IN_TRANSIT if available_at is after horizon start; else AVAILABLE |

### MachineCalendar

*core*: One row per machine, day and shift: capacity, downtime and staffing.

- **Primary key:** calendar_id
- **Foreign key:** (machine_id) references Machine(machine_id); cardinality 1 : 1..N; required
- **Unique together:** (machine_id, calendar_date, shift_code)
- **Rule:** shift_end > shift_start
- **Rule:** downtime_minutes > 0 if and only if downtime_reason != NONE
- **Rule:** Slot is usable only if operators_assigned >= Machine.operators_required and available_minutes > 0
- **Rule:** Usable window = shift_start to shift_start + available_minutes (D3)

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| calendar_id | str | yes | pattern `^CAL-MACH-\d{3}-\d{8}-[ABC]$` | - | yes | Unique calendar-slot key. |
| machine_id | str | yes | - | - | no | Machine this slot belongs to. |
| calendar_date | date | yes | - | - | no | Date the shift starts. |
| shift_code | str | yes | one of: A, B, C | - | no | Shift identifier. |
| shift_start | datetime | yes | - | - | no | Shift start. |
| shift_end | datetime | yes | - | - | no | Shift end (may be on the next day). |
| planned_break_minutes | int | yes | >= 0 | min | no | Scheduled breaks. |
| downtime_minutes | int | yes | >= 0 | min | no | Maintenance or breakdown time. |
| downtime_reason | str | yes | one of: NONE, PLANNED_MAINTENANCE, BREAKDOWN | - | no | Why the machine is down (NONE if not). |
| operators_assigned | int | yes | >= 0; <= 6 | people | no | People staffed on this slot. |
| available_minutes | int | yes | >= 0 | min | no | Usable production time in the slot. **Derived:** (shift_end - shift_start) in minutes - planned_break_minutes - downtime_minutes |

### ChangeoverMatrix

*core*: Sequence-dependent setup time when a machine group switches product family.

- **Primary key:** machine_group, from_family, to_family
- **Rule:** from_family == to_family implies changeover_minutes = 0
- **Rule:** Matrix is not required to be symmetric
- **Rule:** Looked up by value (no FK); every group/family pair must exist (D7)

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| machine_group | str | yes | one of: CUT, MILL, DRILL, WELD, PAINT, ASSEMBLE, TEST | - | no | Machine group the setup applies to. |
| from_family | str | yes | one of: FAM-A, FAM-B, FAM-C, FAM-D, FAM-E, FAM-F | - | no | Family just finished. |
| to_family | str | yes | one of: FAM-A, FAM-B, FAM-C, FAM-D, FAM-E, FAM-F | - | no | Family about to start. |
| changeover_minutes | int | yes | >= 0; <= 240 | min | no | Setup time. |

### ProductionOrder

*core*: A customer/plant order to make a quantity of a product.

- **Primary key:** order_id
- **Foreign key:** (product_id) references Product(product_id); cardinality 1 : 0..N; required
- **Foreign key:** (route_id) references Routing(route_id); cardinality 1 : 0..N; required
- **Rule:** due_date >= release_date
- **Rule:** Routing.product_id must equal ProductionOrder.product_id
- **Rule:** Routing must be ACTIVE on release_date

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| order_id | str | yes | pattern `^ORD-\d{4}-\d{6}$` | - | yes | Unique order key. |
| product_id | str | yes | - | - | no | Product ordered. |
| route_id | str | yes | - | - | no | Routing version frozen at order time. |
| quantity | int | yes | >= 1; <= 5000 | EA | no | Units to make. |
| release_date | date | yes | - | - | no | Earliest date work may start. |
| due_date | date | yes | - | - | no | Customer due date. |
| priority | int | yes | one of: 1, 2, 3, 4 | - | no | 1 = most urgent, 4 = least. |
| is_urgent | bool | yes | - | - | no | Urgent flag. **Derived:** priority == 1 |
| order_status | str | yes | one of: NEW, PLANNED, IN_PROGRESS, COMPLETED, CANCELLED | - | no | Current status (history is in OrderStatusEvent). |

### Operation

*core*: One step of one order (the order-level instance of a RoutingStep).

- **Primary key:** operation_id
- **Foreign key:** (order_id) references ProductionOrder(order_id); cardinality 1 : 1..N; required
- **Foreign key:** (route_id, step_seq) references RoutingStep(route_id, step_seq); cardinality 1 : 0..N; required
- **Foreign key:** (predecessor_operation_id) references Operation(operation_id); cardinality 1 : 0..1; optional
- **Rule:** Operation.route_id equals ProductionOrder.route_id
- **Rule:** predecessor is the operation of the same order with step_seq - 1
- **Rule:** standard_minutes must fit within one shift (D2)

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| operation_id | str | yes | pattern `^OP-ORD-\d{4}-\d{6}-\d{2}$` | - | yes | Unique operation key: OP-<order-id>-<step>. |
| order_id | str | yes | - | - | no | Owning order. |
| route_id | str | yes | - | - | no | Routing the step comes from. |
| step_seq | int | yes | >= 1 | - | no | Step number within the routing. |
| predecessor_operation_id | str | no | - | - | no | Operation that must finish first; empty for step 1. |
| standard_minutes | float | yes | >= 0.1 | min | no | Run time at standard machine speed. **Derived:** RoutingStep.fixed_minutes + order quantity * RoutingStep.run_minutes_per_unit |
| operation_status | str | yes | one of: PENDING, SCHEDULED, IN_PROGRESS, DONE, BLOCKED | - | no | Current status. |

### MaterialRequirement

*bridge*: Material an operation needs, and the lot reserved for it (D5).

- **Primary key:** requirement_id
- **Foreign key:** (operation_id) references Operation(operation_id); cardinality 1 : 0..N; required
- **Foreign key:** (material_id) references Material(material_id); cardinality 1 : 0..N; required
- **Foreign key:** (allocated_lot_id) references InventoryLot(lot_id); cardinality 1 : 0..N; optional
- **Unique together:** (operation_id, material_id)
- **Rule:** InventoryLot.material_id must equal material_id
- **Rule:** Total required_qty allocated to a lot <= its quantity_received

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| requirement_id | str | yes | pattern `^MREQ-\d{4}-\d{6}$` | - | yes | Unique requirement key. |
| operation_id | str | yes | - | - | no | Operation needing the material. |
| material_id | str | yes | - | - | no | Material needed. |
| allocated_lot_id | str | no | - | - | no | Lot reserved; empty if none found. |
| required_qty | float | yes | >= 0.001 | material UoM | no | Quantity needed. **Derived:** RoutingStepMaterial.qty_per_unit * ProductionOrder.quantity |

### OperationSchedule

*core*: A planned time slot for one operation on one machine.

- **Primary key:** schedule_id
- **Foreign key:** (operation_id) references Operation(operation_id); cardinality 1 : 0..N; required
- **Foreign key:** (machine_id) references Machine(machine_id); cardinality 1 : 0..N; required
- **Foreign key:** (calendar_id) references MachineCalendar(calendar_id); cardinality 1 : 0..N; required
- **Foreign key:** (previous_schedule_id) references OperationSchedule(schedule_id); cardinality 1 : 0..1; optional
- **Rule:** planned_end - planned_start = changeover_minutes + run_minutes
- **Rule:** Window must lie inside the usable window of calendar_id
- **Rule:** Machine must be in the routing step's eligible list (RoutingStepMachine)
- **Rule:** Start must be >= predecessor end and >= order release_date
- **Rule:** Start must be >= available_at of the allocated lot
- **Rule:** No two PLANNED rows on one machine overlap
- **Rule:** No two PLANNED rows with the same tool_group overlap

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| schedule_id | str | yes | pattern `^SCH-\d{4}-\d{6}$` | - | yes | Unique schedule key. |
| operation_id | str | yes | - | - | no | Operation being scheduled. |
| machine_id | str | yes | - | - | no | Machine chosen. |
| calendar_id | str | yes | - | - | no | Shift slot the operation sits inside (D2). |
| planned_start | datetime | yes | - | - | no | Start of changeover (or run if none). |
| planned_end | datetime | yes | - | - | no | End of run. |
| previous_schedule_id | str | no | - | - | no | Previous schedule row on the same machine; empty if first. |
| changeover_minutes | int | yes | >= 0 | min | no | Setup before the run. **Derived:** ChangeoverMatrix lookup (machine group, previous family, this family); 0 if first on machine |
| run_minutes | int | yes | >= 1 | min | no | Run time on this machine. **Derived:** ceil(Operation.standard_minutes / Machine.speed_factor) |
| schedule_version | int | yes | >= 1 | - | no | Increases on each reschedule. |
| schedule_status | str | yes | one of: PLANNED, SUPERSEDED, CANCELLED | - | no | Current status (history in ScheduleEvent). |

### OrderStatusEvent

*event*: Append-only history of order status changes.

- **Primary key:** event_id
- **Foreign key:** (order_id) references ProductionOrder(order_id); cardinality 1 : 1..N; required
- **Rule:** from_status to to_status must follow TRANSITIONS
- **Rule:** Rows are never updated or deleted

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| event_id | str | yes | pattern `^OEV-\d{4}-\d{6}$` | - | yes | Unique event key. |
| order_id | str | yes | - | - | no | Order that changed. |
| from_status | str | no | one of: NEW, PLANNED, IN_PROGRESS, COMPLETED, CANCELLED | - | no | Previous status; empty on creation. |
| to_status | str | yes | one of: NEW, PLANNED, IN_PROGRESS, COMPLETED, CANCELLED | - | no | New status. |
| event_ts | datetime | yes | - | - | no | When the change happened. |
| reason | str | no | - | - | no | Why it changed. |

### ScheduleEvent

*event*: Append-only history of schedule status changes.

- **Primary key:** event_id
- **Foreign key:** (schedule_id) references OperationSchedule(schedule_id); cardinality 1 : 1..N; required
- **Rule:** from_status to to_status must follow TRANSITIONS
- **Rule:** Rows are never updated or deleted

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| event_id | str | yes | pattern `^SEV-\d{4}-\d{6}$` | - | yes | Unique event key. |
| schedule_id | str | yes | - | - | no | Schedule row that changed. |
| from_status | str | no | one of: PLANNED, SUPERSEDED, CANCELLED | - | no | Previous status; empty on creation. |
| to_status | str | yes | one of: PLANNED, SUPERSEDED, CANCELLED | - | no | New status. |
| event_ts | datetime | yes | - | - | no | When the change happened. |
| reason | str | no | - | - | no | Why it changed. |

### ConstraintViolation

*output*: Recorded constraint violations and explained infeasibility (D8).

- **Primary key:** violation_id
- **Foreign key:** (operation_id) references Operation(operation_id); cardinality 1 : 0..N; required
- **Foreign key:** (schedule_id) references OperationSchedule(schedule_id); cardinality 1 : 0..N; optional
- **Rule:** LATE_DELIVERY is SOFT; every other type is HARD

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| violation_id | str | yes | pattern `^VIO-\d{4}-\d{6}$` | - | yes | Unique violation key. |
| operation_id | str | yes | - | - | no | Operation affected. |
| schedule_id | str | no | - | - | no | Schedule row involved; empty if unscheduled. |
| violation_type | str | yes | one of: MACHINE_OVERLAP, PRECEDENCE_VIOLATED, MATERIAL_NOT_AVAILABLE, OUTSIDE_SHIFT_CALENDAR, CHANGEOVER_OMITTED, UNSTAFFED_SHIFT, SHARED_TOOL_CONFLICT, MACHINE_NOT_ELIGIBLE, NO_CAPACITY_IN_HORIZON, PREDECESSOR_UNSCHEDULED, LATE_DELIVERY | - | no | Kind of violation. |
| severity | str | yes | one of: HARD, SOFT | - | no | HARD breaks feasibility; SOFT is a quality miss. |
| detected_at | datetime | yes | - | - | no | When the check ran. |
| details | str | yes | - | - | no | Plain-language explanation. |

### GroundTruth

*ground_truth*: Labels for injected anomalies and valid boundary cases. Separate file; never an input feature.

- **Primary key:** case_id
- **Rule:** INJECTED_VIOLATION rows must be flagged with the same violation type
- **Rule:** VALID_BOUNDARY rows must be accepted (no violation)

| Field | Type | Required | Constraint | Unit | Unique | Description |
|---|---|---|---|---|---|---|
| case_id | str | yes | pattern `^GT-\d{4}-\d{6}$` | - | yes | Unique case key. |
| issue_type | str | yes | one of: MACHINE_OVERLAP, PRECEDENCE_VIOLATED, MATERIAL_NOT_AVAILABLE, OUTSIDE_SHIFT_CALENDAR, CHANGEOVER_OMITTED, BOUNDARY_STARTS_AT_PREDECESSOR_END, BOUNDARY_STARTS_AT_MATERIAL_AVAILABLE, BOUNDARY_ENDS_AT_SHIFT_END | - | no | Injected anomaly or boundary-case type. |
| entity_type | str | yes | one of: OperationSchedule | - | no | Table holding the record. |
| entity_id | str | yes | - | - | no | Primary key of the record. |
| ground_truth | str | yes | one of: INJECTED_VIOLATION, VALID_BOUNDARY | - | no | Label. |
| expected_decision | str | yes | one of: FLAG_AND_REPORT, ACCEPT | - | no | What a validator should do. |
| description | str | yes | - | - | no | Exactly what was changed (or why it is valid). |

## Relationships

| Parent | Child | Parent columns | Child (FK) columns | Cardinality | FK required |
|---|---|---|---|---|---|
| Product | Routing | product_id | product_id | 1 : 1..N | yes |
| Product | ProductionOrder | product_id | product_id | 1 : 0..N | yes |
| Routing | RoutingStep | route_id | route_id | 1 : 1..N | yes |
| Routing | ProductionOrder | route_id | route_id | 1 : 0..N | yes |
| RoutingStep | RoutingStepMachine | route_id, step_seq | route_id, step_seq | 1 : 1..N | yes |
| Machine | RoutingStepMachine | machine_id | machine_id | 1 : 0..N | yes |
| RoutingStep | RoutingStepMaterial | route_id, step_seq | route_id, step_seq | 1 : 0..N | yes |
| Material | RoutingStepMaterial | material_id | material_id | 1 : 0..N | yes |
| Machine | MachineCalendar | machine_id | machine_id | 1 : 1..N | yes |
| Material | InventoryLot | material_id | material_id | 1 : 0..N | yes |
| ProductionOrder | Operation | order_id | order_id | 1 : 1..N | yes |
| RoutingStep | Operation | route_id, step_seq | route_id, step_seq | 1 : 0..N | yes |
| Operation | Operation | operation_id | predecessor_operation_id | 1 : 0..1 | no |
| Operation | MaterialRequirement | operation_id | operation_id | 1 : 0..N | yes |
| Material | MaterialRequirement | material_id | material_id | 1 : 0..N | yes |
| InventoryLot | MaterialRequirement | lot_id | allocated_lot_id | 1 : 0..N | no |
| Operation | OperationSchedule | operation_id | operation_id | 1 : 0..N | yes |
| Machine | OperationSchedule | machine_id | machine_id | 1 : 0..N | yes |
| MachineCalendar | OperationSchedule | calendar_id | calendar_id | 1 : 0..N | yes |
| OperationSchedule | OperationSchedule | schedule_id | previous_schedule_id | 1 : 0..1 | no |
| OperationSchedule | ScheduleEvent | schedule_id | schedule_id | 1 : 1..N | yes |
| ProductionOrder | OrderStatusEvent | order_id | order_id | 1 : 1..N | yes |
| Operation | ConstraintViolation | operation_id | operation_id | 1 : 0..N | yes |
| OperationSchedule | ConstraintViolation | schedule_id | schedule_id | 1 : 0..N | no |

## Many-to-many relationships (resolved by bridge tables)

- RoutingStep N:M Machine via **RoutingStepMachine**
- RoutingStep N:M Material via **RoutingStepMaterial**
- Operation N:M InventoryLot via **MaterialRequirement**

## Lifecycle transitions

**ProductionOrder.order_status**

- NEW -> PLANNED, CANCELLED
- PLANNED -> IN_PROGRESS, CANCELLED
- IN_PROGRESS -> COMPLETED, CANCELLED
- COMPLETED -> (final)
- CANCELLED -> (final)

**OperationSchedule.schedule_status**

- PLANNED -> SUPERSEDED, CANCELLED
- SUPERSEDED -> (final)
- CANCELLED -> (final)
