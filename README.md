# KiCad DXF Import Plugin

A KiCad 10.0+ PCB action plugin that imports DXF files (AutoCAD R12-R2013) directly into the board editor.

## Features

- **Pure Python DXF parser** — no external dependencies
- **Rich entity support**: LINE, CIRCLE, ARC, LWPOLYLINE, POLYLINE (2D), TEXT, MTEXT, ELLIPSE, SPLINE
- **Chinese/CJK support** — auto-detects DXF encoding ($DWGCODEPAGE)
- **Bulge arc interpolation** for curved polylines
- **B-spline approximation** via de Boor's algorithm
- **Configurable import**: unit scale, target layer, text layer, line width

## Installation

1. Create a folder `dxf_import/` in KiCad's scripting plugins directory:
   ```
   %APPDATA%/kicad/10.0/scripting/plugins/dxf_import/
   ```
2. Copy `__init__.py`, `dxf_reader.py`, and `icon.png` into that folder.
3. Restart Pcbnew.
4. The plugin appears under **Tools > External Plugins > Import DXF**.

## Usage

1. Open a PCB in Pcbnew.
2. Click **Tools > External Plugins > Import DXF**.
3. Select your DXF file.
4. Configure import settings and click **Import**.

## Requirements

- KiCad 10.0 or later
- No external Python packages needed

## License

MIT
