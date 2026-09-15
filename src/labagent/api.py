import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .contracts import Scenario
from .platform import Platform


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    angle_deg: float = Field(default=0, ge=-30, le=30)
    rate_deg_s: float = Field(default=5, gt=0, le=10)
    duration_s: float = Field(default=2, gt=0, le=30)
    image_pairs: int = Field(default=10, ge=1, le=100)
    target_velocity: float = Field(default=5, ge=0, le=20)
    target_ti: float = Field(default=.1, ge=0, le=1)


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Scenario
    objective: str = Field(min_length=1, max_length=1000)
    mode: Literal["simulation", "bridge"] = "simulation"
    parameters: Parameters = Field(default_factory=Parameters)


class Completion(BaseModel):
    receipt: str
    status: Literal["completed", "failed"]
    result: dict


def create_app(url=None, run_scheduler=True):
    store = Platform(url or os.getenv("LAB_DATABASE_URL", "sqlite:///labagent.db"))

    @asynccontextmanager
    async def lifespan(app):
        async def scheduler():
            while True:
                await asyncio.to_thread(store.tick)
                await asyncio.sleep(.5)
        runner = asyncio.create_task(scheduler()) if run_scheduler else None
        yield
        if runner:
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Fluid Lab Agent", lifespan=lifespan)
    app.state.store = store

    def auth(authorization: str = Header(default="")):
        token = os.getenv("LAB_API_TOKEN", "")
        if token and not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Invalid API token")

    @app.get("/health")
    def health():
        return {"status": "ok", "engine": "langgraph"}

    @app.post("/api/tasks", dependencies=[Depends(auth)], status_code=201)
    def submit(body: Submission):
        return store.create(body.model_dump(mode="json"))

    @app.get("/api/tasks", dependencies=[Depends(auth)])
    def tasks():
        return sorted(store.list("task"), key=lambda t: t["created_at"], reverse=True)

    @app.get("/api/tasks/{key}", dependencies=[Depends(auth)])
    def task(key: str):
        value = store.read(key)
        if not value or "scenario" not in value:
            raise HTTPException(404, "Task not found")
        return value

    @app.post("/api/tasks/{key}/cancel", dependencies=[Depends(auth)])
    def cancel(key: str):
        try:
            return store.cancel(key)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/memories", dependencies=[Depends(auth)])
    def memories():
        return store.list("memory")

    @app.delete("/api/memories/{key}", dependencies=[Depends(auth)])
    def forget(key: str):
        with store.lock, store.engine.begin() as c:
            c.execute(store.rows.delete().where(store.rows.c.id == key, store.rows.c.kind == "memory"))
        return {"deleted": key}

    @app.post("/api/bridge/{kind}/claim", dependencies=[Depends(auth)])
    def claim(kind: Literal["device", "desktop"], worker: str = "bridge"):
        return store.claim(kind, worker)

    @app.post("/api/operations/{key}/complete", dependencies=[Depends(auth)])
    def complete(key: str, body: Completion):
        try:
            return store.complete(key, body.receipt, body.status, body.result)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")
    return app


app = create_app()
