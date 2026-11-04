# Ansys Mechanical scripting entry point (run inside Mechanical).
# Generated for model: vehicle_model; material: steel
import json

MODEL_NAME = 'vehicle_model'
MATERIAL = 'steel'
LOAD_CASES = json.loads('[{"name": "vertical_bump", "force_n": 5000}, {"name": "braking", "force_n": 3500}]')

def build_and_solve(model_path="artifacts/model.step"):
    # ExtAPI is available when this file is run by Ansys Mechanical.
    # Keep all geometry import, named selections, mesh, and result requests
    # behind this function so a project-specific template can override it.
    analysis = ExtAPI.DataModel.Project.Model.Analyses.AddAnalysis("Static Structural")
    analysis.Name = MODEL_NAME + "_static"
    return {"analysis": analysis.Name, "load_cases": LOAD_CASES, "material": MATERIAL}

if __name__ == "__main__":
    print(json.dumps(build_and_solve(), ensure_ascii=False))
