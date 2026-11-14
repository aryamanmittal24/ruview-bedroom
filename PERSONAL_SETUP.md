# RuView Bedroom

Private source snapshot of the customized ESP32/WiZ bedroom dashboard.
Upstream: https://github.com/ruvnet/RuView
Upstream base commit: `dd02efe2fe129ae8068e9b805ea35aaa660e47dd`. Original licenses and attribution are retained.
This is a source snapshot, not a GitHub fork; prior Git history is not included.

## Local dashboard

In separate terminals, run:

```sh
python3 scripts/c6-presence-watcher.py --port 5005
python3 scripts/wiz-spacesense-watcher.py
python3 scripts/ruview-sensing-server.py
```

Open http://127.0.0.1:3000/#room-map. Configure ESP32 nodes to send UDP to your computer's current LAN address, port 5005, with unique node IDs. WiZ device mappings in the watcher are installation-specific; edit them for another home. See firmware/esp32-csi-node/README.md for board-specific flashing.

Custom room geometry, live signal display, movement learning code and WiZ event bridge are included. Wi-Fi passwords, firmware backups/build products, captured CSI, calibration files, private room photos and tokens are excluded. Selected interface screenshots are included for documentation. Calibrate after placing the sensors. Activity is experimental and does not identify a person's coordinates or provide validated medical readings.

Submodule references are retained. Use `git submodule update --init --recursive` only when working on the wider upstream components; the local Python dashboard does not need them. Upstream GitHub Actions workflows are omitted and Actions are disabled on this upload; local source and test scripts are retained.
