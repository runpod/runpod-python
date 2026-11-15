"""wire protocol for remote function execution.

this is the contract between the sdk and the worker runtime images.
the worker runtime and client share these shapes and validation rules.

live mode ships function source with every request; deployed mode omits
the code (the worker unpacked the build and resolves the function by
name). arguments, results, and stream chunks are json values.
"""

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

from .._serialization import normalize_json, validate_json

EXECUTION_FUNCTION = "function"

FORMAT_JSON = "json"


@dataclass
class FunctionRequest:
    """a request to execute one function on a worker."""

    function_name: str
    function_code: Optional[str] = None
    args: List[Any] = field(default_factory=list)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    dependencies: Optional[List[str]] = None
    system_dependencies: Optional[List[str]] = None
    execution_type: str = EXECUTION_FUNCTION
    accelerate_downloads: bool = True
    serialization_format: str = FORMAT_JSON

    def validate(self) -> None:
        if self.serialization_format != FORMAT_JSON:
            raise ValueError("serialization_format must be 'json'")
        if not isinstance(self.args, list) or not isinstance(self.kwargs, dict):
            raise TypeError("args must be a list and kwargs must be a dictionary")
        validate_json(
            {f.name: getattr(self, f.name) for f in fields(self)}, "function request"
        )

    @classmethod
    def from_input(cls, data: Dict[str, Any]) -> "FunctionRequest":
        if not isinstance(data, dict):
            raise TypeError("function request must be a dictionary")
        request = cls(**{f.name: data[f.name] for f in fields(cls) if f.name in data})
        request.validate()
        request.args = normalize_json(request.args, "arguments")
        request.kwargs = normalize_json(request.kwargs, "keyword arguments")
        return request

    def to_input(self) -> Dict[str, Any]:
        """serialize as the job input dict."""
        self.validate()
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class FunctionResponse:
    """the worker's result for one function execution."""

    success: bool
    json_result: Any = None
    error: Optional[str] = None
    stdout: Optional[str] = None

    @classmethod
    def from_output(cls, output: Dict[str, Any]) -> "FunctionResponse":
        if not isinstance(output, dict) or not isinstance(output.get("success"), bool):
            raise TypeError("function response must contain a boolean success field")
        if output.get("serialization_format", FORMAT_JSON) != FORMAT_JSON:
            raise ValueError("serialization_format must be 'json'")
        if output.get("result") is not None:
            raise ValueError("function responses must use json_result")
        if output["success"] and "json_result" not in output:
            raise ValueError("successful function response is missing json_result")
        validate_json(output, "function response")
        return cls(
            success=output["success"],
            json_result=normalize_json(output.get("json_result"), "return value"),
            error=output.get("error"),
            stdout=output.get("stdout"),
        )
