"""Mechanical backlash model: tracks the motor position (what an Alpaca
focuser reports as Position) separately from the optical (drawtube)
position, which is what actually determines focus.

Backlash is modelled as a single gap (slack) of `backlash_steps` in the
drivetrain. The drivetrain is always "engaged" against one side of that gap
- the direction the drawtube last moved. Motor steps in the engaged
direction move the drawtube one-for-one. Motor steps in the opposite
direction first have to cross the gap: the drawtube doesn't move at all
until `backlash_steps` steps have been absorbed, after which that direction
becomes the engaged one and the drawtube follows again. A partial take-up
(reversing, then reversing back before the gap is crossed) is undone
step-for-step on the way back, also without moving the drawtube.

Because it's one physical gap, a reversal in either direction loses the
same number of steps, and |Position - optical| can never exceed the gap -
however many reversals happen. (An earlier version allowed separate IN/OUT
amounts, but unequal amounts ratchet the drawtube away from the motor by
the difference on every out-and-back cycle, without bound - something no
real drivetrain does.)

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
    takeup_steps: int  # gap crossed so far toward reversing out of engaged_direction
    reversal_backlash_steps: int  # total gap to cross before a reversal moves the drawtube
    backlash_steps: int

    @property
    def offset_steps(self) -> int:
        """motor - optical: how far the reported Position is from the truth."""
        return self.motor_position - self.optical_position


class BacklashModel:
    def __init__(self, position: int, backlash_steps: int, engaged: int = OUT):
        self.motor = int(position)
        self.optical = int(position)
        self.backlash = max(0, int(backlash_steps))
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

        needed = self.backlash - self.takeup
        if n < needed:
            self.takeup += n
            return
        n -= needed
        self.engaged = direction
        self.takeup = 0
        self.optical += direction * n

    def set_backlash(self, backlash_steps: int) -> None:
        self.backlash = max(0, int(backlash_steps))
        # A partial take-up that's now at least the (smaller) new gap means
        # the gears are engaged in the reversal direction.
        if self.takeup > 0 and self.takeup >= self.backlash:
            self.engaged = -self.engaged
            self.takeup = 0

    def reset(self, position: int | None = None, engaged: int | None = None) -> None:
        """Re-syncs the drawtube to the motor (e.g. after a simulated
        re-home), with the gap fully taken up in `engaged` direction."""
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
            reversal_backlash_steps=self.backlash,
            backlash_steps=self.backlash,
        )
