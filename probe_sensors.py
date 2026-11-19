"""One-off probe: dump every LibreHardwareMonitor sensor to JSON."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
LHM = os.path.join(ROOT, "vendor", "lhm")
sys.path.insert(0, LHM)
os.add_dll_directory(LHM)

import clr  # noqa: E402

clr.AddReference(os.path.join(LHM, "LibreHardwareMonitorLib.dll"))

from LibreHardwareMonitor.Hardware import Computer  # noqa: E402


def dump(hw, depth=0):
    hw.Update()
    node = {
        "type": str(hw.HardwareType),
        "name": hw.Name,
        "depth": depth,
        "sensors": [],
        "children": [],
    }
    for s in hw.Sensors:
        value = s.Value
        node["sensors"].append(
            {
                "type": str(s.SensorType),
                "name": s.Name,
                "index": int(s.Index),
                "value": None if value is None else float(value),
            }
        )
    for sub in hw.SubHardware:
        node["children"].append(dump(sub, depth + 1))
    return node


def main():
    computer = Computer()
    computer.IsCpuEnabled = True
    computer.IsMotherboardEnabled = True
    computer.IsGpuEnabled = False
    computer.Open()
    result = {"ok": True, "hardware": []}
    try:
        for hw in computer.Hardware:
            result["hardware"].append(dump(hw))
    finally:
        computer.Close()
    out = os.path.join(ROOT, "probe_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("WROTE", out)
    for hw in result["hardware"]:
        print(f"{hw['type']}: {hw['name']}")
        for s in hw["sensors"]:
            print(f"   {s['type']:12} {s['name']:28} {s['value']}")
        for sub in hw["children"]:
            print(f"   -> {sub['type']}: {sub['name']}")
            for s in sub["sensors"]:
                print(f"      {s['type']:12} {s['name']:28} {s['value']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("ERROR:", type(exc).__name__, exc)
        with open(os.path.join(ROOT, "probe_error.txt"), "w", encoding="utf-8") as f:
            import traceback

            f.write(traceback.format_exc())
        sys.exit(1)
