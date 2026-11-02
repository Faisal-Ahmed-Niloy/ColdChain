"""Physics model of a vaccine fridge, used for three things:
  1. generating the AI training dataset (ml/train.py)
  2. the software device simulator (simulator/fridge_sim.py)
  3. tests

Model (Newton's law of cooling, Euler integration, units deg C and minutes):
    dT/dt = k_leak * (T_amb - T)                      heat leak through the walls
          + door * k_door * (T_amb - T)               extra leak while the door is open
          - comp * cool_rate * health                  compressor cooling
The controller is a copy of the firmware rule: ON at >= 7 C (after 60 s min
off-time), OFF at <= 3 C, forced OFF without mains power.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

T_COMP_ON, T_COMP_OFF, MIN_OFF_S = 7.0, 3.0, 60.0


@dataclass
class FridgeParams:
    ambient: float = 30.0            # deg C, Dhaka room temperature
    ambient_swing: float = 3.0       # daily +/- swing
    k_leak: float = 0.010            # 1/min
    k_door: float = 0.12             # 1/min, extra while door open
    cool_rate: float = 0.9           # deg C/min at full health
    health: float = 1.0              # 1.0 = new compressor, 0.3 = badly worn
    health_decay_per_h: float = 0.0  # gradual weakening
    sensor_noise: float = 0.03       # deg C std (DS18B20 resolution is 0.0625)
    door_rate_per_h: float = 3.0     # random openings per hour
    door_mean_s: float = 25.0
    long_door_prob: float = 0.06     # chance an opening lasts 2-5 min
    power_fail_rate_per_h: float = 0.06
    power_fail_mean_min: float = 30.0

    @staticmethod
    def random(rng: np.random.Generator) -> "FridgeParams":
        return FridgeParams(
            ambient=float(rng.uniform(24, 35)),
            ambient_swing=float(rng.uniform(0, 4)),
            k_leak=float(rng.uniform(0.005, 0.016)),
            k_door=float(rng.uniform(0.06, 0.2)),
            cool_rate=float(rng.uniform(0.5, 1.3)),
            health=float(rng.choice([1.0, 1.0, rng.uniform(0.6, 1.0), rng.uniform(0.25, 0.6)])),
            health_decay_per_h=float(rng.choice([0.0, 0.0, rng.uniform(0.005, 0.03)])),
            sensor_noise=float(rng.uniform(0.02, 0.06)),
            door_rate_per_h=float(rng.uniform(0.5, 6)),
            door_mean_s=float(rng.uniform(10, 45)),
            long_door_prob=float(rng.uniform(0.02, 0.12)),
            power_fail_rate_per_h=float(rng.uniform(0.0, 0.12)),
            power_fail_mean_min=float(rng.uniform(10, 60)),
        )


@dataclass
class FridgePhysics:
    params: FridgeParams
    rng: np.random.Generator
    temp: float = 5.0
    t: float = 0.0                       # seconds since start
    comp: bool = False
    comp_off_since: float = -1e9
    door: bool = False
    door_until: float = 0.0
    mains: bool = True
    mains_until: float = 0.0
    health_now: float = field(init=False)
    sensed: float = field(init=False)
    cycle_eff: float = 1.0               # per-cycle compressor efficiency variation

    def __post_init__(self) -> None:
        self.health_now = self.params.health
        self.sensed = self.temp

    def ambient(self) -> float:
        p = self.params
        return p.ambient + p.ambient_swing * math.sin(2 * math.pi * self.t / 86400.0)

    def measured(self) -> float:
        # the probe lags the air temperature (first-order, ~30 s time constant)
        noisy = self.sensed + self.rng.normal(0, self.params.sensor_noise)
        return round(noisy / 0.0625) * 0.0625        # DS18B20 12-bit quantisation

    def step(self, dt: float = 5.0, door: bool | None = None, mains: bool | None = None,
             random_events: bool = True) -> dict:
        p = self.params
        # --- random disturbances (unless the caller forces them) ---
        if door is None:
            if self.door and self.t >= self.door_until:
                self.door = False
            if not self.door and random_events and self.rng.random() < p.door_rate_per_h * dt / 3600:
                dur = (self.rng.uniform(120, 300) if self.rng.random() < p.long_door_prob
                       else self.rng.exponential(p.door_mean_s))
                self.door, self.door_until = True, self.t + dur
        else:
            self.door = door
        if mains is None:
            if not self.mains and self.t >= self.mains_until:
                self.mains = True
            if self.mains and random_events and self.rng.random() < p.power_fail_rate_per_h * dt / 3600:
                self.mains = False
                self.mains_until = self.t + self.rng.exponential(p.power_fail_mean_min) * 60
        else:
            self.mains = mains

        # --- controller (same rule as the firmware) ---
        m = self.measured()
        if not self.mains:
            if self.comp:
                self.comp, self.comp_off_since = False, self.t
        elif self.comp and m <= T_COMP_OFF:
            self.comp, self.comp_off_since = False, self.t
        elif not self.comp and m >= T_COMP_ON and self.t - self.comp_off_since >= MIN_OFF_S:
            self.comp = True
            self.cycle_eff = float(np.clip(self.rng.normal(1.0, 0.07), 0.8, 1.2))

        # --- physics ---
        amb = self.ambient()
        dTdt = p.k_leak * (amb - self.temp)
        if self.door:
            dTdt += p.k_door * (amb - self.temp)
        if self.comp:
            # a hot room lowers the compressor's effective capacity (condenser runs hotter)
            ambient_derate = max(0.6, 1.0 - 0.012 * (amb - 25.0))
            dTdt -= p.cool_rate * self.health_now * self.cycle_eff * ambient_derate
        self.temp += dTdt * dt / 60.0
        self.sensed += (self.temp - self.sensed) * dt / (30.0 + dt)
        self.health_now = max(0.05, self.health_now - p.health_decay_per_h * dt / 3600)
        self.t += dt
        return {"t": self.t, "temp": m, "door": int(self.door), "comp": int(self.comp),
                "mains": int(self.mains), "health": self.health_now, "true_temp": self.temp}


def simulate(params: FridgeParams, hours: float, seed: int, dt: float = 5.0,
             start_temp: float | None = None) -> list[dict]:
    rng = np.random.default_rng(seed)
    sim = FridgePhysics(params, rng, temp=start_temp if start_temp is not None else float(rng.uniform(3, 7)))
    return [sim.step(dt) for _ in range(int(hours * 3600 / dt))]
