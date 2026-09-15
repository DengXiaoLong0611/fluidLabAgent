"""Durable task orchestration. Each graph tick commits one recoverable transition."""
import os
import time
import uuid
from threading import RLock
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import (
    JSON,
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    select,
)

SCENARIOS = {"field_generation": "device", "piv": "desktop", "flow_control": "device"}


class Tick(TypedDict, total=False):
    task_id: str
    phase: str


class Platform:
    def __init__(self, url: str):
        self.engine = create_engine(url)
        self.lock = RLock()
        meta = MetaData()
        self.rows = Table("lab_records", meta, Column("id", String, primary_key=True),
                          Column("kind", String, index=True), Column("data", JSON))
        meta.create_all(self.engine)
        graph = StateGraph(Tick)
        graph.add_node("inspect", self.inspect)
        graph.add_node("plan", self.plan)
        graph.add_node("dispatch", self.dispatch)
        graph.add_node("evaluate", self.evaluate)
        graph.add_edge(START, "inspect")
        graph.add_conditional_edges("inspect", lambda s: s["phase"],
                                    {"queued": "plan", "planned": "dispatch", "waiting": "evaluate", "done": END})
        for node in ("plan", "dispatch", "evaluate"):
            graph.add_edge(node, END)
        self.graph = graph.compile()

    def read(self, key):
        with self.engine.connect() as c:
            row = c.execute(select(self.rows.c.data).where(self.rows.c.id == key)).first()
            return row[0] if row else None

    def list(self, kind):
        with self.engine.connect() as c:
            return [r[0] for r in c.execute(select(self.rows.c.data).where(self.rows.c.kind == kind))]

    def put(self, c, kind, data):
        found = c.execute(select(self.rows.c.id).where(self.rows.c.id == data["id"])).first()
        if found:
            c.execute(self.rows.update().where(self.rows.c.id == data["id"]).values(data=data))
        else:
            c.execute(self.rows.insert().values(id=data["id"], kind=kind, data=data))

    def save(self, kind, data):
        with self.lock, self.engine.begin() as c:
            self.put(c, kind, data)

    def create(self, request):
        task = {"id": str(uuid.uuid4()), **request, "status": "queued", "created_at": time.time(),
                "events": [{"stage": "queued", "at": time.time()}]}
        self.save("task", task)
        return task

    def transition(self, task, status):
        task["status"] = status
        task["events"].append({"stage": status, "at": time.time()})

    def inspect(self, state):
        task = self.read(state["task_id"])
        return {"phase": task["status"] if task["status"] in ("queued", "planned", "waiting") else "done"}

    def plan(self, state):
        task = self.read(state["task_id"])
        p = task["parameters"]
        templates = {"field_generation": "flap.set", "piv": "davis.capture", "flow_control": "flow.set"}
        task["plan"] = {"tool": templates[task["scenario"]], "parameters": p,
                        "reason": "Validated scenario template", "planner": "deterministic"}
        if os.getenv("LAB_USE_MODEL") == "1":
            from .specialist import advise
            task["plan"]["advice"] = advise(task, self.list("memory")[-5:])
        self.transition(task, "planned")
        self.save("task", task)
        return {}

    def dispatch(self, state):
        task = self.read(state["task_id"])
        op = {"id": task["id"] + ":operation", "task_id": task["id"],
              "kind": SCENARIOS[task["scenario"]], "status": "queued", "mode": task["mode"],
              "tool": task["plan"]["tool"], "parameters": task["parameters"], "created_at": time.time()}
        task["operation_id"] = op["id"]
        self.transition(task, "waiting")
        with self.engine.begin() as c:
            self.put(c, "operation", op)
            self.put(c, "task", task)
        return {}

    def evaluate(self, state):
        task = self.read(state["task_id"])
        op = self.read(task["operation_id"])
        if op["status"] == "queued" and op["mode"] == "simulation":
            from .bridge import simulate
            op.update(status="completed", result=simulate(op))
            self.save("operation", op)
        if op["status"] == "running" and op["deadline"] < time.time():
            op.update(status="unknown", error="Bridge timed out; reconcile physical state before another run")
            self.save("operation", op)
        if op["status"] in ("completed", "failed", "unknown"):
            task["result"] = op.get("result", {"error": op.get("error")})
            self.transition(task, op["status"])
            with self.engine.begin() as c:
                self.put(c, "task", task)
                self.put(c, "memory", {"id": task["id"] + ":memory", "task_id": task["id"],
                    "scenario": task["scenario"], "objective": task["objective"], "mode": task["mode"],
                    "result": task["result"], "status": task["status"], "approved": False})
        return {}

    def tick(self):
        with self.lock:
            for task in self.list("task"):
                if task["status"] in ("queued", "planned", "waiting"):
                    try:
                        self.graph.invoke({"task_id": task["id"]})
                    # The scheduler must persist an unexpected node failure instead of dying.
                    except Exception as exc:  # noqa: BLE001
                        task = self.read(task["id"])
                        task["result"] = {"error": type(exc).__name__, "message": str(exc)[:300]}
                        self.transition(task, "failed")
                        self.save("task", task)

    def claim(self, kind, worker):
        with self.lock, self.engine.begin() as c:
            rows = c.execute(select(self.rows.c.data).where(self.rows.c.kind == "operation").with_for_update()).all()
            ops = [r[0] for r in rows]
            # One physical execution at a time per resource kind; unknown blocks reuse.
            if any(o["kind"] == kind and o["status"] in ("running", "unknown") for o in ops):
                return None
            for op in ops:
                if op["kind"] == kind and op["mode"] == "bridge" and op["status"] == "queued":
                    op.update(status="running", worker=worker, receipt=str(uuid.uuid4()), deadline=time.time() + 120)
                    self.put(c, "operation", op)
                    return op
        return None

    def complete(self, key, receipt, status, result):
        with self.lock, self.engine.begin() as c:
            row = c.execute(select(self.rows.c.data).where(self.rows.c.id == key).with_for_update()).first()
            if not row:
                raise ValueError("Operation not found")
            op = row[0]
            if op.get("receipt") != receipt:
                raise ValueError("Receipt mismatch")
            if op["status"] in ("completed", "failed"):
                return op
            if op["status"] != "running":
                raise ValueError("Operation requires reconciliation")
            op.update(status=status, result=result)
            self.put(c, "operation", op)
            return op

    def cancel(self, key):
        with self.lock:
            task = self.read(key)
            if not task:
                raise ValueError("Task not found")
            op = self.read(task.get("operation_id", ""))
            if task["status"] not in ("queued", "planned", "waiting") or (op and op["status"] != "queued"):
                raise ValueError("Already executing or terminal; cancellation cannot stop physical hardware")
            self.transition(task, "cancelled")
            with self.engine.begin() as c:
                self.put(c, "task", task)
                if op:
                    op["status"] = "cancelled"
                    self.put(c, "operation", op)
            return task
