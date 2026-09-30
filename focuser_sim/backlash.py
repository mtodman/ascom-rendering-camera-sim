"""Mechanical backlash model: tracks the motor position (what an Alpaca
focuser reports as Position) separately from the optical (drawtube)
position, which is what actually determines focus.

The drivetrain is always "engaged" in one direction - the direction the
drawtube last moved. Motor steps in the engaged direction move the drawtube
one-for-one. Motor steps in the opposite direction first have to take up
the slack in the gears/coupling: the drawtube doesn't move at all until
`backlash_steps[that direction]` steps have been absorbed, after which that
direction becomes the engaged one and the drawtube follows again.

A partial take-up (reversing, then reversing back before all the slack is
absorbed) is undone step-for-step on the way back, also without moving the
drawtube - so with equal IN/OUT amounts this reduces to the classic
"dead band of width N" gap model. Separate IN/OUT amounts model a
drivetrain whose slack differs by direction (e.g. gravity loading a
drawtube that's pointing up, or an asymmetric coupling).

Direction convention (ASCOM): OUT = increasing Position, IN = decreasing.
"""
from __future__ import annotations

from dataclasses import dataclass

OUT = 1
IN = -1


def direction_name(direction: int) -> str:
    return "out" if direction == OUT else "in"


def parse_direction(name: str) -> int:
    return OUT if name.strip().lower() == "out" else IN


@dataclass
class BacklashSnapshot:
    motor_position: int
    optical_position: int
    engaged_direction: str  # "in" | "out"
    takeup_steps: int  # slack absorbed so far toward reversing out of engaged_direction
    reversal_backlash_steps: int  # total slack to absorb before a reversal moves the drawtube
    backlash_in_steps: int
    backlash_out_steps: int

    @property
    def offset_steps(self) -> int:
        """motor - optical: how far the reported Position is from the truth."""
        return self.motor_position - self.optical_position


class BacklashModel:
    def __init__(self, position: int, backlash_in_steps: int, backlash_out_steps: int, engaged: int = OUT):
        self.motor = int(position)
        self.optical = int(position)
        self.backlash = {IN: max(0, int(backlash_in_steps)), OUT: max(0, int(backlash_out_steps))}
        self.engaged = engaged
        self.takeup = 0

    def move(self, delta: int) -> None:
        """Applies `delta` motor steps (signed: + is OUT) through the
        drivetrain, updating the optical position accordingly."""
        if delta == 0:
            return
        direction = OUT if delta > 0 else IN
        n = abs(delta)
        self.motor += delta

        if direction == self.engaged:
            # Moving back toward engagement undoes any partial take-up first.
            undo = min(self.takeup, n)
            self.takeup -= undo
            n -= undo
            self.optical += direction * n
            return

        needed = self.backlash[direction] - self.takeup
        if n < needed:
            self.takeup += n
            return
        n -= needed
        self.engaged = direction
        self.takeup = 0
        self.optical += direction * n

    def set_backlash(self, backlash_in_steps: int, backlash_out_steps: int) -> None:
        self.backlash = {IN: max(0, int(backlash_in_steps)), OUT: max(0, int(backlash_out_steps))}
        # A partial take-up that's now at least the (smaller) new slack means
        # the gears are engaged in the reversal direction.
        reversal = -self.engaged
        if self.takeup > 0 and self.takeup >= self.backlash[reversal]:
            self.engaged = reversal
            self.takeup = 0

    def reset(self, position: int | None = None, engaged: int | None = None) -> None:
        """Re-syncs the drawtube to the motor (e.g. after a simulated
        re-home), with the slack fully taken up in `engaged` direction."""
        if position is not None:
            self.motor = int(position)
        self.optical = self.motor
        if engaged is not None:
            self.engaged = engaged
        self.takeup = 0

    def snapshot(self) -> BacklashSnapshot:
        return BacklashSnapshot(
            motor_position=self.motor,
            optical_position=self.optical,
            engaged_direction=direction_name(self.engaged),
            takeup_steps=self.takeup,
            reversal_backlash_steps=self.backlash[-self.engaged],
            backlash_in_steps=self.backlash[IN],
            backlash_out_steps=self.backlash[OUT],
        )
