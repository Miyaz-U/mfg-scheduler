-- Manufacturing scheduling schema (generated from src/mfgsched/schema.py).
-- Load with:  sqlite3 mfg.db < schema.sql
PRAGMA foreign_keys = ON;

-- A sellable item (one row per SKU).
CREATE TABLE Product (
  product_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  product_name TEXT NOT NULL,
  product_family TEXT NOT NULL CHECK (product_family IN ('FAM-A', 'FAM-B', 'FAM-C', 'FAM-D', 'FAM-E', 'FAM-F')),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('ACTIVE', 'OBSOLETE')),
  PRIMARY KEY (product_id),
  UNIQUE (sku)
);

-- A versioned recipe (ordered steps) for making a product.
CREATE TABLE Routing (
  route_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  route_version INTEGER NOT NULL CHECK (route_version >= 1),
  effective_from TEXT NOT NULL,
  effective_to TEXT,
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('DRAFT', 'ACTIVE', 'RETIRED')),
  PRIMARY KEY (route_id),
  FOREIGN KEY (product_id) REFERENCES Product (product_id)
);

-- One ordered step inside a routing (the template).
CREATE TABLE RoutingStep (
  route_id TEXT NOT NULL,
  step_seq INTEGER NOT NULL CHECK (step_seq >= 1),
  op_type TEXT NOT NULL CHECK (op_type IN ('CUT', 'MILL', 'DRILL', 'WELD', 'PAINT', 'ASSEMBLE', 'TEST')),
  run_minutes_per_unit REAL NOT NULL CHECK (run_minutes_per_unit >= 0.1),
  fixed_minutes REAL NOT NULL CHECK (fixed_minutes >= 0),
  tool_group TEXT,
  PRIMARY KEY (route_id, step_seq),
  FOREIGN KEY (route_id) REFERENCES Routing (route_id)
);

-- A production machine belonging to one machine group.
CREATE TABLE Machine (
  machine_id TEXT NOT NULL,
  machine_name TEXT NOT NULL,
  machine_group TEXT NOT NULL CHECK (machine_group IN ('CUT', 'MILL', 'DRILL', 'WELD', 'PAINT', 'ASSEMBLE', 'TEST')),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('ACTIVE', 'MAINTENANCE', 'RETIRED')),
  operators_required INTEGER NOT NULL CHECK (operators_required >= 1) CHECK (operators_required <= 3),
  speed_factor REAL NOT NULL CHECK (speed_factor >= 0.5) CHECK (speed_factor <= 1.5),
  commissioned_on TEXT NOT NULL,
  effective_to TEXT,
  PRIMARY KEY (machine_id)
);

-- N:M bridge: which machines may run which step.
CREATE TABLE RoutingStepMachine (
  route_id TEXT NOT NULL,
  step_seq INTEGER NOT NULL CHECK (step_seq >= 1),
  machine_id TEXT NOT NULL,
  is_preferred INTEGER NOT NULL,
  PRIMARY KEY (route_id, step_seq, machine_id),
  FOREIGN KEY (route_id, step_seq) REFERENCES RoutingStep (route_id, step_seq),
  FOREIGN KEY (machine_id) REFERENCES Machine (machine_id)
);

-- A raw material or purchased component.
CREATE TABLE Material (
  material_id TEXT NOT NULL,
  material_name TEXT NOT NULL,
  material_category TEXT NOT NULL CHECK (material_category IN ('STEEL', 'ALUMINUM', 'PLASTIC', 'FASTENER', 'COATING', 'ELECTRONICS', 'PACKAGING')),
  unit_of_measure TEXT NOT NULL CHECK (unit_of_measure IN ('KG', 'EA', 'L', 'M')),
  standard_lead_time_days INTEGER NOT NULL CHECK (standard_lead_time_days >= 1) CHECK (standard_lead_time_days <= 60),
  PRIMARY KEY (material_id)
);

-- N:M bridge: material needed per unit for a step.
CREATE TABLE RoutingStepMaterial (
  route_id TEXT NOT NULL,
  step_seq INTEGER NOT NULL CHECK (step_seq >= 1),
  material_id TEXT NOT NULL,
  qty_per_unit REAL NOT NULL CHECK (qty_per_unit >= 0.001),
  PRIMARY KEY (route_id, step_seq, material_id),
  FOREIGN KEY (route_id, step_seq) REFERENCES RoutingStep (route_id, step_seq),
  FOREIGN KEY (material_id) REFERENCES Material (material_id)
);

