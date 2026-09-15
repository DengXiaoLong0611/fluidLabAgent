from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Scenario(StrEnum):
    FIELD_GENERATION = "field_generation"
    PIV = "piv"
    FLOW_CONTROL = "flow_control"


class TaskRequest(BaseModel):
    scenario: Scenario
    objective: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


class TargetFieldSpec(BaseModel):
    mean_velocity: float = Field(ge=0)
    direction_deg: float = Field(ge=-360, le=360)
    turbulence_intensity: float = Field(ge=0, le=1)
    duration_s: float = Field(gt=0, le=3600)
    tolerance: float = Field(gt=0)
    region: str = "test_section"


class FanZone(BaseModel):
    zone_id: str
    fan_ids: list[int] = Field(min_length=1)
    min_command: float = 0
    max_command: float = 1
    max_delta: float = 0.1


class FlapAxis(BaseModel):
    axis_id: str
    min_angle_deg: float
    max_angle_deg: float
    max_rate_deg_s: float = Field(gt=0)


class FieldAction(BaseModel):
    fan_commands: dict[str, float]
    flap_angles_deg: dict[str, float] = Field(default_factory=dict)
    hold_s: float = Field(gt=0, le=3600)


class Observation(BaseModel):
    mean_velocity: float = 0
    direction_deg: float = 0
    turbulence_intensity: float = 0
    quality: Literal["good", "degraded", "invalid"] = "good"
    source: str = "simulator"
    raw_artifact: str | None = None


class OperationResult(BaseModel):
    operation_id: str
    status: Literal["accepted", "completed", "rejected", "failed"]
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
