# VI-grade bridge entry point for vehicle_model.
# The bridge should translate scenario.json into VI-CarRealTime/VI-MotorDrive
# commands on the licensed Windows host and write metrics.json/report.html.
import json
from pathlib import Path

SCENARIO = Path(__file__).with_name("scenario.json")

def load_scenario():
    return json.loads(SCENARIO.read_text(encoding="utf-8"))

if __name__ == "__main__":
    print(json.dumps(load_scenario(), ensure_ascii=False))