-- A received (or expected) batch of one material.
CREATE TABLE InventoryLot (
  lot_id TEXT NOT NULL,
  material_id TEXT NOT NULL,
  quantity_received REAL NOT NULL CHECK (quantity_received >= 0.001),
  expected_available_at TEXT NOT NULL,
  available_at TEXT NOT NULL,
  delay_days INTEGER NOT NULL CHECK (delay_days >= 0),
  is_delayed INTEGER NOT NULL,
  quantity_allocated REAL NOT NULL CHECK (quantity_allocated >= 0),
  quantity_remaining REAL NOT NULL CHECK (quantity_remaining >= 0),
  lot_status TEXT NOT NULL CHECK (lot_status IN ('IN_TRANSIT', 'AVAILABLE', 'EXHAUSTED')),
  PRIMARY KEY (lot_id),
  FOREIGN KEY (material_id) REFERENCES Material (material_id)
);

-- One row per machine, day and shift: capacity, downtime and staffing.
CREATE TABLE MachineCalendar (
  calendar_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  calendar_date TEXT NOT NULL,
  shift_code TEXT NOT NULL CHECK (shift_code IN ('A', 'B', 'C')),
  shift_start TEXT NOT NULL,
  shift_end TEXT NOT NULL,
  planned_break_minutes INTEGER NOT NULL CHECK (planned_break_minutes >= 0),
  downtime_minutes INTEGER NOT NULL CHECK (downtime_minutes >= 0),
  downtime_reason TEXT NOT NULL CHECK (downtime_reason IN ('NONE', 'PLANNED_MAINTENANCE', 'BREAKDOWN')),
  operators_assigned INTEGER NOT NULL CHECK (operators_assigned >= 0) CHECK (operators_assigned <= 6),
  available_minutes INTEGER NOT NULL CHECK (available_minutes >= 0),
  PRIMARY KEY (calendar_id),
  UNIQUE (machine_id, calendar_date, shift_code),
  FOREIGN KEY (machine_id) REFERENCES Machine (machine_id)
);

-- Sequence-dependent setup time when a machine group switches product family.
CREATE TABLE ChangeoverMatrix (
  machine_group TEXT NOT NULL CHECK (machine_group IN ('CUT', 'MILL', 'DRILL', 'WELD', 'PAINT', 'ASSEMBLE', 'TEST')),
  from_family TEXT NOT NULL CHECK (from_family IN ('FAM-A', 'FAM-B', 'FAM-C', 'FAM-D', 'FAM-E', 'FAM-F')),
  to_family TEXT NOT NULL CHECK (to_family IN ('FAM-A', 'FAM-B', 'FAM-C', 'FAM-D', 'FAM-E', 'FAM-F')),
  changeover_minutes INTEGER NOT NULL CHECK (changeover_minutes >= 0) CHECK (changeover_minutes <= 240),
  PRIMARY KEY (machine_group, from_family, to_family)
);

