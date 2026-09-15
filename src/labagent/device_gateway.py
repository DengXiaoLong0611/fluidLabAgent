from abc import ABC, abstractmethod
from uuid import uuid4

from .contracts import FieldAction, Observation, OperationResult


class DeviceGateway(ABC):
    @abstractmethod
    def apply_field_action(self, action: FieldAction) -> OperationResult: ...

    @abstractmethod
    def observe(self) -> Observation: ...


class SimulatedDeviceGateway(DeviceGateway):
    """Deterministic simulator used before Arduino/driver integration."""
    def __init__(self) -> None:
        self.last_action: FieldAction | None = None

    def apply_field_action(self, action: FieldAction) -> OperationResult:
        self.last_action = action
        return OperationResult(operation_id=str(uuid4()), status="completed",
                               message="simulated action applied")

    def observe(self) -> Observation:
        action = self.last_action
        if not action:
            return Observation(quality="degraded")
        values = list(action.fan_commands.values())
        mean = (sum(values) / len(values) * 20) if values else 0
        turbulence = min(1.0, (sum(abs(v - 0.5) for v in values) / len(values)) if values else 0)
        return Observation(mean_velocity=mean, turbulence_intensity=turbulence)
