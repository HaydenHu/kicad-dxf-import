# KiCad DXF Import Plugin (IPC API)

A KiCad 10.0+ IPC plugin that imports DXF files (AutoCAD R12-R2018+) into the PCB editor via the IPC API.

## Features

- **Pure Python DXF parser** — no external CAD libraries needed
- **Rich entity support**: LINE, CIRCLE, ARC, LWPOLYLINE, POLYLINE (2D), TEXT, MTEXT, ELLIPSE, SPLINE, DIMENSION, LEADER
- **Native KiCad dimensions** — DXF DIMENSION/LEADER entities become KiCad PCB_DIM objects
- **Chinese/CJK support** — auto-detects DXF encoding ($DWGCODEPAGE)
- **Bulge arc interpolation** for curved polylines
- **B-spline approximation** via de Boor's algorithm
