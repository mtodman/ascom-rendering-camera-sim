# ASCOM Alpaca Camera Simulator (Sky-Rendering)

An ASCOM Alpaca camera device server that implements the full ICameraV4
(Platform 7) REST surface, but instead of a synthetic test pattern, renders
the real patch of sky the camera is pointed at:

1. Queries a configured **Alpaca telescope** device for its current
   RightAscension/Declination (and FocalLength/ApertureDiameter, if it
   exposes them).
2. Computes the field of view and plate scale from that focal length plus
   the simulated camera's pixel size/sensor resolution.
3. Projects real stars (Tycho-2 catalog, complete to V≈11.5) from that patch
   of sky onto the sensor's pixel grid via a gnomonic (tangent-plane)
   projection.
4. Optionally queries a configured **Alpaca focuser** device for its current
   position and simulates defocus: geometric-optics blur from the focuser's
   offset from a configured in-focus reference position, combined with
   atmospheric seeing.
5. Optionally queries a configured **Alpaca filter wheel** device for its
   current position and adds that filter's own `FocusOffsets` value (read
   directly from the device, in the same step units as the focuser) into the
   defocus calculation — a non-parfocal filter set shifts focus repeatably
   and predictably by filter, exactly like real hardware.
6. Optionally queries a configured **Alpaca cover/calibrator** device: a
   closed cover blocks the star field entirely (like a lens cap), and an
   on+ready calibrator behind it renders a uniform flat-field illumination
   instead — exactly like a real flat panel.
7. Renders a realistic frame: Gaussian star PSFs scaled by magnitude,
   exposure time, and focus (or flat-field illumination if the cover is
   closed), plus dark current, bias, read noise, shot noise, then applies
   binning/subframing/gain/ADC quantization.

No internet access is needed at runtime — the star catalog is a local CSV
file built once by `scripts/build_catalog.py`.

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python scripts/build_catalog.py   # only needed once, or to refresh
```

## Configuration

The easiest way to change settings is the built-in **web setup UI**: with
the server running, open `http://localhost:11115/setup` (or whatever port
you've configured) in a browser, or click through from any Alpaca client's
"Setup" button — this follows the Alpaca spec's standard setup URL
convention, so ASCOM tools find it automatically. Its Status panel shows a
live query of the configured telescope's current RA/Dec (and whether that's
real data or the configured fallback, if it's unreachable), refreshed every
time the page loads. Below that, it lists every setting grouped by section,
pre-filled with the current values. Camera/telescope/
catalog changes apply immediately (no restart), and everything is written
back to `config.yaml`. Server/network changes (host/port/discovery port)
still need a process restart to take effect, which the page will tell you.

The Telescope, Focuser, Filter Wheel, and Cover/Calibrator sections each have a
**"Discover ... on network"** button: it broadcasts the Alpaca discovery
probe, queries every responding device server's Management API for devices
of that type, and lets you pick one from a dropdown to auto-fill the Alpaca
Base URL / Device Number fields — no need to know a driver's address ahead
of time. This finds any matching Alpaca driver already running on the
network or the local host (real hardware drivers, other simulators, etc.),
not just this project's own test stubs.

Saving through the web UI uses `ruamel.yaml`'s round-trip mode, so the
comments and formatting in `config.yaml` are preserved — only the fields you
actually change are rewritten; everything else (including comments) is left
exactly as it was.

Alternatively, edit `config.yaml` directly (restart the server afterwards):

- `telescope.alpaca_base_url` / `device_number` — where to find the real
  (or simulated) Alpaca telescope to query for pointing.
- `telescope.use_telescope_focal_length` / `use_telescope_aperture` — if
  true, reads the telescope's Alpaca `FocalLength`/`ApertureDiameter`
  properties each exposure (converting them from meters, the unit the ASCOM
  spec defines them in, to the millimeters used everywhere else in this
  project); otherwise (or if unavailable) falls back to
  `telescope.fallback_focal_length_mm` / `fallback_aperture_diameter_mm`,
  which **are** in millimeters.
- `focuser.alpaca_base_url` / `device_number` — where to find an Alpaca
  focuser to query for defocus simulation. Leave unreachable/unconfigured
  (the default) and every exposure renders in focus, exactly like before
  this feature existed.
- `focuser.in_focus_position` — the focuser step position that represents
  perfect focus for your setup. Alpaca's `Position` property is just a raw
  step count with no defined zero, so this must be set explicitly (e.g. via
  the web setup page) to match wherever "in focus" actually is.
