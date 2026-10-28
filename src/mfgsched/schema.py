"""Data dictionary and relationship model: the single source of truth.

Generators, the scheduler, the validator and the documentation builder all
read this file, so the design cannot drift apart between them.
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# Allowed-value lists (enums)
# --------------------------------------------------------------------------
MACHINE_GROUPS = ("CUT", "MILL", "DRILL", "WELD", "PAINT", "ASSEMBLE", "TEST")
PRODUCT_FAMILIES = ("FAM-A", "FAM-B", "FAM-C", "FAM-D", "FAM-E", "FAM-F")
SHIFT_CODES = ("A", "B", "C")
MATERIAL_CATEGORIES = ("STEEL", "ALUMINUM", "PLASTIC", "FASTENER",
                       "COATING", "ELECTRONICS", "PACKAGING")
UNITS_OF_MEASURE = ("KG", "EA", "L", "M")
ORDER_STATUSES = ("NEW", "PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED")
SCHEDULE_STATUSES = ("PLANNED", "SUPERSEDED", "CANCELLED")
# The first five are the edge cases the problem statement requires us to inject.
VIOLATION_TYPES = (
    "MACHINE_OVERLAP", "PRECEDENCE_VIOLATED", "MATERIAL_NOT_AVAILABLE",
    "OUTSIDE_SHIFT_CALENDAR", "CHANGEOVER_OMITTED",      # the 5 injected edge cases
    "UNSTAFFED_SHIFT", "SHARED_TOOL_CONFLICT", "MACHINE_NOT_ELIGIBLE",
    "NO_CAPACITY_IN_HORIZON", "PREDECESSOR_UNSCHEDULED", "LATE_DELIVERY",
)
INJECTED_ISSUE_TYPES = VIOLATION_TYPES[:5]
BOUNDARY_ISSUE_TYPES = (
    "BOUNDARY_STARTS_AT_PREDECESSOR_END",
    "BOUNDARY_STARTS_AT_MATERIAL_AVAILABLE",
    "BOUNDARY_ENDS_AT_SHIFT_END",
)

DTYPES = ("str", "int", "float", "bool", "date", "datetime")


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Field:
    name: str
    dtype: str                      # one of DTYPES
    required: bool = True
    description: str = ""
    unit: str = ""
    allowed: tuple = ()             # enum values (empty = any)
    pattern: str = ""               # regex for ID-style strings
    min_value: float | None = None
    max_value: float | None = None
    unique: bool = False
    derived: str = ""               # formula text if computed from other data


@dataclass(frozen=True)
class Table:
    name: str
    kind: str                       # core/child/bridge/event/output/ground_truth
    description: str
    pk: tuple[str, ...]
    fields: tuple[Field, ...]
    unique_together: tuple[tuple[str, ...], ...] = ()
    rules: tuple[str, ...] = ()     # cross-table business rules (used in Step 8)

    def field(self, name: str) -> Field:
        return next(f for f in self.fields if f.name == name)


@dataclass(frozen=True)
class Relationship:
    parent: str
    child: str
    parent_cols: tuple[str, ...]
    child_cols: tuple[str, ...]
    child_card: str                 # "1..N", "0..N" or "0..1" (parent is always 1)
    fk_required: bool = True        # False = the FK column may be empty


F = Field

# --------------------------------------------------------------------------
# Core entities
# --------------------------------------------------------------------------
PRODUCT = Table(
    "Product", "core", "A sellable item (one row per SKU).",
    ("product_id",),
    (
        F("product_id", "str", True, "Unique product key.",
          pattern=r"^PROD-\d{4}-\d{6}$", unique=True),
        F("sku", "str", True, "Stock-keeping unit; used inside route IDs.",
          pattern=r"^SKU-\d{4}$", unique=True),
        F("product_name", "str", True, "Human-readable name."),
        F("product_family", "str", True,
          "Family used for sequence-dependent changeovers.",
          allowed=PRODUCT_FAMILIES),
        F("effective_from", "date", True, "First date the product may be ordered."),
        F("effective_to", "date", False,
          "Last date it may be ordered; empty means open-ended."),
        F("lifecycle_state", "str", True, "Lifecycle state.",
          allowed=("ACTIVE", "OBSOLETE")),
    ),
    rules=("effective_to >= effective_from when present",
           "OBSOLETE products must have effective_to set"),
)

ROUTING = Table(
    "Routing", "core", "A versioned recipe (ordered steps) for making a product.",
    ("route_id",),
    (
        F("route_id", "str", True, "Unique routing key: ROUTE-<sku>-<version>.",
          pattern=r"^ROUTE-SKU-\d{4}-\d{2}$", unique=True),
        F("product_id", "str", True, "Product this routing makes."),
        F("route_version", "int", True, "Version number.", min_value=1),
        F("effective_from", "date", True, "First date the routing is valid."),
        F("effective_to", "date", False, "Last valid date; empty means open-ended."),
        F("lifecycle_state", "str", True, "Lifecycle state.",
          allowed=("DRAFT", "ACTIVE", "RETIRED")),
    ),
    rules=("route_id suffix equals route_version zero-padded to 2 digits",
           "At most one ACTIVE routing per product on any date"),
)

MACHINE = Table(
    "Machine", "core", "A production machine belonging to one machine group.",
    ("machine_id",),
    (
        F("machine_id", "str", True, "Unique machine key.",
          pattern=r"^MACH-\d{3}$", unique=True),
        F("machine_name", "str", True, "Human-readable name."),
        F("machine_group", "str", True,
          "Type of work the machine does; must match RoutingStep.op_type.",
          allowed=MACHINE_GROUPS),
        F("lifecycle_state", "str", True, "Lifecycle state.",
          allowed=("ACTIVE", "MAINTENANCE", "RETIRED")),
        F("operators_required", "int", True, "Crew needed to run the machine.",
          unit="people", min_value=1, max_value=3),
        F("speed_factor", "float", True,
          "1.0 = standard speed; 1.2 = 20 percent faster.",
          min_value=0.5, max_value=1.5),
        F("commissioned_on", "date", True, "Date the machine entered service."),
        F("effective_to", "date", False, "Retirement date; empty if still in service."),
    ),
)

MACHINE_CALENDAR = Table(
    "MachineCalendar", "core",
    "One row per machine, day and shift: capacity, downtime and staffing.",
    ("calendar_id",),
    (
        F("calendar_id", "str", True, "Unique calendar-slot key.",
          pattern=r"^CAL-MACH-\d{3}-\d{8}-[ABC]$", unique=True),
        F("machine_id", "str", True, "Machine this slot belongs to."),
        F("calendar_date", "date", True, "Date the shift starts."),
        F("shift_code", "str", True, "Shift identifier.", allowed=SHIFT_CODES),
        F("shift_start", "datetime", True, "Shift start."),
        F("shift_end", "datetime", True, "Shift end (may be on the next day)."),
        F("planned_break_minutes", "int", True, "Scheduled breaks.",
          unit="min", min_value=0),
        F("downtime_minutes", "int", True, "Maintenance or breakdown time.",
          unit="min", min_value=0),
        F("downtime_reason", "str", True, "Why the machine is down (NONE if not).",
          allowed=("NONE", "PLANNED_MAINTENANCE", "BREAKDOWN")),
        F("operators_assigned", "int", True, "People staffed on this slot.",
          unit="people", min_value=0, max_value=6),
        F("available_minutes", "int", True, "Usable production time in the slot.",
          unit="min", min_value=0,
          derived="(shift_end - shift_start) in minutes - planned_break_minutes - downtime_minutes"),
    ),
    unique_together=(("machine_id", "calendar_date", "shift_code"),),
    rules=("shift_end > shift_start",
           "downtime_minutes > 0 if and only if downtime_reason != NONE",
           "Slot is usable only if operators_assigned >= Machine.operators_required "
           "and available_minutes > 0",
           "Usable window = shift_start to shift_start + available_minutes (D3)"),
)

MATERIAL = Table(
    "Material", "core", "A raw material or purchased component.",
    ("material_id",),
    (
        F("material_id", "str", True, "Unique material key.",
          pattern=r"^MAT-\d{4}$", unique=True),
        F("material_name", "str", True, "Human-readable name."),
        F("material_category", "str", True, "Category.", allowed=MATERIAL_CATEGORIES),
        F("unit_of_measure", "str", True, "Unit used for quantities.",
          allowed=UNITS_OF_MEASURE),
        F("standard_lead_time_days", "int", True, "Typical supplier lead time.",
          unit="days", min_value=1, max_value=60),
    ),
)

INVENTORY_LOT = Table(
    "InventoryLot", "core", "A received (or expected) batch of one material.",
    ("lot_id",),
    (
        F("lot_id", "str", True, "Unique lot key.",
          pattern=r"^LOT-\d{4}-\d{6}$", unique=True),
        F("material_id", "str", True, "Material in this lot."),
        F("quantity_received", "float", True, "Lot size in the material's unit.",
          min_value=0.001),
        F("expected_available_at", "datetime", True, "Originally promised availability."),
        F("available_at", "datetime", True,
          "Actual availability; equals expected unless the lot is delayed."),
        F("delay_days", "int", True, "Whole days the lot is late.", unit="days",
          min_value=0,
          derived="max(0, whole days between expected_available_at and available_at)"),
        F("is_delayed", "bool", True, "True if the lot is late.",
          derived="delay_days > 0"),
        F("quantity_allocated", "float", True, "Quantity reserved by requirements.",
          min_value=0,
          derived="sum of MaterialRequirement.required_qty where allocated_lot_id = lot_id"),
        F("quantity_remaining", "float", True, "Unreserved quantity.", min_value=0,
          derived="quantity_received - quantity_allocated"),
        F("lot_status", "str", True, "Snapshot status at the horizon start.",
          allowed=("IN_TRANSIT", "AVAILABLE", "EXHAUSTED"),
          derived="EXHAUSTED if quantity_remaining = 0; IN_TRANSIT if available_at is after horizon start; else AVAILABLE"),
    ),
    rules=("available_at >= expected_available_at",),
)

PRODUCTION_ORDER = Table(
    "ProductionOrder", "core", "A customer/plant order to make a quantity of a product.",
    ("order_id",),
    (
        F("order_id", "str", True, "Unique order key.",
          pattern=r"^ORD-\d{4}-\d{6}$", unique=True),
        F("product_id", "str", True, "Product ordered."),
        F("route_id", "str", True, "Routing version frozen at order time."),
        F("quantity", "int", True, "Units to make.", unit="EA",
          min_value=1, max_value=5000),
        F("release_date", "date", True, "Earliest date work may start."),
        F("due_date", "date", True, "Customer due date."),
        F("priority", "int", True, "1 = most urgent, 4 = least.",
          allowed=(1, 2, 3, 4)),
        F("is_urgent", "bool", True, "Urgent flag.", derived="priority == 1"),
        F("order_status", "str", True, "Current status (history is in OrderStatusEvent).",
          allowed=ORDER_STATUSES),
    ),
    rules=("due_date >= release_date",
           "Routing.product_id must equal ProductionOrder.product_id",
           "Routing must be ACTIVE on release_date"),
)

OPERATION = Table(
    "Operation", "core",
    "One step of one order (the order-level instance of a RoutingStep).",
    ("operation_id",),
    (
        F("operation_id", "str", True, "Unique operation key: OP-<order-id>-<step>.",
          pattern=r"^OP-ORD-\d{4}-\d{6}-\d{2}$", unique=True),
        F("order_id", "str", True, "Owning order."),
        F("route_id", "str", True, "Routing the step comes from."),
        F("step_seq", "int", True, "Step number within the routing.", min_value=1),
        F("predecessor_operation_id", "str", False,
          "Operation that must finish first; empty for step 1."),
        F("standard_minutes", "float", True,
          "Run time at standard machine speed.", unit="min", min_value=0.1,
          derived="RoutingStep.fixed_minutes + order quantity * RoutingStep.run_minutes_per_unit"),
        F("operation_status", "str", True, "Current status.",
          allowed=("PENDING", "SCHEDULED", "IN_PROGRESS", "DONE", "BLOCKED")),
    ),
    rules=("Operation.route_id equals ProductionOrder.route_id",
           "predecessor is the operation of the same order with step_seq - 1",
           "standard_minutes must fit within one shift (D2)"),
)

CHANGEOVER_MATRIX = Table(
    "ChangeoverMatrix", "core",
    "Sequence-dependent setup time when a machine group switches product family.",
    ("machine_group", "from_family", "to_family"),
    (
        F("machine_group", "str", True, "Machine group the setup applies to.",
          allowed=MACHINE_GROUPS),
        F("from_family", "str", True, "Family just finished.", allowed=PRODUCT_FAMILIES),
        F("to_family", "str", True, "Family about to start.", allowed=PRODUCT_FAMILIES),
        F("changeover_minutes", "int", True, "Setup time.", unit="min",
          min_value=0, max_value=240),
    ),
    rules=("from_family == to_family implies changeover_minutes = 0",
           "Matrix is not required to be symmetric",
           "Looked up by value (no FK); every group/family pair must exist (D7)"),
)

OPERATION_SCHEDULE = Table(
    "OperationSchedule", "core",
    "A planned time slot for one operation on one machine.",
    ("schedule_id",),
    (
        F("schedule_id", "str", True, "Unique schedule key.",
          pattern=r"^SCH-\d{4}-\d{6}$", unique=True),
        F("operation_id", "str", True, "Operation being scheduled."),
        F("machine_id", "str", True, "Machine chosen."),
        F("calendar_id", "str", True, "Shift slot the operation sits inside (D2)."),
        F("planned_start", "datetime", True, "Start of changeover (or run if none)."),
        F("planned_end", "datetime", True, "End of run."),
        F("previous_schedule_id", "str", False,
          "Previous schedule row on the same machine; empty if first."),
        F("changeover_minutes", "int", True, "Setup before the run.", unit="min",
          min_value=0,
          derived="ChangeoverMatrix lookup (machine group, previous family, this family); 0 if first on machine"),
        F("run_minutes", "int", True, "Run time on this machine.", unit="min",
          min_value=1,
          derived="ceil(Operation.standard_minutes / Machine.speed_factor)"),
        F("schedule_version", "int", True, "Increases on each reschedule.", min_value=1),
        F("schedule_status", "str", True, "Current status (history in ScheduleEvent).",
          allowed=SCHEDULE_STATUSES),
    ),
    rules=("planned_end - planned_start = changeover_minutes + run_minutes",
           "Window must lie inside the usable window of calendar_id",
           "Machine must be in the routing step's eligible list (RoutingStepMachine)",
           "Start must be >= predecessor end and >= order release_date",
           "Start must be >= available_at of the allocated lot",
           "No two PLANNED rows on one machine overlap",
           "No two PLANNED rows with the same tool_group overlap"),
)

# --------------------------------------------------------------------------
# Supporting tables
# --------------------------------------------------------------------------
ROUTING_STEP = Table(
    "RoutingStep", "child", "One ordered step inside a routing (the template).",
    ("route_id", "step_seq"),
    (
        F("route_id", "str", True, "Parent routing."),
        F("step_seq", "int", True, "Position in the routing, starting at 1.", min_value=1),
        F("op_type", "str", True, "Machine group that can do this step.",
          allowed=MACHINE_GROUPS),
        F("run_minutes_per_unit", "float", True, "Run time per unit.",
          unit="min/unit", min_value=0.1),
        F("fixed_minutes", "float", True, "Fixed time per operation (load/unload).",
          unit="min", min_value=0),
        F("tool_group", "str", False, "Shared tooling code; empty if none (D6).",
          pattern=r"^TOOL-\d{2}$"),
    ),
    rules=("step_seq values are consecutive starting at 1 within a route",),
)

ROUTING_STEP_MACHINE = Table(
    "RoutingStepMachine", "bridge", "N:M bridge: which machines may run which step.",
    ("route_id", "step_seq", "machine_id"),
    (
        F("route_id", "str", True, "Routing."),
        F("step_seq", "int", True, "Step number.", min_value=1),
        F("machine_id", "str", True, "Eligible machine."),
        F("is_preferred", "bool", True, "Preferred machine for this step."),
    ),
    rules=("Machine.machine_group must equal RoutingStep.op_type",
           "Every step has at least one eligible machine"),
)

ROUTING_STEP_MATERIAL = Table(
    "RoutingStepMaterial", "bridge", "N:M bridge: material needed per unit for a step.",
    ("route_id", "step_seq", "material_id"),
    (
        F("route_id", "str", True, "Routing."),
        F("step_seq", "int", True, "Step number.", min_value=1),
        F("material_id", "str", True, "Material used."),
        F("qty_per_unit", "float", True, "Material per finished unit.",
          unit="material UoM", min_value=0.001),
    ),
)

MATERIAL_REQUIREMENT = Table(
    "MaterialRequirement", "bridge",
    "Material an operation needs, and the lot reserved for it (D5).",
    ("requirement_id",),
    (
        F("requirement_id", "str", True, "Unique requirement key.",
          pattern=r"^MREQ-\d{4}-\d{6}$", unique=True),
        F("operation_id", "str", True, "Operation needing the material."),
        F("material_id", "str", True, "Material needed."),
        F("allocated_lot_id", "str", False, "Lot reserved; empty if none found."),
        F("required_qty", "float", True, "Quantity needed.", unit="material UoM",
          min_value=0.001,
          derived="RoutingStepMaterial.qty_per_unit * ProductionOrder.quantity"),
    ),
    unique_together=(("operation_id", "material_id"),),
    rules=("InventoryLot.material_id must equal material_id",
           "Total required_qty allocated to a lot <= its quantity_received"),
)

ORDER_STATUS_EVENT = Table(
    "OrderStatusEvent", "event", "Append-only history of order status changes.",
    ("event_id",),
    (
        F("event_id", "str", True, "Unique event key.",
          pattern=r"^OEV-\d{4}-\d{6}$", unique=True),
        F("order_id", "str", True, "Order that changed."),
        F("from_status", "str", False, "Previous status; empty on creation.",
          allowed=ORDER_STATUSES),
        F("to_status", "str", True, "New status.", allowed=ORDER_STATUSES),
        F("event_ts", "datetime", True, "When the change happened."),
        F("reason", "str", False, "Why it changed."),
    ),
    rules=("from_status to to_status must follow TRANSITIONS",
           "Rows are never updated or deleted"),
)

SCHEDULE_EVENT = Table(
    "ScheduleEvent", "event", "Append-only history of schedule status changes.",
    ("event_id",),
    (
        F("event_id", "str", True, "Unique event key.",
          pattern=r"^SEV-\d{4}-\d{6}$", unique=True),
        F("schedule_id", "str", True, "Schedule row that changed."),
        F("from_status", "str", False, "Previous status; empty on creation.",
          allowed=SCHEDULE_STATUSES),
        F("to_status", "str", True, "New status.", allowed=SCHEDULE_STATUSES),
        F("event_ts", "datetime", True, "When the change happened."),
        F("reason", "str", False, "Why it changed."),
    ),
    rules=("from_status to to_status must follow TRANSITIONS",
           "Rows are never updated or deleted"),
)

CONSTRAINT_VIOLATION = Table(
    "ConstraintViolation", "output",
    "Recorded constraint violations and explained infeasibility (D8).",
    ("violation_id",),
    (
        F("violation_id", "str", True, "Unique violation key.",
          pattern=r"^VIO-\d{4}-\d{6}$", unique=True),
        F("operation_id", "str", True, "Operation affected."),
        F("schedule_id", "str", False, "Schedule row involved; empty if unscheduled."),
        F("violation_type", "str", True, "Kind of violation.", allowed=VIOLATION_TYPES),
        F("severity", "str", True, "HARD breaks feasibility; SOFT is a quality miss.",
          allowed=("HARD", "SOFT")),
        F("detected_at", "datetime", True, "When the check ran."),
        F("details", "str", True, "Plain-language explanation."),
    ),
    rules=("LATE_DELIVERY is SOFT; every other type is HARD",),
)

GROUND_TRUTH = Table(
    "GroundTruth", "ground_truth",
    "Labels for injected anomalies and valid boundary cases. Separate file; never an input feature.",
    ("case_id",),
    (
        F("case_id", "str", True, "Unique case key.",
          pattern=r"^GT-\d{4}-\d{6}$", unique=True),
        F("issue_type", "str", True, "Injected anomaly or boundary-case type.",
          allowed=INJECTED_ISSUE_TYPES + BOUNDARY_ISSUE_TYPES),
        F("entity_type", "str", True, "Table holding the record.",
          allowed=("OperationSchedule",)),
        F("entity_id", "str", True, "Primary key of the record."),
        F("ground_truth", "str", True, "Label.",
          allowed=("INJECTED_VIOLATION", "VALID_BOUNDARY")),
        F("expected_decision", "str", True, "What a validator should do.",
          allowed=("FLAG_AND_REPORT", "ACCEPT")),
        F("description", "str", True, "Exactly what was changed (or why it is valid)."),
    ),
    rules=("INJECTED_VIOLATION rows must be flagged with the same violation type",
           "VALID_BOUNDARY rows must be accepted (no violation)"),
)

TABLES: tuple[Table, ...] = (
    PRODUCT, ROUTING, ROUTING_STEP, MACHINE, ROUTING_STEP_MACHINE,
    MATERIAL, ROUTING_STEP_MATERIAL, INVENTORY_LOT, MACHINE_CALENDAR,
    CHANGEOVER_MATRIX, PRODUCTION_ORDER, OPERATION, MATERIAL_REQUIREMENT,
    OPERATION_SCHEDULE, ORDER_STATUS_EVENT, SCHEDULE_EVENT, CONSTRAINT_VIOLATION,
)
TABLE_BY_NAME = {t.name: t for t in TABLES + (GROUND_TRUTH,)}
CORE_ENTITIES = (
    "Product", "Routing", "Operation", "Machine", "MachineCalendar", "Material",
    "InventoryLot", "ProductionOrder", "OperationSchedule", "ChangeoverMatrix",
)

# --------------------------------------------------------------------------
# Relationships (every foreign key)
# --------------------------------------------------------------------------
R = Relationship
RELATIONSHIPS: tuple[Relationship, ...] = (
    R("Product", "Routing", ("product_id",), ("product_id",), "1..N"),
    R("Product", "ProductionOrder", ("product_id",), ("product_id",), "0..N"),
    R("Routing", "RoutingStep", ("route_id",), ("route_id",), "1..N"),
    R("Routing", "ProductionOrder", ("route_id",), ("route_id",), "0..N"),
    R("RoutingStep", "RoutingStepMachine", ("route_id", "step_seq"), ("route_id", "step_seq"), "1..N"),
    R("Machine", "RoutingStepMachine", ("machine_id",), ("machine_id",), "0..N"),
    R("RoutingStep", "RoutingStepMaterial", ("route_id", "step_seq"), ("route_id", "step_seq"), "0..N"),
    R("Material", "RoutingStepMaterial", ("material_id",), ("material_id",), "0..N"),
    R("Machine", "MachineCalendar", ("machine_id",), ("machine_id",), "1..N"),
    R("Material", "InventoryLot", ("material_id",), ("material_id",), "0..N"),
    R("ProductionOrder", "Operation", ("order_id",), ("order_id",), "1..N"),
    R("RoutingStep", "Operation", ("route_id", "step_seq"), ("route_id", "step_seq"), "0..N"),
    R("Operation", "Operation", ("operation_id",), ("predecessor_operation_id",), "0..1", False),
    R("Operation", "MaterialRequirement", ("operation_id",), ("operation_id",), "0..N"),
    R("Material", "MaterialRequirement", ("material_id",), ("material_id",), "0..N"),
    R("InventoryLot", "MaterialRequirement", ("lot_id",), ("allocated_lot_id",), "0..N", False),
    R("Operation", "OperationSchedule", ("operation_id",), ("operation_id",), "0..N"),
    R("Machine", "OperationSchedule", ("machine_id",), ("machine_id",), "0..N"),
    R("MachineCalendar", "OperationSchedule", ("calendar_id",), ("calendar_id",), "0..N"),
    R("OperationSchedule", "OperationSchedule", ("schedule_id",), ("previous_schedule_id",), "0..1", False),
    R("OperationSchedule", "ScheduleEvent", ("schedule_id",), ("schedule_id",), "1..N"),
    R("ProductionOrder", "OrderStatusEvent", ("order_id",), ("order_id",), "1..N"),
    R("Operation", "ConstraintViolation", ("operation_id",), ("operation_id",), "0..N"),
    R("OperationSchedule", "ConstraintViolation", ("schedule_id",), ("schedule_id",), "0..N", False),
)

# Logical many-to-many relationships and the bridge table that resolves each
NM_RELATIONSHIPS = (
    ("RoutingStep", "Machine", "RoutingStepMachine"),
    ("RoutingStep", "Material", "RoutingStepMaterial"),
    ("Operation", "InventoryLot", "MaterialRequirement"),
)

# Allowed lifecycle transitions: (table, status column) -> {from: (to, ...)}
TRANSITIONS = {
    ("ProductionOrder", "order_status"): {
        "NEW": ("PLANNED", "CANCELLED"),
        "PLANNED": ("IN_PROGRESS", "CANCELLED"),
        "IN_PROGRESS": ("COMPLETED", "CANCELLED"),
        "COMPLETED": (),
        "CANCELLED": (),
    },
    ("OperationSchedule", "schedule_status"): {
        "PLANNED": ("SUPERSEDED", "CANCELLED"),
        "SUPERSEDED": (),
        "CANCELLED": (),
    },
}

DECISIONS = (
    ("D1", "All datetimes are ISO text in plant-local time (no time zones)."),
    ("D2", "An operation (changeover + run) must fit inside one shift row; no splitting."),
    ("D3", "A shift's usable window starts at shift_start and lasts available_minutes."),
    ("D4", "Labor = operators_assigned (calendar) versus operators_required (machine)."),
    ("D5", "Each material requirement is allocated to one inventory lot."),
    ("D6", "Shared tooling is a tool_group code on a routing step; same code cannot overlap."),
    ("D7", "Changeover lookup: machine group + (previous family, next family); first op on a machine = 0."),
    ("D8", "An unschedulable operation gets no OperationSchedule row, only a ConstraintViolation."),
    ("D9", "Makespan and lateness are computed in the metrics step, not stored."),
    ("D10", "An order is PLANNED only if all its operations are scheduled; otherwise it stays NEW and violations explain the gap."),
    ("D11", "An order is late if its last operation ends after the end of due_date (SOFT violation)."),
    ("D12", "In a batch run, NEW -> PLANNED events are stamped 2 hours after the order was received."),
    ("D13", "Dispatching is greedy by time (urgent first, then due date) with no backfilling."),
    ("D14", "Each injected case changes exactly one record; neighbouring records are excluded so labels stay unambiguous."),
    ("D15", "Boundary cases (margin 0) are valid rows labelled ACCEPT, used to measure false alarms."),
    ("D16", "MATERIAL_NOT_AVAILABLE is injected by moving the availability of a lot used by a single requirement."),
)


def foreign_keys(table_name: str) -> list[Relationship]:
    """All relationships where `table_name` is the child (holds the FK)."""
    return [r for r in RELATIONSHIPS if r.child == table_name]