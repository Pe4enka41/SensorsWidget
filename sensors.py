"""Sensor worker: LibreHardwareMonitor (CPU, fans) + NVML (GPU)."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QThread, Signal

ROOT = os.path.dirname(os.path.abspath(__file__))
LHM_DIR = os.path.join(ROOT, "vendor", "lhm")


def _unblock(folder: str) -> None:
    """Drop the "downloaded from the internet" mark so .NET can load the DLLs."""
    try:
        import ctypes

        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                ctypes.windll.kernel32.DeleteFileW(
                    ctypes.c_wchar_p(path + ":Zone.Identifier")
                )
    except Exception:
        pass


def _num(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


class SensorWorker(QThread):
    sample = Signal(dict)
    inventory = Signal(list)
    failed = Signal(str)
    ready = Signal()

    def __init__(self, fan_name: str = "CPU Fan", interval_ms: int = 1000):
        super().__init__()
        self.fan_name = fan_name
        self.interval_ms = interval_ms
        self._stop = False
        self._computer = None
        self._lhm_error = None
        self._nvml = None
        self._gpu_handle = None
        self._gpu_name = "GPU"
        self._last_inventory = None

    # ------------------------------------------------------------------ setup
    def _init_lhm(self) -> None:
        _unblock(LHM_DIR)
        if LHM_DIR not in sys.path:
            sys.path.insert(0, LHM_DIR)
        try:
            os.add_dll_directory(LHM_DIR)
        except Exception:
            pass
        import clr  # noqa: F401

        clr.AddReference(os.path.join(LHM_DIR, "LibreHardwareMonitorLib.dll"))
        from LibreHardwareMonitor.Hardware import Computer

        computer = Computer()
        computer.IsCpuEnabled = True
        computer.IsMotherboardEnabled = True
        computer.IsGpuEnabled = False
        computer.IsMemoryEnabled = False
        computer.IsStorageEnabled = False
        computer.IsNetworkEnabled = False
        computer.IsControllerEnabled = False
        computer.IsPsuEnabled = False
        computer.Open()
        self._computer = computer

    def _init_nvml(self) -> None:
        try:
            import pynvml

            pynvml.nvmlInit()
            self._nvml = pynvml
            self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(self._gpu_handle)
            self._gpu_name = name.decode() if isinstance(name, bytes) else str(name)
        except Exception as exc:  # noqa: BLE001
            self._nvml = None
            print("NVML unavailable:", exc)

    # ------------------------------------------------------------------ read
    def _read_lhm(self):
        cpu_temp = cpu_load = cpu_power = cpu_clock = None
        fans = {}
        controls = {}

        for hw in self._computer.Hardware:
            hw.Update()
            hw_type = str(hw.HardwareType)
            if hw_type == "Cpu":
                core_temps = []
                for s in hw.Sensors:
                    stype = str(s.SensorType)
                    value = _num(s.Value)
                    if value is None:
                        continue
                    if stype == "Temperature":
                        if s.Name == "CPU Package":
                            cpu_temp = value
                        elif s.Name.startswith("CPU Core #") or s.Name == "Core Max":
                            core_temps.append(value)
                    elif stype == "Load" and s.Name == "CPU Total":
                        cpu_load = value
                    elif stype == "Power" and s.Name == "CPU Package":
                        cpu_power = value
                    elif stype == "Clock" and s.Name == "CPU Core #1":
                        cpu_clock = value
                if cpu_temp is None and core_temps:
                    cpu_temp = max(core_temps)
            elif hw_type == "Motherboard":
                for sub in hw.SubHardware:
                    sub.Update()
                    for s in sub.Sensors:
                        stype = str(s.SensorType)
                        value = _num(s.Value)
                        if value is None:
                            continue
                        if stype == "Fan":
                            fans[s.Name] = value
                        elif stype == "Control":
                            controls[s.Name] = value

        inventory = [
            {
                "name": name,
                "rpm": fans.get(name),
                "pct": controls.get(name),
            }
            for name in sorted(set(fans) | set(controls))
        ]
        if inventory != self._last_inventory:
            self._last_inventory = inventory
            self.inventory.emit(inventory)

        chosen = self.fan_name
        if chosen not in fans and chosen not in controls:
            spinning = [n for n, rpm in fans.items() if rpm and rpm > 0]
            chosen = spinning[0] if spinning else (inventory[0]["name"] if inventory else None)

        return {
            "temp": cpu_temp,
            "load": cpu_load,
            "power": cpu_power,
            "clock": cpu_clock,
            "fan_name": chosen,
            "fan_rpm": fans.get(chosen) if chosen else None,
            "fan_pct": controls.get(chosen) if chosen else None,
        }

    def _read_nvml(self):
        if not self._nvml or not self._gpu_handle:
            return {"name": "GPU", "temp": None, "load": None, "power": None, "fan": None}
        nv = self._nvml
        out = {"name": self._gpu_name, "temp": None, "load": None, "power": None, "fan": None}
        try:
            out["temp"] = float(nv.nvmlDeviceGetTemperature(self._gpu_handle, nv.NVML_TEMPERATURE_GPU))
        except Exception:
            pass
        try:
            out["fan"] = float(nv.nvmlDeviceGetFanSpeed(self._gpu_handle))
        except Exception:
            pass
        try:
            out["load"] = float(nv.nvmlDeviceGetUtilizationRates(self._gpu_handle).gpu)
        except Exception:
            pass
        try:
            out["power"] = nv.nvmlDeviceGetPowerUsage(self._gpu_handle) / 1000.0
        except Exception:
            pass
        return out

    # ------------------------------------------------------------------ loop
    def run(self):
        try:
            self._init_lhm()
        except Exception as exc:  # noqa: BLE001
            self._lhm_error = f"{type(exc).__name__}: {exc}"
            self.failed.emit(self._lhm_error)

        self._init_nvml()
        self.ready.emit()

        while not self._stop:
            started = time.time()
            data = {"ts": started, "gpu": self._read_nvml(), "cpu": {}, "fan": {}}
            if self._computer is not None:
                try:
                    cpu = self._read_lhm()
                    data["cpu"] = {
                        "temp": cpu["temp"],
                        "load": cpu["load"],
                        "power": cpu["power"],
                        "clock": cpu["clock"],
                    }
                    data["fan"] = {
                        "name": cpu["fan_name"],
                        "rpm": cpu["fan_rpm"],
                        "pct": cpu["fan_pct"],
                    }
                except Exception as exc:  # noqa: BLE001
                    self.failed.emit(f"{type(exc).__name__}: {exc}")
            self.sample.emit(data)
            elapsed_ms = (time.time() - started) * 1000.0
            self.msleep(max(150, int(self.interval_ms - elapsed_ms)))

        if self._computer is not None:
            try:
                self._computer.Close()
            except Exception:
                pass
        if self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass

    def stop(self):
        self._stop = True
        self.wait(3000)

    def set_fan(self, name: str):
        self.fan_name = name