- `focuser.use_focuser_step_size` — if true, reads the focuser's Alpaca
  `StepSize` property (already in microns, no unit conversion needed);
  otherwise falls back to `focuser.fallback_step_size_um`.
- `focuser.use_optical_position_action` — if true (the default), asks the
  focuser for its true drawtube position via the `OpticalPosition` Alpaca
  Action and renders defocus from that rather than the reported `Position`.
  Only this project's backlash focuser simulator (below) implements it; any
  other focuser rejects the action and `Position` is used automatically.
- `filter_wheel.alpaca_base_url` / `device_number` — where to find an Alpaca
  filter wheel to query for the current filter's focus offset. Leave
  unreachable/unconfigured (the default) and every exposure renders exactly
  like before this feature existed (offset always 0). No local tunable is
  needed — the real device's own per-filter `FocusOffsets` values are used
  directly, so the offset is exactly what the device reports.
- `cover_calibrator.alpaca_base_url` / `device_number` — where to find an
  Alpaca cover/calibrator to query for cover state and calibrator
  brightness. Leave unreachable/unconfigured (the default) and every
  exposure renders exactly like before this feature existed (cover always
  open, calibrator off).
- `cover_calibrator.calibrator_e_per_s_at_max_brightness` — electrons/
  second/pixel the calibrator produces at `Brightness == MaxBrightness`;
  scales linearly down with the device's reported brightness fraction. The
  default is tuned for the default camera's well depth — adjust for other
  sensors/well depths, or to make flats saturate/not saturate as desired.
- `camera.*` — sensor pixel size/resolution, well depth, read noise, dark
  current, seeing FWHM, and `zero_point_e_per_s_mag0` (tune this to control
  overall brightness/exposure behavior).
- `server.port` / `server.discovery_port` — this host already runs other
  Alpaca simulators on 11111/11112, so this project defaults to **11115**.
  The discovery responder binds with `SO_REUSEPORT` so it coexists cleanly
  with other Alpaca device servers already listening on the shared discovery
  port 32227.

## Running

```bash
./venv/bin/python -m camera_sim.server
```

This starts the Alpaca HTTP API on `config.yaml`'s `server.port`, and a UDP
discovery responder on `server.discovery_port` (default Alpaca discovery
port 32227). Any Alpaca-aware client (N.I.N.A., SharpCap, ASCOM tools) can
discover and connect to it as a normal camera.

## Backlash focuser simulator

`focuser_sim` is a second Alpaca device server in this repo: a full
IFocuserV4 absolute focuser with **configurable mechanical backlash**, for
reproducing and fault-finding backlash-related focus problems (e.g. an
autofocus routine or backlash compensation in client software that lands
off focus).

```bash
./venv/bin/python -m focuser_sim.server      # or scripts/start-focusersim.sh
```

