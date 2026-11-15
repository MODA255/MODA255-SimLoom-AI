# AgentCAD operation compatibility

SimLoom keeps its canonical operation names under `cad.*`, while accepting the
flat operation names published by AgentCAD. For example, `add_line` resolves to
`cad.line_create`, `move` to `cad.entity_move`, and `add_linear_dim` to
`cad.dimension_linear_create`.

The alias is resolved before schema validation. AgentCAD-style spellings such
as `p1`/`p2`, `points`, `point`, `text_height`, `translation`, `factor`, and
`properties` are copied to the canonical SimLoom fields when the canonical
field was not supplied. If both spellings are present, the canonical SimLoom
field wins. The caller still supplies SimLoom's file-based document context
(`project_dir`, `input_file`, and `output_file` for writes); the operation then
uses the normal transaction, checkpoint, validator, diff, history and rollback
path.

The compatibility names are included in MCP discovery with
`compatibility.alias_for`, so an agent can discover the mapping instead of
guessing it. The alias does not claim a vendor-specific semantic that the
canonical operation cannot provide. For example, both AgentCAD radial and
diameter dimension names currently map to the vendor-neutral radial dimension
operation; CATIA/Ansys/Adams/VI-grade behavior still requires Windows software
validation.

The mapping lives in `src/engineering_agent/agentcad_compat.py`. The registry
and Python operation gateway share the same resolver, which keeps direct,
HTTP, MCP and Web calls consistent.
