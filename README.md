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
5. Renders a realistic frame: Gaussian star PSFs scaled by magnitude,
   exposure time, and focus, plus dark current, bias, read noise, shot
   noise, then applies binning/subframing/gain/ADC quantization.

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

The Telescope and Focuser sections each have a **"Discover
telescopes/focusers on network"** button: it broadcasts the Alpaca discovery
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

## Testing without real hardware

A minimal mock Alpaca telescope is included for testing the whole pipeline:

```bash
./venv/bin/python scripts/mock_telescope.py --ra 5.5877 --dec -5.3911 --focal-length 800 --aperture 200
```

Point it elsewhere at any time:

```bash
curl -X PUT "http://localhost:11111/debug/pointing?ra_hours=3.7836&dec_deg=24.1167&focal_length_mm=300"
```

A matching mock Alpaca focuser is included for testing defocus:

```bash
./venv/bin/python scripts/mock_focuser.py --position 15000 --step-size 2.5
```

Move it at any time (configure `focuser.in_focus_position` to `15000` in
`config.yaml`/the web setup page to see stars go from sharp to progressively
blurrier as `position` moves away from it):

```bash
curl -X PUT "http://localhost:11114/debug/position?position=15200"
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
