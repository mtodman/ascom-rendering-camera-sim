# ASCOM Alpaca Camera Simulator (Sky-Rendering)

An ASCOM Alpaca camera device server that implements the full ICameraV4
(Platform 7) REST surface, but instead of a synthetic test pattern, renders
the real patch of sky the camera is pointed at:

1. Queries a configured **Alpaca telescope** device for its current
   RightAscension/Declination (and FocalLength, if it exposes one).
2. Computes the field of view and plate scale from that focal length plus
   the simulated camera's pixel size/sensor resolution.
3. Projects real stars (Tycho-2 catalog, complete to V≈9) from that patch of
   sky onto the sensor's pixel grid via a gnomonic (tangent-plane) projection.
4. Renders a realistic frame: Gaussian star PSFs scaled by magnitude and
   exposure time, dark current, bias, read noise, shot noise, then applies
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

The Telescope section has a **"Discover telescopes on network"** button:
it broadcasts the Alpaca discovery probe, queries every responding device
server's Management API for Telescope devices, and lets you pick one from
a dropdown to auto-fill the Alpaca Base URL / Device Number fields — no
need to know a telescope driver's address ahead of time. This finds any
Alpaca telescope driver already running on the network or the local host
(real hardware drivers, other simulators, etc.), not just this project's
own test stub.

Saving through the web UI uses `ruamel.yaml`'s round-trip mode, so the
comments and formatting in `config.yaml` are preserved — only the fields you
actually change are rewritten; everything else (including comments) is left
exactly as it was.

Alternatively, edit `config.yaml` directly (restart the server afterwards):

- `telescope.alpaca_base_url` / `device_number` — where to find the real
  (or simulated) Alpaca telescope to query for pointing.
- `telescope.use_telescope_focal_length` — if true, reads the telescope's
  Alpaca `FocalLength` property each exposure (converting it from meters,
  the unit the ASCOM spec defines it in, to the millimeters used everywhere
  else in this project); otherwise (or if unavailable) falls back to
  `telescope.fallback_focal_length_mm`, which **is** in millimeters.
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
./venv/bin/python scripts/mock_telescope.py --ra 5.5877 --dec -5.3911 --focal-length 800
```

Point it elsewhere at any time:

```bash
curl -X PUT "http://localhost:11111/debug/pointing?ra_hours=3.7836&dec_deg=24.1167&focal_length_mm=300"
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
  stars (which dominate the mag-9 catalog) are accurately positioned.
