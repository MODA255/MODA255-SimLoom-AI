# Generated engineering inputs: vehicle_model

This directory was generated from one vendor-neutral model specification.

1. Run `catia/create_model.CATScript` on the CATIA workstation.
2. Export the resulting model to the path expected by Ansys and Adams.
3. Run `ansys/solve.py` and `adams/build_model.cmd` using the configured bridge.
4. Run `vi-grade/run_scenario.py` with `vi-grade/scenario.json`.

The agent records every generated file in the task report. Vendor templates
should be reviewed and versioned before production use.