On Windows, use `scripts\start-focusersim.ps1` / `stop-focusersim.ps1`
instead. To start/stop it from desktop icons on either OS, see
[Desktop start/stop icons](#desktop-startstop-icons-focuser-simulator).

It listens on port **11118** by default (settings in `focuser_sim.yaml`),
answers Alpaca discovery, and its setup page is at
`http://localhost:11118/setup`.

How backlash is modelled: the simulator tracks the **motor position** (what
it reports as `Position`) separately from the **optical position** (where
the drawtube really is). The drivetrain is engaged in whichever direction
the drawtube last moved. After a direction reversal, the first
`backlash_in_steps` (reversing to move IN, i.e. decreasing position) or
`backlash_out_steps` (reversing to move OUT) motor steps are absorbed by the
slack: `Position` changes, the drawtube doesn't. Reversing back before the
slack is fully taken up undoes the partial take-up, again without moving the
drawtube. For example, with 100 steps each way, moving 25000 → 26000 →
25000 leaves `Position` at 25000 but the drawtube at 25100.

Moves are timed (`steps_per_second`; 0 = instant): `IsMoving` is true while
moving, `Position` updates during the move, and `Halt` stops it part-way.
Moves outside 0..`max_step` are clamped. Temperature is a fixed config
value; temperature compensation isn't available.

The setup page shows a live view of reported vs optical position and how
much slack has been taken up, manual move/halt/reset controls, and a table
of recent moves with the steps each one lost to backlash. Backlash amounts
and speed can be changed there live, without a restart.

Non-standard extras are exposed as Alpaca Actions (listed in
`SupportedActions`):

| Action | Returns |
| --- | --- |
| `OpticalPosition` | The true drawtube position (string integer). |
| `BacklashState` | JSON snapshot: motor/optical position, engaged direction, slack taken up. |
| `ResetBacklash` | Re-syncs the drawtube to the motor. Parameters: `in`, `out`, or empty for `initial_engaged_direction`. |

**Integration with the camera:** point the camera's `focuser.alpaca_base_url`
at the focuser simulator (e.g. `http://localhost:11118`, or use the camera
setup page's "Discover focusers" button). Leave `use_optical_position_action`
on, and set `in_focus_position` in optical terms (at startup and after a
reset, optical = `start_position`). The camera then renders defocus from
where the drawtube really is, so a focus run that backlash spoils produces
visibly soft stars even though the focuser *reports* the right position. The
camera's status page and per-exposure log line show both positions.

A typical fault-finding loop with a client application:

1. Start the camera sim and the focuser sim; connect both from the client.
2. Set `backlash_in_steps`/`backlash_out_steps` on the focuser setup page
   (e.g. match what you suspect your real focuser has).
3. Run the client's autofocus. Watch the focuser setup page's
   *Recent moves* table: every row with non-zero *Lost* is a move whose
   commanded distance the drawtube didn't fully travel, and *Error* shows how
   far the reported position is from the truth at the end.
4. Compare with the client's backlash compensation turned on/off, or with
   different backlash amounts, to confirm whether it compensates correctly
   (final *Error* of 0 after focus).

## Desktop start/stop icons (focuser simulator)

The start/stop scripts run the focuser simulator in the background (no
terminal window), log to `run/`, and show a popup confirming the result;
starting when it's already running just says so, and stopping only ever
touches `focuser_sim.server` processes. `scripts/icons/` has matching
icons (`.ico` for Windows, `.png` for Linux).

Do the one-time [Setup](#setup) (create the venv and install
requirements) first. If you later move the project folder, the icons stop
working: delete them and repeat the steps below from the new location.

### Windows 11

**Scripted:** open PowerShell in the project folder and run:

```powershell
$proj = (Get-Location).Path; $shell = New-Object -ComObject WScript.Shell; foreach ($k in 'Start','Stop') { $l = $shell.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\$k Focuser Simulator.lnk"); $l.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"; $l.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$proj\scripts\$($k.ToLower())-focusersim.ps1`""; $l.WorkingDirectory = $proj; $l.IconLocation = "$proj\scripts\icons\focusersim-$($k.ToLower()).ico,0"; $l.WindowStyle = 7; $l.Save() }
```

This creates **Start Focuser Simulator** and **Stop Focuser Simulator** on
your desktop (it finds the real desktop folder, including a
OneDrive-synced one). Press **F5** on the desktop if they don't appear
straight away.

**By hand:**

1. Right-click the desktop → **New → Shortcut**.
2. For the location, enter (replacing `<project>` with the full path to the
   project folder):
   ```
   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<project>\scripts\start-focusersim.ps1"
   ```
3. Name it (e.g. *Start Focuser Simulator*) → **Finish**.
4. Right-click the shortcut → **Properties → Change Icon → Browse** → pick
   `<project>\scripts\icons\focusersim-start.ico`.
5. Repeat with `stop-focusersim.ps1` and `focusersim-stop.ico` for the stop
   icon.

To pin either to the taskbar or Start menu: right-click it → **Show more
options → Pin to taskbar** / **Pin to Start**.

Logs: `run\focusersim.err.log` (the server's output) and
`run\focusersim.log`.

### Linux (GNOME, KDE, XFCE, etc.)

The `.sh` scripts use `zenity` for their popups (`sudo apt install zenity`
on Debian/Ubuntu/Raspberry Pi OS if it's missing). From the project
folder, run:

```bash
PROJ="$(pwd)"; DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"; mkdir -p ~/.local/share/applications "$DESK"
for k in start stop; do
  f=~/.local/share/applications/focusersim-$k.desktop
  cat > "$f" <<EOF
[Desktop Entry]
Type=Application
Name=${k^} Focuser Simulator
Comment=${k^} the ASCOM Alpaca backlash focuser simulator
Exec="$PROJ/scripts/$k-focusersim.sh"
Icon=$PROJ/scripts/icons/focusersim-$k.png
Terminal=false
Categories=Science;Astronomy;
EOF
  chmod +x "$f" "$PROJ/scripts/$k-focusersim.sh"
  cp "$f" "$DESK/"
  gio set "$DESK/focusersim-$k.desktop" metadata::trusted true 2>/dev/null || true
done
```

This adds **Start Focuser Simulator** and **Stop Focuser Simulator** both
to the applications menu (search for "Focuser") and to the desktop.

- **GNOME / Ubuntu:** desktop icons need the *Desktop Icons NG* extension
  (enabled by default on Ubuntu). If an icon shows as a plain file, or a
  generic icon with a red X, right-click it → **Allow Launching**.
- **KDE Plasma:** the first launch may ask to confirm running the file →
  **Continue** (tick *Do not ask again*).
- **Raspberry Pi OS / LXDE:** if double-clicking asks what to do, choose
  **Execute**. To stop being asked: File Manager → **Edit → Preferences →
  General → Don't ask options on launch executable file**.

To add either to a dock/panel: open the applications menu, find it,
right-click → **Add to Favorites** / **Pin to Task Manager** / **Add to
panel** (wording varies by desktop).

Logs: `run/focusersim.log`.

To remove the icons:

```bash
rm ~/.local/share/applications/focusersim-{start,stop}.desktop "$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"/focusersim-{start,stop}.desktop
```

## Testing without real hardware

A minimal mock Alpaca telescope is included for testing the whole pipeline:

```bash
./venv/bin/python scripts/mock_telescope.py --ra 5.5877 --dec -5.3911 --focal-length 800 --aperture 200
```

Point it elsewhere at any time:

```bash
curl -X PUT "http://localhost:11111/debug/pointing?ra_hours=3.7836&dec_deg=24.1167&focal_length_mm=300"
```

A minimal mock Alpaca focuser (no backlash; instant moves) is also included
for testing defocus:

```bash
./venv/bin/python scripts/mock_focuser.py --position 15000 --step-size 2.5
```

Move it at any time (configure `focuser.in_focus_position` to `15000` in
`config.yaml`/the web setup page to see stars go from sharp to progressively
blurrier as `position` moves away from it):

```bash
curl -X PUT "http://localhost:11114/debug/position?position=15200"
```

A matching mock Alpaca filter wheel is included for testing per-filter focus offsets:

```bash
./venv/bin/python scripts/mock_filterwheel.py
```

Change filters at any time (defaults to `["Red","Green","Blue","Clear","Ha","OIII"]` with offsets `[500,550,150,0,800,250]` steps — filter 4 "Ha" adds 800 steps, i.e. `800 * step_size_um` of extra defocus, on top of whatever the focuser itself is doing):

```bash
curl -X PUT "http://localhost:11117/api/v1/filterwheel/0/position" -d "Position=4"
```

A matching mock Alpaca cover/calibrator is included for testing the closed-cover/flat-field behavior:

```bash
./venv/bin/python scripts/mock_covercalibrator.py
```

Toggle it at any time:

```bash
curl -X PUT "http://localhost:11116/debug/state?cover=closed&calibrator_on=true&brightness=50"
```

Then drive an exposure and render a PNG preview:

```bash
./venv/bin/python scripts/preview.py --base-url http://localhost:11115 --duration 4 --out /tmp/preview.png
```

Run the automated smoke tests:

```bash
./venv/bin/python -m pytest tests/
```

## Notes / simplifications

- Proper motion and precession are ignored (catalog positions are used as-is,
  J2000) — fine for a simulator, but a real star's simulated position could
  drift from truth over long timescales.
- Field rotation is a fixed `camera.rotation_deg` config value, not derived
  from a real rotator device.
- Tycho-2 is known to be less reliable for the very brightest stars
  (V ≲ 2-3, satellite saturation) and in very dense/crowded fields; fainter
  stars (which dominate the catalog) are accurately positioned.
- Defocus blur is computed from simple geometric optics (blur size scales
  with focuser offset ÷ focal ratio) and combined with atmospheric seeing
  in quadrature, rendered as a wider Gaussian PSF. A real defocused star's
  PSF is closer to a disk (or an annulus, with a central obstruction) than
  a Gaussian, but this is a standard simplification for this purpose — it
  gives a smooth, monotonic FWHM/HFD-vs-focuser-position curve, which is
  what autofocus routines actually need to converge on.
