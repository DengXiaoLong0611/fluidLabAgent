"""Durable task orchestration. Each graph tick commits one recoverable transition."""
import os
import time
import uuid
from threading import RLock
from typing import TypedDict

import httpx
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

from .model_advisor import consult

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
        now = time.time()
        task = {"id": str(uuid.uuid4()), **request, "status": "queued", "control_state": "running",
                "hardware_stopped": False, "created_at": now, "events": [{"stage": "queued", "at": now}],
                "interventions": [], "node_runs": []}
        self.save("task", task)
        self.log("task.created", "info", task_id=task["id"], details={"scenario": task["scenario"]})
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
        settings = self.model_settings()
        if settings["provider"] != "disabled":
            try:
                advice = consult(task, self.list("memory"), settings)
                task["plan"]["advice"] = advice.content
                task["plan"]["adviser"] = {"provider": settings["provider"], "model": settings["model"]}
                self.record_usage(settings["provider"], settings["model"], advice.input_tokens,
                                  advice.output_tokens, advice.cost_usd, advice.latency_ms)
                self.log("model.advice.completed", "info", task_id=task["id"],
                         details={"provider": settings["provider"], "model": settings["model"],
                                  "latency_ms": advice.latency_ms})
            except (httpx.HTTPError, ConnectionError, KeyError, TypeError, ValueError) as exc:
                # The deterministic plan remains safe and usable.
                self.log("model.advice.failed", "warning", task_id=task["id"],
                         details={"provider": settings["provider"], "model": settings["model"],
                                  "error": str(exc)[:300]})
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
                if task.get("control_state", "running") != "running":
                    continue
                if task["status"] in ("queued", "planned", "waiting"):
                    node = {"queued": "plan", "planned": "dispatch", "waiting": "evaluate"}[task["status"]]
                    started = time.time()
                    try:
                        self.graph.invoke({"task_id": task["id"]})
                        updated = self.read(task["id"])
                        ended = time.time()
                        run_status = "waiting" if node == "evaluate" and updated["status"] == "waiting" else "completed"
                        prior_runs = updated.setdefault("node_runs", [])
                        duplicate_wait = (run_status == "waiting" and prior_runs
                                          and prior_runs[-1]["node"] == node
                                          and prior_runs[-1]["status"] == "waiting")
                        if not duplicate_wait:
                            prior_runs.append({"id": str(uuid.uuid4()), "node": node,
                                "status": run_status, "started_at": started, "ended_at": ended,
                                "duration_ms": round((ended - started) * 1000, 3),
                                "input": {"task_status": task["status"]},
                                "output": {"task_status": updated["status"]}})
                            self.save("task", updated)
                            self.log("graph.node." + run_status, "info", task_id=task["id"],
                                     details={"node": node, "duration_ms": round((ended - started) * 1000, 3)})
                    # The scheduler must persist an unexpected node failure instead of dying.
                    except Exception as exc:  # noqa: BLE001
                        task = self.read(task["id"])
                        task["result"] = {"error": type(exc).__name__, "message": str(exc)[:300]}
                        ended = time.time()
                        task.setdefault("node_runs", []).append({"id": str(uuid.uuid4()), "node": node,
                            "status": "failed", "started_at": started, "ended_at": ended,
                            "duration_ms": round((ended - started) * 1000, 3),
                            "input": {"task_status": task["status"]}, "error": str(exc)[:300]})
                        self.transition(task, "failed")
                        self.save("task", task)
                        self.log("graph.node.failed", "error", task_id=task["id"],
                                 details={"node": node, "error": str(exc)[:300]})

    def claim(self, kind, worker):
        with self.lock, self.engine.begin() as c:
            rows = c.execute(select(self.rows.c.data).where(self.rows.c.kind == "operation").with_for_update()).all()
            ops = [r[0] for r in rows]
            # Emergency commands bypass the normal one-operation resource lock.
            for op in sorted(ops, key=lambda item: item.get("created_at", 0)):
                if (kind == "device" and op.get("priority") == "critical"
                        and op["status"] == "queued" and op["mode"] == "bridge"):
                    op.update(status="running", worker=worker, receipt=str(uuid.uuid4()), deadline=time.time() + 15)
                    self.put(c, "operation", op)
                    return op
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

    def pause(self, key):
        with self.lock:
            task = self.read(key)
            if not task:
                raise ValueError("Task not found")
            if task["status"] not in ("queued", "planned", "waiting"):
                raise ValueError("Only an active task can be paused")
            task["control_state"] = "paused"
            task["events"].append({"stage": "paused", "at": time.time()})
            self.save("task", task)
            self.log("task.paused", "warning", task_id=key)
            return task

    def resume(self, key, message=""):
        with self.lock:
            task = self.read(key)
            if not task:
                raise ValueError("Task not found")
            if task.get("control_state") != "paused":
                raise ValueError("Task is not paused")
            if message.strip():
                task.setdefault("interventions", []).append({"message": message.strip(), "at": time.time()})
            task["control_state"] = "running"
            task["events"].append({"stage": "resumed", "at": time.time()})
            self.save("task", task)
            self.log("task.resumed", "info", task_id=key, details={"message": message.strip()[:300]})
            return task

    def stop_agent(self, key):
        with self.lock:
            task = self.read(key)
            if not task:
                raise ValueError("Task not found")
            if task["status"] not in ("queued", "planned", "waiting"):
                raise ValueError("Task is already terminal")
            op = self.read(task.get("operation_id", ""))
            if op and op["status"] == "queued":
                op["status"] = "cancelled"
                self.save("operation", op)
            task["control_state"] = "stopped"
            task["hardware_stopped"] = False
            self.transition(task, "stopped")
            self.save("task", task)
            self.log("agent.stopped", "warning", task_id=key)
            return task

    def emergency_stop(self, target, reason):
        now = time.time()
        op = {"id": "emergency:" + str(uuid.uuid4()), "task_id": None, "kind": "device",
              "status": "queued", "mode": "bridge", "tool": "system.emergency_stop",
              "priority": "critical", "parameters": {"target": target, "reason": reason},
              "created_at": now}
        self.save("operation", op)
        self.log("device.emergency_stop.requested", "critical", details=op["parameters"])
        return op

    def heartbeat(self, component, details=None):
        row = {"id": "heartbeat:" + component, "component": component, "status": "ok",
               "at": time.time(), "details": details or {}}
        self.save("heartbeat", row)
        return row

    def record_usage(self, provider, model, input_tokens, output_tokens, cost_usd, latency_ms):
        row = {"id": "usage:" + str(uuid.uuid4()), "provider": provider, "model": model,
               "input_tokens": input_tokens, "output_tokens": output_tokens,
               "estimated_cost_usd": cost_usd, "latency_ms": latency_ms, "at": time.time()}
        self.save("usage", row)
        return row

    def model_settings(self):
        return self.read("settings:model") or {"id": "settings:model", "provider": "disabled",
            "model": "deterministic-planner", "temperature": 0, "max_tokens": 2048}

    def update_model_settings(self, settings):
        row = {"id": "settings:model", **settings}
        self.save("settings", row)
        self.log("model.settings.updated", "info", details={"provider": row["provider"], "model": row["model"]})
        return row

    def log(self, event, level="info", task_id=None, details=None):
        row = {"id": "log:" + str(uuid.uuid4()), "event": event, "level": level,
               "task_id": task_id, "details": details or {}, "at": time.time()}
        self.save("log", row)
        return row

    def logs(self, limit=100):
        return sorted(self.list("log"), key=lambda row: row["at"], reverse=True)[:limit]

    def dashboard(self):
        heartbeats = {row["component"]: row for row in self.list("heartbeat")}
        components = {name: {"status": "unknown", "message": "尚未收到心跳"}
                      for name in ("device_gateway", "arduino", "rpa")}
        components.update({name: {"status": row["status"] if time.time() - row["at"] <= 5 else "stale",
                                  "last_seen": row["at"],
                                  "details": row["details"]} for name, row in heartbeats.items()})
        components["fastapi"] = {"status": "ok"}
        components["database"] = {"status": "ok", "url": self.engine.url.drivername}
        components["langgraph"] = {"status": "ok"}
        usage = self.list("usage")
        total_input = sum(row["input_tokens"] for row in usage)
        total_output = sum(row["output_tokens"] for row in usage)
        model = self.model_settings()
        model = {key: value for key, value in model.items() if key != "id"}
        provider_env = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                        "google": "GOOGLE_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
        model["api_key_configured"] = (model["provider"] == "ollama" or bool(
            os.getenv(provider_env.get(model["provider"], ""))
        ))
        tasks = self.list("task")
        stops = sorted((op for op in self.list("operation") if op.get("tool") == "system.emergency_stop"),
                       key=lambda op: op["created_at"], reverse=True)[:10]
        return {"components": components, "model": model,
                "tasks": {"total": len(tasks), "active": sum(t["status"] in ("queued", "planned", "waiting") for t in tasks),
                          "paused": sum(t.get("control_state") == "paused" for t in tasks)},
                "usage": {"calls": len(usage), "input_tokens": total_input, "output_tokens": total_output,
                          "total_tokens": total_input + total_output,
                          "estimated_cost_usd": round(sum(row["estimated_cost_usd"] for row in usage), 6),
                          "average_latency_ms": round(sum(row["latency_ms"] for row in usage) / len(usage), 1) if usage else 0},
                "emergency_stops": stops}
