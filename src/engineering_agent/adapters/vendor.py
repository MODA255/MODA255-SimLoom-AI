from .base import CommandAdapter
from ..models import ToolName


class AnsysAdapter(CommandAdapter):
    name = ToolName.ANSYS
    capabilities = frozenset({"mesh", "static-structural", "modal", "thermal", "results"})


class CatiaAdapter(CommandAdapter):
    name = ToolName.CATIA
    capabilities = frozenset({"parametric-geometry", "assembly", "step-export", "iges-export"})


class AdamsAdapter(CommandAdapter):
    name = ToolName.ADAMS
    capabilities = frozenset({"multibody", "kinematics", "dynamics", "motion-analysis"})


class ViGradeAdapter(CommandAdapter):
    name = ToolName.VI_GRADE
    capabilities = frozenset({"vehicle-dynamics", "ride-handling", "lap-simulation", "vehicle-model"})
