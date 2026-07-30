"""wire protocol for remote function execution.

this is the contract between the sdk and the worker runtime images.
the worker repo imports these shapes from the runpod package so client
and worker can never drift.

live mode ships function source with every request; deployed mode omits
the code (the worker unpacked the build and resolves the function by
name). args/kwargs are base64 cloudpickle unless serialization_format
is "json".
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

EXECUTION_FUNCTION = "function"

FORMAT_CLOUDPICKLE = "cloudpickle"
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
    serialization_format: str = FORMAT_CLOUDPICKLE

    def to_input(self) -> Dict[str, Any]:
        """serialize as the job input dict."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class FunctionResponse:
    """the worker's result for one function execution."""

    success: bool
    result: Optional[str] = None
    json_result: Any = None
    error: Optional[str] = None
    stdout: Optional[str] = None

    @classmethod
    def from_output(cls, output: Dict[str, Any]) -> "FunctionResponse":
        return cls(
            success=output.get("success", False),
            result=output.get("result"),
            json_result=output.get("json_result"),
            error=output.get("error"),
            stdout=output.get("stdout"),
        )
