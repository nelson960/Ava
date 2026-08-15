from __future__ import annotations

import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, NoReturn

from .protocol import (
    CANVAS_MAX_NODES_PER_TURN,
    AvaCanvasTransaction,
    CanvasAnimateOperation,
    CanvasBooleanStateVariable,
    CanvasClearOperation,
    CanvasCreateOperation,
    CanvasDeleteOperation,
    CanvasDragWithinAction,
    CanvasExploreAction,
    CanvasEnumStateVariable,
    CanvasFocusOperation,
    CanvasInteractionBinding,
    CanvasNode,
    CanvasPathNode,
    CanvasRevealAction,
    CanvasRevealOperation,
    CanvasScalarStateVariable,
    CanvasSelectAction,
    CanvasSetEnumAction,
    CanvasSetScalarAction,
    CanvasStateVariable,
    CanvasToggleStateAction,
    CanvasToggleVisibilityAction,
    CanvasUpdateOperation,
)

_NUMBER = re.compile(r"[+-]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+))(?:[eE][+-]?\d+)?")
_COMMANDS = frozenset("MmLlHhVvCcSsQqTtAaZz")
_ARITY = {
    "M": 2,
    "L": 2,
    "H": 1,
    "V": 1,
    "C": 6,
    "S": 4,
    "Q": 4,
    "T": 2,
    "A": 7,
    "Z": 0,
}


class CanvasPathValidationError(ValueError):
    """A path is syntactically invalid or exceeds the bounded grammar."""


class CanvasTransactionRejected(ValueError):
    def __init__(self, code: str, message: str, transaction_id: str) -> None:
        super().__init__(message)
        self.code = code
        self.transaction_id = transaction_id


@dataclass(frozen=True, slots=True)
class ValidatedCanvasScene:
    id: str
    revision: int
    nodes: Mapping[str, CanvasNode]
    bindings: Mapping[str, CanvasInteractionBinding]
    state_variables: Mapping[str, CanvasStateVariable]
    focused_node_id: str | None


def validate_canvas_path(path_data: str) -> None:
    tokens = _tokenize_path(path_data)
    command_count = sum(token in _COMMANDS for token in tokens)
    number_count = len(tokens) - command_count
    if command_count < 1 or command_count > 128:
        raise CanvasPathValidationError("path requires 1-128 commands")
    if number_count > 512:
        raise CanvasPathValidationError("path exceeds 512 numbers")

    command: str | None = None
    values: list[float] = []
    first_command = True
    current_x = 0.0
    current_y = 0.0
    subpath_x = 0.0
    subpath_y = 0.0

    def checked_point(x: float, y: float) -> tuple[float, float]:
        if not (-2_000 <= x <= 3_000 and -1_200 <= y <= 1_800):
            raise CanvasPathValidationError("path point is outside safe bounds")
        return x, y

    def validate_active() -> None:
        nonlocal values, first_command
        nonlocal current_x, current_y, subpath_x, subpath_y
        if command is None:
            return
        upper = command.upper()
        relative = command.islower()
        arity = _ARITY[upper]
        if arity == 0:
            if values:
                raise CanvasPathValidationError(f"{command} does not take numbers")
            current_x, current_y = subpath_x, subpath_y
            first_command = False
            return
        if len(values) < arity or len(values) % arity:
            raise CanvasPathValidationError(
                f"{command} requires complete groups of {arity} numbers"
            )
        if first_command and upper != "M":
            raise CanvasPathValidationError("path must begin with a move command")
        if upper == "M" and len(values) < 2:
            raise CanvasPathValidationError("move command requires a point")
        for offset in range(0, len(values), arity):
            group = values[offset : offset + arity]

            def point(x: float, y: float) -> tuple[float, float]:
                if relative:
                    x += current_x
                    y += current_y
                return checked_point(x, y)

            if upper == "A":
                if group[0] < 0 or group[1] < 0:
                    raise CanvasPathValidationError("arc radii cannot be negative")
                if group[3] not in (0.0, 1.0) or group[4] not in (0.0, 1.0):
                    raise CanvasPathValidationError("arc flags must be 0 or 1")
                if group[0] > 2_000 or group[1] > 1_200:
                    raise CanvasPathValidationError("arc radii exceed safe bounds")
                current_x, current_y = point(group[5], group[6])
            elif upper in ("M", "L", "T"):
                current_x, current_y = point(group[0], group[1])
                if upper == "M" and offset == 0:
                    subpath_x, subpath_y = current_x, current_y
            elif upper == "H":
                x = group[0] + current_x if relative else group[0]
                current_x, current_y = checked_point(x, current_y)
            elif upper == "V":
                y = group[0] + current_y if relative else group[0]
                current_x, current_y = checked_point(current_x, y)
            elif upper == "C":
                point(group[0], group[1])
                point(group[2], group[3])
                current_x, current_y = point(group[4], group[5])
            elif upper in ("S", "Q"):
                point(group[0], group[1])
                current_x, current_y = point(group[2], group[3])
        values = []
        first_command = False

    for token in tokens:
        if token in _COMMANDS:
            validate_active()
            command = token
            continue
        if command is None or command.upper() == "Z":
            raise CanvasPathValidationError("path number appears without a command")
        number = float(token)
        if not math.isfinite(number) or abs(number) > 100_000:
            raise CanvasPathValidationError("path number is outside finite limits")
        values.append(number)
    validate_active()


