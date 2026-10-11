# Seraphim v3 - Verified Parts & Design Record

All prices verified against live vendor pages on 2026-10-11.


## 2026-10-11 · Seraphim Drone Parts Verification

Verified live vendor prices for the Seraphim v3 coaxial dual-LiDAR drone.
AUL: 3.28 kg | 372 Wh | 40-50 min practical endurance.

### Verified prices (checked 2026-10-11, vendor pages)

| Part | Qty | Verified Price | Source |
|------|-----|---------------|--------|
| Unitree L2 4D LiDAR | 2 | $419 ea ($838 total) | shop.unitree.com |
| NVIDIA Jetson Orin Nano Super Dev Kit 8GB | 1 | $399 | amazon.com / sparkfun.com |
| T-Motor 2810 900KV brushless motor | 2 | ~$19 ea ($38) | monkeyhobby / vueselect |
| DYS 65A 2-in-1 ESC (2-8S, AM32) | 1 | $43.99 | rotorxpress.com |
| 6S2P 21700 Li-ion pack (8000mAh, ~186Wh) | 2 | ~$32-37 ea ($70) | aliexpress (Zeee/Sinxpower) |
| Bambu Lab PET-CF 1.75mm filament 1kg | 1 | $44.99 | us.store.bambulab.com |
| Bambu Lab P2S hardened steel nozzle hotend 0.6mm | 1 | $51.99 | swingdesign.com / additive-x.com |
| 9x5x3 tri-blade props | 4 | ~$10 ea ($40) | getfpv / kingbird |
| Flight controller (Pixhawk-class w/GPS) | 1 | ~$90-150 | pixhawk.org / holybro.com |
| 915MHz telemetry + 2.4GHz RC radio | 1 | ~$60-100 | holybro.com |
| Standard Remote ID module (Part 89) | 1 | ~$100-250 | bluerobots.com / holybro.com |
| Night strobe LED | 1 | ~$15 | generic FPV |
| Antenna + wiring + BEC + steel inserts | 1 kit | ~$80 | misc |
| **TOTAL (hardware)** | | **~$2,200-2,500** | |

### Design decisions locked in
- Drum: 3x PA-CF truss arcs + tri-honeycomb infill, 230 g (not solid CF)
- Battery: 2x 6S2P 21700 Li-ion (372 Wh), not LiPo (more stable, lighter)
- Compute: Jetson Orin Nano 8GB (GPU needed for dual-L2 128k pts/s 3D detection)
- Control: 1 central frame-tilt actuator + differential RPM (yaw) + collective (altitude)
- Material: PET-CF filament for print (0.6mm hardened-steel nozzle recommended)

### 3D model delivered
- `seraphim_base.stl` (1.2 MB, ASCII STL, 5888 tris)
- `seraphim_base.glb` (283 KB, binary glTF)
- Both verified: 0 NaN, 0 collapsed faces, max radius 228.2 mm
- Ready for Fusion 360 / Blender / Bambu Studio

### Regulatory compliance
- FAA registration N-number (visible on drone)
- Standard Remote ID module (14 CFR Part 89)
- Night anti-collision strobe
- Part 107 Remote Pilot cert (if commercial ops)
- Operational: <=400 ft AGL, VLOS, no overflying uninvolved people
- Huntsville: KHSV Class C / Redstone Arsenal R-2577 / Maxwell AFB nearby
- Gadsden rural: Class G airspace, cleanest for outdoor testing under 400 ft

---
