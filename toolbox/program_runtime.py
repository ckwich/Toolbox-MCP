from __future__ import annotations

import ast
import operator
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

from mcp import types

from toolbox.models import ErrorInfo, ProgramCallTrace, ProgramRunResult


class ToolProgramRuntime:
    """A small constrained evaluator for programmatic mounted-tool composition."""

    MAX_PROGRAM_LENGTH = 12_000
    MAX_AST_NODES = 500
    MAX_LOOP_ITERATIONS = 1_000
    MAX_TOOL_CALLS = 128

    def __init__(
        self,
        tool_invoker: Callable[[str, dict[str, Any] | None], Awaitable[types.CallToolResult]],
    ) -> None:
        self._tool_invoker = tool_invoker
        self._traces: list[ProgramCallTrace] = []
        self._loop_iterations = 0

    @classmethod
    def budget_settings(cls) -> dict[str, int]:
        return {
            "max_program_length_chars": cls.MAX_PROGRAM_LENGTH,
            "max_ast_nodes": cls.MAX_AST_NODES,
            "max_loop_iterations": cls.MAX_LOOP_ITERATIONS,
            "max_tool_calls": cls.MAX_TOOL_CALLS,
        }

    async def run(
        self,
        program: str,
        *,
        initial_context: dict[str, Any] | None = None,
        result_variable: str = "result",
        return_variables: list[str] | None = None,
    ) -> ProgramRunResult:
        self._traces = []
        self._loop_iterations = 0
        environment: dict[str, Any] = dict(initial_context or {})

        try:
            if len(program) > self.MAX_PROGRAM_LENGTH:
                self._raise_issue(
                    "budget_exceeded",
                    f"Program exceeds max length of {self.MAX_PROGRAM_LENGTH} characters",
                    details={"limit": self.MAX_PROGRAM_LENGTH},
                )

            module = ast.parse(program, mode="exec")
            ast_nodes = sum(1 for _ in ast.walk(module))
            if ast_nodes > self.MAX_AST_NODES:
                self._raise_issue(
                    "budget_exceeded",
                    f"Program exceeds max AST size of {self.MAX_AST_NODES} nodes",
                    details={"limit": self.MAX_AST_NODES, "actual": ast_nodes},
                )

            last_value: Any = None
            for statement in module.body:
                last_value = await self._exec_statement(statement, environment)

            final_data = environment.get(result_variable, last_value)
            final_text = final_data if isinstance(final_data, str) else None
            return ProgramRunResult(
                success=True,
                error=None,
                result_variable=result_variable,
                final_data=_json_safe_value(final_data),
                final_text=final_text,
                call_count=len(self._traces),
                calls=self._traces,
                variables=self._serializable_bindings(environment, return_variables),
            )
        except _ProgramRuntimeIssue as exc:
            return self._failure_result(
                code=exc.code,
                message=str(exc),
                environment=environment,
                result_variable=result_variable,
                return_variables=return_variables,
                details=exc.details,
            )
        except SyntaxError as exc:
            return self._failure_result(
                code="invalid_program",
                message=exc.msg or "Program could not be parsed",
                environment=environment,
                result_variable=result_variable,
                return_variables=return_variables,
                details={"line": exc.lineno, "offset": exc.offset},
            )
        except ValueError as exc:
            return self._failure_result(
                code="invalid_program",
                message=str(exc),
                environment=environment,
                result_variable=result_variable,
                return_variables=return_variables,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure_result(
                code="program_runtime_error",
                message=str(exc),
                environment=environment,
                result_variable=result_variable,
                return_variables=return_variables,
            )

    def _failure_result(
        self,
        *,
        code: str,
        message: str,
        environment: dict[str, Any],
        result_variable: str,
        return_variables: list[str] | None,
        details: dict[str, Any] | None = None,
    ) -> ProgramRunResult:
        final_data = environment.get(result_variable)
        final_text = final_data if isinstance(final_data, str) else None
        return ProgramRunResult(
            success=False,
            error=ErrorInfo(
                code=code,
                message=message,
                retryable=code in {"tool_timeout", "tool_invocation_failed"},
                details=details,
            ),
            result_variable=result_variable,
            final_data=_json_safe_value(final_data),
            final_text=final_text,
            call_count=len(self._traces),
            calls=self._traces,
            variables=self._serializable_bindings(environment, return_variables),
        )

    async def _exec_statement(self, statement: ast.stmt, environment: dict[str, Any]) -> Any:
        if isinstance(statement, ast.Assign):
            value = await self._eval_expression(statement.value, environment)
            for target in statement.targets:
                self._assign_target(target, value, environment)
            return value

        if isinstance(statement, ast.AugAssign):
            return await self._exec_augmented_assignment(statement, environment)

        if isinstance(statement, ast.Expr):
            return await self._eval_expression(statement.value, environment)

        if isinstance(statement, ast.If):
            branch = statement.body if self._truthy(await self._eval_expression(statement.test, environment)) else statement.orelse
            result: Any = None
            for nested in branch:
                result = await self._exec_statement(nested, environment)
            return result

        if isinstance(statement, ast.For):
            return await self._exec_for_loop(statement, environment)

        if isinstance(statement, ast.Break):
            raise _LoopBreakSignal

        if isinstance(statement, ast.Continue):
            raise _LoopContinueSignal

        if isinstance(statement, ast.Pass):
            return None

        raise ValueError(f"Unsupported statement type: {type(statement).__name__}")

    async def _eval_expression(self, expression: ast.expr, environment: dict[str, Any]) -> Any:
        if isinstance(expression, ast.Constant):
            return expression.value

        if isinstance(expression, ast.Name):
            if expression.id in environment:
                return environment[expression.id]
            raise ValueError(f"Unknown variable: {expression.id}")

        if isinstance(expression, ast.Dict):
            keys = [await self._eval_expression(key, environment) for key in expression.keys]
            values = [await self._eval_expression(value, environment) for value in expression.values]
            return {str(key): value for key, value in zip(keys, values, strict=True)}

        if isinstance(expression, ast.List):
            return [await self._eval_expression(element, environment) for element in expression.elts]

        if isinstance(expression, ast.Tuple):
            return [await self._eval_expression(element, environment) for element in expression.elts]

        if isinstance(expression, ast.Attribute):
            base = await self._eval_expression(expression.value, environment)
            if isinstance(base, dict) and expression.attr in base:
                return base[expression.attr]
            raise ValueError(f"Cannot access attribute '{expression.attr}' on value of type {type(base).__name__}")

        if isinstance(expression, ast.Subscript):
            base = await self._eval_expression(expression.value, environment)
            key = await self._eval_slice(expression.slice, environment)
            try:
                return base[key]
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Subscript lookup failed for key '{key}'") from exc

        if isinstance(expression, ast.Call):
            return await self._eval_call(expression, environment)

        if isinstance(expression, ast.BinOp):
            return await self._eval_binop(expression, environment)

        if isinstance(expression, ast.BoolOp):
            return await self._eval_boolop(expression, environment)

        if isinstance(expression, ast.Compare):
            return await self._eval_compare(expression, environment)

        if isinstance(expression, ast.UnaryOp):
            return await self._eval_unary(expression, environment)

        if isinstance(expression, ast.IfExp):
            branch = expression.body if self._truthy(await self._eval_expression(expression.test, environment)) else expression.orelse
            return await self._eval_expression(branch, environment)

        raise ValueError(f"Unsupported expression type: {type(expression).__name__}")

    async def _eval_call(self, expression: ast.Call, environment: dict[str, Any]) -> Any:
        if not isinstance(expression.func, ast.Name):
            raise ValueError("Only direct builtin function calls are supported")

        function_name = expression.func.id
        positional = [await self._eval_expression(argument, environment) for argument in expression.args]
        keywords = {
            keyword.arg: await self._eval_expression(keyword.value, environment)
            for keyword in expression.keywords
            if keyword.arg is not None
        }

        if function_name == "call_tool":
            return await self._call_tool_builtin(positional, keywords)

        if function_name == "len":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            return len(positional[0])

        if function_name == "sum":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            return sum(positional[0])

        if function_name == "sorted":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            return sorted(positional[0])

        if function_name == "range":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=3)
            return list(range(*[int(value) for value in positional]))

        if function_name == "keys":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            if not isinstance(positional[0], dict):
                raise ValueError("keys expects a dictionary argument")
            return list(positional[0].keys())

        if function_name == "values":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            if not isinstance(positional[0], dict):
                raise ValueError("values expects a dictionary argument")
            return list(positional[0].values())

        if function_name == "items":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            if not isinstance(positional[0], dict):
                raise ValueError("items expects a dictionary argument")
            return list(positional[0].items())

        if function_name == "str":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            return str(positional[0])

        if function_name == "int":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            return int(positional[0])

        if function_name == "float":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            return float(positional[0])

        if function_name == "bool":
            self._ensure_arg_count(function_name, positional, minimum=1, maximum=1)
            return bool(positional[0])

        raise ValueError(f"Unsupported function call: {function_name}")

    async def _call_tool_builtin(self, positional: list[Any], keywords: dict[str, Any]) -> Any:
        if len(self._traces) >= self.MAX_TOOL_CALLS:
            self._raise_issue(
                "budget_exceeded",
                f"Program exceeds max mounted tool call count of {self.MAX_TOOL_CALLS}",
                details={"limit": self.MAX_TOOL_CALLS},
            )

        if keywords and not {"tool", "arguments"} >= set(keywords.keys()):
            raise ValueError("call_tool only accepts 'tool' and 'arguments' keyword parameters")

        if positional:
            tool_name = positional[0]
            arguments = positional[1] if len(positional) > 1 else None
        else:
            tool_name = keywords.get("tool")
            arguments = keywords.get("arguments")

        if not isinstance(tool_name, str):
            raise ValueError("call_tool requires the first argument to be the mounted tool name")
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError("call_tool arguments must be an object")

        try:
            result = await self._tool_invoker(tool_name, arguments or {})
        except TimeoutError as exc:
            self._raise_issue(
                "tool_timeout",
                f"Mounted tool call timed out: {tool_name}",
                details={"tool": tool_name},
            )
            raise exc
        except Exception as exc:  # noqa: BLE001
            self._raise_issue(
                "tool_invocation_failed",
                str(exc),
                details={"tool": tool_name},
            )
            raise exc

        data, text = self._normalize_result(result)
        trace = ProgramCallTrace(
            index=len(self._traces) + 1,
            tool=tool_name,
            arguments=_redacted_argument_value(arguments or {}),
            data=data,
            text=text,
            is_error=bool(result.isError),
            error_message=self._joined_text_content(result) if result.isError else None,
        )
        self._traces.append(trace)
        if trace.is_error:
            self._raise_issue(
                "tool_call_failed",
                trace.error_message or f"Tool call failed: {tool_name}",
                details={"tool": tool_name, "call_index": trace.index},
            )
        return data if data is not None else text

    async def _exec_augmented_assignment(self, statement: ast.AugAssign, environment: dict[str, Any]) -> Any:
        current = self._read_assignment_target(statement.target, environment)
        value = await self._eval_expression(statement.value, environment)
        updated = self._apply_binary_operator(statement.op, current, value)
        self._assign_target(statement.target, updated, environment)
        return updated

    async def _exec_for_loop(self, statement: ast.For, environment: dict[str, Any]) -> Any:
        if statement.orelse:
            raise ValueError("For-else blocks are not supported")

        iterable = await self._eval_expression(statement.iter, environment)
        try:
            iterator = iter(iterable)
        except TypeError as exc:  # noqa: BLE001
            raise ValueError(f"Value of type {type(iterable).__name__} is not iterable") from exc

        result: Any = None
        for item in iterator:
            self._loop_iterations += 1
            if self._loop_iterations > self.MAX_LOOP_ITERATIONS:
                self._raise_issue(
                    "budget_exceeded",
                    f"Program exceeds max loop iteration count of {self.MAX_LOOP_ITERATIONS}",
                    details={"limit": self.MAX_LOOP_ITERATIONS},
                )

            self._assign_target(statement.target, item, environment)
            try:
                for nested in statement.body:
                    result = await self._exec_statement(nested, environment)
            except _LoopContinueSignal:
                continue
            except _LoopBreakSignal:
                break
        return result

    async def _eval_binop(self, expression: ast.BinOp, environment: dict[str, Any]) -> Any:
        left = await self._eval_expression(expression.left, environment)
        right = await self._eval_expression(expression.right, environment)
        return self._apply_binary_operator(expression.op, left, right)

    async def _eval_boolop(self, expression: ast.BoolOp, environment: dict[str, Any]) -> Any:
        values = expression.values
        if isinstance(expression.op, ast.And):
            result = True
            for value in values:
                result = await self._eval_expression(value, environment)
                if not self._truthy(result):
                    return result
            return result

        if isinstance(expression.op, ast.Or):
            for value in values:
                result = await self._eval_expression(value, environment)
                if self._truthy(result):
                    return result
            return result

        raise ValueError(f"Unsupported boolean operator: {type(expression.op).__name__}")

    async def _eval_compare(self, expression: ast.Compare, environment: dict[str, Any]) -> bool:
        left = await self._eval_expression(expression.left, environment)
        comparators = [await self._eval_expression(value, environment) for value in expression.comparators]
        current = left
        for operator_node, comparator in zip(expression.ops, comparators, strict=True):
            comparator_fn = _comparison_operator(operator_node)
            if not comparator_fn(current, comparator):
                return False
            current = comparator
        return True

    async def _eval_unary(self, expression: ast.UnaryOp, environment: dict[str, Any]) -> Any:
        operand = await self._eval_expression(expression.operand, environment)
        if isinstance(expression.op, ast.Not):
            return not self._truthy(operand)
        if isinstance(expression.op, ast.USub):
            return -operand
        if isinstance(expression.op, ast.UAdd):
            return +operand
        raise ValueError(f"Unsupported unary operator: {type(expression.op).__name__}")

    async def _eval_slice(self, slice_node: ast.slice, environment: dict[str, Any]) -> Any:
        if isinstance(slice_node, ast.Slice):
            raise ValueError("Slice syntax is not supported")
        return await self._eval_expression(slice_node, environment)

    def _assign_target(self, target: ast.expr, value: Any, environment: dict[str, Any]) -> None:
        if isinstance(target, ast.Name):
            environment[target.id] = value
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"Cannot unpack value of type {type(value).__name__}")
            if len(target.elts) != len(value):
                raise ValueError(f"Cannot unpack {len(value)} value(s) into {len(target.elts)} target(s)")
            for nested_target, nested_value in zip(target.elts, value, strict=True):
                self._assign_target(nested_target, nested_value, environment)
            return
        raise ValueError(f"Unsupported assignment target: {type(target).__name__}")

    def _read_assignment_target(self, target: ast.expr, environment: dict[str, Any]) -> Any:
        if isinstance(target, ast.Name):
            if target.id not in environment:
                raise ValueError(f"Unknown variable: {target.id}")
            return environment[target.id]
        raise ValueError(f"Unsupported assignment target: {type(target).__name__}")

    @staticmethod
    def _apply_binary_operator(operator_node: ast.operator, left: Any, right: Any) -> Any:
        if isinstance(operator_node, ast.Add):
            return left + right
        if isinstance(operator_node, ast.Sub):
            return left - right
        if isinstance(operator_node, ast.Mult):
            return left * right
        if isinstance(operator_node, ast.Div):
            return left / right
        raise ValueError(f"Unsupported binary operator: {type(operator_node).__name__}")

    @staticmethod
    def _ensure_arg_count(function_name: str, positional: list[Any], *, minimum: int, maximum: int) -> None:
        if not minimum <= len(positional) <= maximum:
            if minimum == maximum:
                raise ValueError(f"{function_name} expects exactly {minimum} positional argument(s)")
            raise ValueError(f"{function_name} expects between {minimum} and {maximum} positional argument(s)")

    @staticmethod
    def _truthy(value: Any) -> bool:
        return bool(value)

    @staticmethod
    def _joined_text_content(result: types.CallToolResult) -> str | None:
        text_blocks = [block.text for block in result.content if isinstance(block, types.TextContent)]
        if not text_blocks:
            return None
        return "\n".join(text_blocks)

    def _normalize_result(self, result: types.CallToolResult) -> tuple[Any | None, str | None]:
        text = self._joined_text_content(result)
        data = result.structuredContent
        if data is None and text is not None:
            data = text
        return data, text

    @staticmethod
    def _serializable_bindings(environment: dict[str, Any], return_variables: list[str] | None) -> dict[str, Any]:
        if not return_variables:
            return {}

        serializable: dict[str, Any] = {}
        for key in return_variables:
            if key.startswith("_") or key not in environment:
                continue
            serializable[key] = _json_safe_value(environment[key])
        return serializable

    @staticmethod
    def _raise_issue(code: str, message: str, details: dict[str, Any] | None = None) -> None:
        raise _ProgramRuntimeIssue(code=code, message=message, details=details)


