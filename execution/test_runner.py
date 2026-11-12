"""Independent verification runner. Test commands never execute in the Coder role."""
from dataclasses import asdict, dataclass
from typing import Callable, List, Optional

from execution.sandbox import SandboxExecutor


@dataclass
class TestResult:
    command: str
    status: str
    returncode: int
    stdout: str
    stderr: str


class TesterRunner:
    __test__ = False
    def __init__(self, sandbox: SandboxExecutor):
        self.sandbox = sandbox

    def run(self, commands: List[str], timeout: Optional[float] = None) -> dict:
        results = []
        for command in commands:
            runner = getattr(self.sandbox, "run_verification", self.sandbox.run_command)
            code, stdout, stderr = runner(command, timeout=timeout)
            status = "passed" if code == 0 else ("blocked" if code == 125 or code == 126 else "failed")
            results.append(asdict(TestResult(command, status, code, stdout, stderr)))
        return {"role": "tester", "status": "passed" if results and all(x["status"] == "passed" for x in results) else "failed",
                "results": results}
