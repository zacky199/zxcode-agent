import subprocess
from pathlib import Path

class GitOps:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def run_git_cmd(self, args: list) -> str:
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"GIT ERROR: {e.stderr.strip()}"
        except FileNotFoundError:
            return "GIT ERROR: Git tidak terinstal di sistem."

    def status(self) -> str:
        return self.run_git_cmd(["status", "--short"])

    def diff(self) -> str:
        return self.run_git_cmd(["diff"])

    def commit_changes(self, message: str) -> str:
        self.run_git_cmd(["add", "."])
        return self.run_git_cmd(["commit", "-m", message])

    def rollback_last(self) -> str:
        """Mengembalikan perubahan terakhir yang belum di-commit."""
        return self.run_git_cmd(["checkout", "."])
