# P8 live-verify (Home Assistant integration -- A1 Mini)

Everything below needs a real Home Assistant instance (with the Mosquitto
add-on), a real A1 Mini, and a real smart plug/door sensor; none of it is
exercised by the automated suite.

- [ ] Save the MQTT settings and see the device "A1 Mini" appear in HA with
      9 sensors, Printing, Camera and 3 buttons. Restart `printerd` and
      confirm there are no duplicate entities (unique ids are serial-based,
      so they survive the restart).
- [ ] Press **Pause** in HA → the A1 Mini pauses. Press **Resume** → it
      resumes.
- [ ] Link the door `binary_sensor`, condition `eq "on"`, with **Hold
      queue** on. Open the door → the queue item shows **Held "Waiting on
      binary_sensor.…"** and the PrinterCard badge appears. Close it → it
      dispatches within about 25 s (alert TTL + poll interval).
- [ ] Link the smart plug `sensor.*_energy` as `energy_kwh` and run a
      print → `prints.energy_kwh` ≈ the plug delta, and `energy_cost` is
      folded into `total_cost` (and into the P7 charge when billing is on).
      Re-run across a **plug reset** (power-cycle the plug mid-print or
      swap it) and confirm the delta still adds up instead of going
      negative.
- [ ] The Camera entity's image updates about every 30 s while a print is
      RUNNING/PREPARE.
- [ ] Stop HA (or set a bad REST token) → the queue still dispatches
      normally and the settings card shows the "Home Assistant
      unreachable" warning.
- [ ] Delete a test printer → its HA entities disappear from HA (empty
      retained discovery payloads).
- [ ] Grep the printerd/worker logs for the MQTT password and HA token →
      there are no hits.

- [ ] The controller runs the spec Verification block.