-- A customer/plant order to make a quantity of a product.
CREATE TABLE ProductionOrder (
  order_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  route_id TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity >= 1) CHECK (quantity <= 5000),
  release_date TEXT NOT NULL,
  due_date TEXT NOT NULL,
  priority INTEGER NOT NULL CHECK (priority IN ('1', '2', '3', '4')),
  is_urgent INTEGER NOT NULL,
  order_status TEXT NOT NULL CHECK (order_status IN ('NEW', 'PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
  PRIMARY KEY (order_id),
  FOREIGN KEY (product_id) REFERENCES Product (product_id),
  FOREIGN KEY (route_id) REFERENCES Routing (route_id)
);

-- One step of one order (the order-level instance of a RoutingStep).
CREATE TABLE Operation (
  operation_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  route_id TEXT NOT NULL,
  step_seq INTEGER NOT NULL CHECK (step_seq >= 1),
  predecessor_operation_id TEXT,
  standard_minutes REAL NOT NULL CHECK (standard_minutes >= 0.1),
  operation_status TEXT NOT NULL CHECK (operation_status IN ('PENDING', 'SCHEDULED', 'IN_PROGRESS', 'DONE', 'BLOCKED')),
  PRIMARY KEY (operation_id),
  FOREIGN KEY (order_id) REFERENCES ProductionOrder (order_id),
  FOREIGN KEY (route_id, step_seq) REFERENCES RoutingStep (route_id, step_seq),
  FOREIGN KEY (predecessor_operation_id) REFERENCES Operation (operation_id)
);

-- Material an operation needs, and the lot reserved for it (D5).
CREATE TABLE MaterialRequirement (
  requirement_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  material_id TEXT NOT NULL,
  allocated_lot_id TEXT,
  required_qty REAL NOT NULL CHECK (required_qty >= 0.001),
  PRIMARY KEY (requirement_id),
  UNIQUE (operation_id, material_id),
  FOREIGN KEY (operation_id) REFERENCES Operation (operation_id),
  FOREIGN KEY (material_id) REFERENCES Material (material_id),
  FOREIGN KEY (allocated_lot_id) REFERENCES InventoryLot (lot_id)
);

-- A planned time slot for one operation on one machine.
CREATE TABLE OperationSchedule (
  schedule_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  calendar_id TEXT NOT NULL,
  planned_start TEXT NOT NULL,
  planned_end TEXT NOT NULL,
  previous_schedule_id TEXT,
  changeover_minutes INTEGER NOT NULL CHECK (changeover_minutes >= 0),
  run_minutes INTEGER NOT NULL CHECK (run_minutes >= 1),
  schedule_version INTEGER NOT NULL CHECK (schedule_version >= 1),
  schedule_status TEXT NOT NULL CHECK (schedule_status IN ('PLANNED', 'SUPERSEDED', 'CANCELLED')),
  PRIMARY KEY (schedule_id),
  FOREIGN KEY (operation_id) REFERENCES Operation (operation_id),
  FOREIGN KEY (machine_id) REFERENCES Machine (machine_id),
  FOREIGN KEY (calendar_id) REFERENCES MachineCalendar (calendar_id),
  FOREIGN KEY (previous_schedule_id) REFERENCES OperationSchedule (schedule_id)
);

-- Append-only history of order status changes.
CREATE TABLE OrderStatusEvent (
  event_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  from_status TEXT CHECK (from_status IN ('NEW', 'PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
  to_status TEXT NOT NULL CHECK (to_status IN ('NEW', 'PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
  event_ts TEXT NOT NULL,
  reason TEXT,
  PRIMARY KEY (event_id),
  FOREIGN KEY (order_id) REFERENCES ProductionOrder (order_id)
);

-- Append-only history of schedule status changes.
CREATE TABLE ScheduleEvent (
  event_id TEXT NOT NULL,
  schedule_id TEXT NOT NULL,
  from_status TEXT CHECK (from_status IN ('PLANNED', 'SUPERSEDED', 'CANCELLED')),
  to_status TEXT NOT NULL CHECK (to_status IN ('PLANNED', 'SUPERSEDED', 'CANCELLED')),
  event_ts TEXT NOT NULL,
  reason TEXT,
  PRIMARY KEY (event_id),
  FOREIGN KEY (schedule_id) REFERENCES OperationSchedule (schedule_id)
);

-- Recorded constraint violations and explained infeasibility (D8).
CREATE TABLE ConstraintViolation (
  violation_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  schedule_id TEXT,
  violation_type TEXT NOT NULL CHECK (violation_type IN ('MACHINE_OVERLAP', 'PRECEDENCE_VIOLATED', 'MATERIAL_NOT_AVAILABLE', 'OUTSIDE_SHIFT_CALENDAR', 'CHANGEOVER_OMITTED', 'UNSTAFFED_SHIFT', 'SHARED_TOOL_CONFLICT', 'MACHINE_NOT_ELIGIBLE', 'NO_CAPACITY_IN_HORIZON', 'PREDECESSOR_UNSCHEDULED', 'LATE_DELIVERY')),
  severity TEXT NOT NULL CHECK (severity IN ('HARD', 'SOFT')),
  detected_at TEXT NOT NULL,
  details TEXT NOT NULL,
  PRIMARY KEY (violation_id),
  FOREIGN KEY (operation_id) REFERENCES Operation (operation_id),
  FOREIGN KEY (schedule_id) REFERENCES OperationSchedule (schedule_id)
);
