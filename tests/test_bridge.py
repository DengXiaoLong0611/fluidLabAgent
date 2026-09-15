from labagent.bridge import serial_command, simulate


def test_serial_emergency_stop_uses_stop_not_move():
    command = serial_command({
        "id": "emergency:1",
        "tool": "system.emergency_stop",
        "parameters": {"target": "all", "reason": "operator request"},
    })
    assert command == {
        "v": 1,
        "id": "emergency:1",
        "command": "STOP",
        "target": "all",
        "reason": "operator request",
    }


def test_serial_motion_command_is_validated():
    command = serial_command({
        "id": "task:operation",
        "tool": "flap.set",
        "parameters": {"angle_deg": 5, "rate_deg_s": 2},
    })
    assert command["command"] == "MOVE"
    assert command["angle_deg"] == 5
    assert command["rate_deg_s"] == 2


def test_simulated_emergency_stop_never_claims_hardware_stopped():
    result = simulate({"tool": "system.emergency_stop", "parameters": {"target": "all"}})
    assert result["simulated"] is True
    assert result["hardware_stopped"] is False
