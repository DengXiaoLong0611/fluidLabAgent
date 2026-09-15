"""Polling worker; adapters implement a documented lab-owned protocol."""
import argparse
import json
import os
import time

import httpx


def simulate(op):
    p = op["parameters"]
    if op["tool"] == "system.emergency_stop":
        return {"simulated": True, "hardware_stopped": False, "target": p["target"],
                "message": "STOP was simulated; no physical device was stopped"}
    if op["tool"] == "davis.capture":
        return {"simulated": True, "image_pairs": p["image_pairs"], "captured": False,
                "message": "Capture workflow simulated; no camera or laser triggered"}
    return {"simulated": True, "angle_deg": p["angle_deg"], "samples": [
        {"t": i, "velocity": round(5 + p["angle_deg"] * .02 + .1 * (i % 3 - 1), 3)} for i in range(20)],
        "message": "Illustrative signal, not a calibrated aerodynamic model", "fan_count_assumption": 600}


def serial_command(op):
    if op["tool"] == "system.emergency_stop":
        return {"v": 1, "id": op["id"], "command": "STOP",
                "target": op["parameters"]["target"], "reason": op["parameters"]["reason"]}
    from .api import Parameters
    p = Parameters.model_validate(op["parameters"])
    if op["tool"] not in ("flap.set", "flow.set"):
        raise ValueError("Unsupported serial tool")
    return {"v": 1, "id": op["id"], "command": "MOVE",
            "angle_deg": p.angle_deg, "rate_deg_s": p.rate_deg_s}


def serial_execute(op, port):
    import serial

    command = serial_command(op)
    with serial.Serial(port, 115200, timeout=1, write_timeout=2) as device:
        time.sleep(2)
        device.reset_input_buffer()
        device.write((json.dumps(command) + "\n").encode())
        deadline = time.monotonic() + (8 if command["command"] == "STOP" else 40)
        while time.monotonic() < deadline:
            raw = device.readline()
            if not raw:
                continue
            reply = json.loads(raw)
            if reply.get("id") != op["id"]:
                continue
            if reply.get("status") == "DONE":
                return reply
            if reply.get("status") == "ERROR":
                raise ValueError(reply.get("message", "Device rejected command"))
        raise TimeoutError("No DONE received; physical outcome unknown")


def rpa_execute(op, endpoint):
    if op["tool"] != "davis.capture":
        raise ValueError("RPA template not allowed")
    response = httpx.post(endpoint, json={"operation_id": op["id"], "template": "davis_capture_v1",
        "parameters": op["parameters"]}, timeout=60,
        headers={"Authorization": "Bearer " + os.getenv("LAB_RPA_TOKEN", "")})
    response.raise_for_status()
    result = response.json()
    if result.get("operation_id") != op["id"] or result.get("status") != "completed":
        raise TimeoutError("RPA completion not confirmed")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--kind", choices=["device", "desktop"], required=True)
    parser.add_argument("--adapter", choices=["simulation", "serial", "rpa"], default="simulation")
    parser.add_argument("--port")
    parser.add_argument("--endpoint")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.adapter == "serial" and (args.kind != "device" or not args.port):
        parser.error("serial requires --kind device and --port")
    if args.adapter == "rpa" and (args.kind != "desktop" or not args.endpoint):
        parser.error("rpa requires --kind desktop and --endpoint")
    with httpx.Client(base_url=args.url, timeout=10, headers={"Authorization": "Bearer " + os.getenv("LAB_API_TOKEN", "")}) as client:
        while True:
            component = "rpa" if args.adapter == "rpa" else "arduino" if args.adapter == "serial" else "device_gateway"
            client.post(f"/api/heartbeat/{component}", json={"details": {
                "kind": args.kind, "adapter": args.adapter, "port": args.port, "endpoint": args.endpoint}}).raise_for_status()
            response = client.post(f"/api/bridge/{args.kind}/claim", params={"worker": args.adapter})
            response.raise_for_status()
            op = response.json()
            if op:
                try:
                    result = serial_execute(op, args.port) if args.adapter == "serial" else rpa_execute(op, args.endpoint) if args.adapter == "rpa" else simulate(op)
                    status = "completed"
                except Exception as exc:  # noqa: BLE001
                    status = "failed"
                    result = {"error": type(exc).__name__, "message": str(exc)[:300]}
                response = client.post(f"/api/operations/{op['id']}/complete", json={
                    "receipt": op["receipt"], "status": status, "result": result})
                response.raise_for_status()
                print(json.dumps({"id": op["id"], "status": status}))
            if args.once:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
