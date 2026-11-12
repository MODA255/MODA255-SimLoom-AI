"""A deterministic agent runtime for operating engineering software."""

from .agent import EngineeringAgent
from .models import ToolName
from .results import MetricRule
from .script_gen import ModelSpec
from .operations import OperationRegistry
from .validation import validate_plan
from .cad_model import CadDocument, EntityRef, SelectionSet, Layer, Block, Constraint, validate_cad_document
from .cad_operations import CadOperationEngine

__all__ = ["EngineeringAgent", "ToolName", "MetricRule", "ModelSpec", "OperationRegistry", "validate_plan", "CadDocument", "EntityRef", "SelectionSet", "Layer", "Block", "Constraint", "validate_cad_document", "CadOperationEngine"]