def _comparison_operator(operator_node: ast.cmpop) -> Callable[[Any, Any], bool]:
    if isinstance(operator_node, ast.Eq):
        return operator.eq
    if isinstance(operator_node, ast.NotEq):
        return operator.ne
    if isinstance(operator_node, ast.Lt):
        return operator.lt
    if isinstance(operator_node, ast.LtE):
        return operator.le
    if isinstance(operator_node, ast.Gt):
        return operator.gt
    if isinstance(operator_node, ast.GtE):
        return operator.ge
    if isinstance(operator_node, ast.In):
        return lambda left, right: left in right
    if isinstance(operator_node, ast.NotIn):
        return lambda left, right: left not in right
    raise ValueError(f"Unsupported comparison operator: {type(operator_node).__name__}")


class _ProgramRuntimeIssue(Exception):
    def __init__(self, *, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class _LoopBreakSignal(Exception):
    pass


class _LoopContinueSignal(Exception):
    pass


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value

    if isinstance(value, datetime | date):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}

    if isinstance(value, list | tuple):
        return [_json_safe_value(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe_value(model_dump(mode="json"))
        except TypeError:
            return _json_safe_value(model_dump())

    return {
        "type": "opaque",
        "class_name": type(value).__name__,
    }


def _redacted_argument_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, dict):
        return {str(key): _redacted_argument_value(item) for key, item in value.items()}

    if isinstance(value, list | tuple):
        return [_redacted_argument_value(item) for item in value]

    return "[redacted]"