def _tokenize_path(path_data: str) -> list[str]:
    if not path_data or len(path_data) > 2_000:
        raise CanvasPathValidationError("path requires 1-2000 characters")
    tokens: list[str] = []
    index = 0
    needs_value = True
    while index < len(path_data):
        character = path_data[index]
        if character.isspace():
            index += 1
            continue
        if character == ",":
            if needs_value:
                raise CanvasPathValidationError("unexpected path comma")
            needs_value = True
            index += 1
            continue
        if character in _COMMANDS:
            tokens.append(character)
            needs_value = True
            index += 1
            continue
        match = _NUMBER.match(path_data, index)
        if match is None:
            raise CanvasPathValidationError(
                f"unsupported path token at character {index}"
            )
        tokens.append(match.group(0))
        needs_value = False
        index = match.end()
    if needs_value and tokens and tokens[-1] not in ("Z", "z"):
        raise CanvasPathValidationError("path ends after a separator or command")
    return tokens


class CanvasSceneValidator:
    """Applies complete declarations atomically against a scene revision."""

    def apply(
        self,
        transaction: AvaCanvasTransaction,
        current: ValidatedCanvasScene | None = None,
    ) -> ValidatedCanvasScene:
        if current is not None and current.id != transaction.scene_id:
            self._reject(
                transaction,
                "sceneMismatch",
                f"active scene {current.id} cannot accept {transaction.scene_id}",
            )
        expected_revision = current.revision if current is not None else 0
        if transaction.base_revision != expected_revision:
            self._reject(
                transaction,
                "revisionMismatch",
                f"expected baseRevision {expected_revision}, "
                f"received {transaction.base_revision}",
            )

        nodes = dict(current.nodes) if current is not None else {}
        focused_node_id = current.focused_node_id if current is not None else None
        for operation in transaction.operations:
            if isinstance(operation, CanvasCreateOperation):
                if operation.node.id in nodes:
                    self._reject(
                        transaction,
                        "duplicateNode",
                        f'canvas node "{operation.node.id}" already exists',
                    )
                nodes[operation.node.id] = operation.node
            elif isinstance(operation, CanvasUpdateOperation):
                if operation.node.id not in nodes:
                    self._unknown_node(transaction, operation.node.id)
                nodes[operation.node.id] = operation.node
            elif isinstance(operation, CanvasDeleteOperation):
                if nodes.pop(operation.node_id, None) is None:
                    self._unknown_node(transaction, operation.node_id)
                if focused_node_id == operation.node_id:
                    focused_node_id = None
            elif isinstance(operation, CanvasRevealOperation):
                node = nodes.get(operation.node_id)
                if node is None:
                    self._unknown_node(transaction, operation.node_id)
                nodes[operation.node_id] = node.model_copy(update={"visible": True})
            elif isinstance(operation, CanvasFocusOperation):
                if operation.node_id not in nodes:
                    self._unknown_node(transaction, operation.node_id)
                focused_node_id = operation.node_id
            elif isinstance(operation, CanvasAnimateOperation):
                if operation.node_id not in nodes:
                    self._unknown_node(transaction, operation.node_id)
            elif isinstance(operation, CanvasClearOperation):
                nodes.clear()
                focused_node_id = None
            else:  # pragma: no cover - discriminated union is exhaustive
                self._reject(
                    transaction,
                    "invalidOperation",
                    f"unsupported operation {operation.operation}",
                )

        if len(nodes) > CANVAS_MAX_NODES_PER_TURN:
            self._reject(
                transaction,
                "nodeLimit",
                f"canvas scene exceeds {CANVAS_MAX_NODES_PER_TURN} nodes",
            )

        bindings = {binding.id: binding for binding in transaction.bindings}
        state_variables = {
            variable.id: variable for variable in transaction.state_variables
        }
        self._validate_nodes(transaction, nodes)
        self._validate_bindings(transaction, nodes, bindings, state_variables)
        return ValidatedCanvasScene(
            id=transaction.scene_id,
            revision=transaction.revision,
            nodes=MappingProxyType(nodes),
            bindings=MappingProxyType(bindings),
            state_variables=MappingProxyType(state_variables),
            focused_node_id=focused_node_id,
        )

    def _validate_nodes(
        self,
        transaction: AvaCanvasTransaction,
        nodes: dict[str, CanvasNode],
    ) -> None:
        for node_id, node in nodes.items():
            parent_id = node.parent_id
            if parent_id is not None:
                parent = nodes.get(parent_id)
                if parent is None or parent.kind != "group":
                    self._reject(
                        transaction,
                        "invalidParent",
                        f'node "{node_id}" requires an existing group parent',
                    )
            seen = {node_id}
            depth = 0
            cursor = node
            while cursor.parent_id is not None:
                if cursor.parent_id in seen:
                    self._reject(
                        transaction,
                        "parentCycle",
                        f'canvas parent cycle includes "{node_id}"',
                    )
                seen.add(cursor.parent_id)
                depth += 1
                if depth > 8:
                    self._reject(
                        transaction,
                        "hierarchyTooDeep",
                        "canvas hierarchy exceeds eight levels",
                    )
                cursor = nodes[cursor.parent_id]
            if isinstance(node, CanvasPathNode):
                try:
                    validate_canvas_path(node.path_data)
                except CanvasPathValidationError as error:
                    self._reject(
                        transaction,
                        "invalidPath",
                        f'node "{node_id}" has invalid path data: {error}',
                    )
            if node.kind == "text" and node.text.count("\n") > 3:
                self._reject(
                    transaction,
                    "invalidOperation",
                    f'text node "{node_id}" exceeds four lines',
                )

    def _validate_bindings(
        self,
        transaction: AvaCanvasTransaction,
        nodes: dict[str, CanvasNode],
        bindings: dict[str, CanvasInteractionBinding],
        state_variables: dict[str, CanvasStateVariable],
    ) -> None:
        for binding in bindings.values():
            if binding.node_id not in nodes:
                self._invalid_binding(
                    transaction,
                    binding,
                    f'source node "{binding.node_id}" does not exist',
                )
            action = binding.action
            if isinstance(
                action,
                (
                    CanvasToggleVisibilityAction,
                    CanvasSelectAction,
                    CanvasRevealAction,
                ),
            ):
                if action.target_node_id not in nodes:
                    self._invalid_binding(
                        transaction,
                        binding,
                        f'target node "{action.target_node_id}" does not exist',
                    )
            elif isinstance(action, CanvasExploreAction):
                referenced = {
                    action.target_node_id,
                    *action.reveal_node_ids,
                    *action.animate_node_ids,
                }
                missing = sorted(referenced.difference(nodes))
                if missing:
                    self._invalid_binding(
                        transaction,
                        binding,
                        f"explore references unknown nodes {missing}",
                    )
                if any(nodes[node_id].visible for node_id in action.reveal_node_ids):
                    self._invalid_binding(
                        transaction,
                        binding,
                        "explore reveal nodes must begin hidden",
                    )
            elif isinstance(action, CanvasDragWithinAction):
                target = nodes.get(action.target_node_id)
                if target is None or target.kind == "group":
                    self._invalid_binding(
                        transaction,
                        binding,
                        f'drag target "{action.target_node_id}" '
                        "must be a drawable node",
                    )
            elif isinstance(action, CanvasToggleStateAction):
                variable = state_variables.get(action.target_state_id)
                if not isinstance(variable, CanvasBooleanStateVariable):
                    self._invalid_state(transaction, binding, action.target_state_id)
            elif isinstance(action, CanvasSetScalarAction):
                variable = state_variables.get(action.target_state_id)
                if not isinstance(variable, CanvasScalarStateVariable):
                    self._invalid_state(transaction, binding, action.target_state_id)
                if not variable.minimum <= action.value <= variable.maximum:
                    self._invalid_state(transaction, binding, action.target_state_id)
                steps = (action.value - variable.minimum) / variable.step
                if not math.isclose(steps, round(steps), abs_tol=1e-8):
                    self._invalid_state(transaction, binding, action.target_state_id)
            elif isinstance(action, CanvasSetEnumAction):
                variable = state_variables.get(action.target_state_id)
                if not isinstance(variable, CanvasEnumStateVariable):
                    self._invalid_state(transaction, binding, action.target_state_id)
                if action.value not in variable.values:
                    self._invalid_state(transaction, binding, action.target_state_id)

    @staticmethod
    def _reject(
        transaction: AvaCanvasTransaction,
        code: str,
        message: str,
    ) -> NoReturn:
        raise CanvasTransactionRejected(code, message, transaction.id)

    def _unknown_node(
        self,
        transaction: AvaCanvasTransaction,
        node_id: str,
    ) -> NoReturn:
        self._reject(
            transaction,
            "unknownNode",
            f'canvas node "{node_id}" does not exist',
        )

    def _invalid_binding(
        self,
        transaction: AvaCanvasTransaction,
        binding: CanvasInteractionBinding,
        detail: str,
    ) -> NoReturn:
        self._reject(
            transaction,
            "invalidBinding",
            f'canvas binding "{binding.id}" is invalid: {detail}',
        )

    def _invalid_state(
        self,
        transaction: AvaCanvasTransaction,
        binding: CanvasInteractionBinding,
        state_id: str,
    ) -> NoReturn:
        self._invalid_binding(
            transaction,
            binding,
            f'state "{state_id}" is missing or incompatible',
        )
