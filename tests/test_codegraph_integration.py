from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "trellis_codegraph.py"
PINNED_VERSION = "1.6.0"
REQUIRED = os.environ.get("TRELLIS_CODEGRAPH_INTEGRATION") == "1"
TASK_SYMBOL = "task_unique_symbol_omega"
RENAMED_SYMBOL = "task_unique_symbol_omega_renamed"


def _cli() -> str | None:
    return shutil.which("codegraph")


class CodegraphIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not REQUIRED:
            raise unittest.SkipTest(
                "set TRELLIS_CODEGRAPH_INTEGRATION=1 to run real CodeGraph CLI+MCP tests"
            )
        cls.cli = _cli()
        if cls.cli is None:
            raise AssertionError(
                "CodeGraph CLI is required when TRELLIS_CODEGRAPH_INTEGRATION=1; "
                f"install @colbymchenry/codegraph@{PINNED_VERSION}"
            )
        version = subprocess.check_output([cls.cli, "--version"], text=True).strip()
        cls.version = version
        if version != PINNED_VERSION:
            raise AssertionError(
                f"expected CodeGraph {PINNED_VERSION}, found {version}"
            )

    def addCleanupContext(self, manager):  # noqa: ANN001, ANN201, N802
        value = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return value

    def git(self, root: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    def query(self, path: Path, name: str) -> list[dict[object, object]]:
        result = subprocess.run(
            [self.cli, "query", name, "--path", str(path), "--json", "--limit", "20"],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        if isinstance(payload, list):
            return payload
        return []

    def names(self, hits: list[dict[object, object]]) -> set[str]:
        found: set[str] = set()
        for hit in hits:
            node = hit.get("node") if isinstance(hit, dict) else None
            if isinstance(node, dict) and isinstance(node.get("name"), str):
                found.add(node["name"])
        return found

    def mcp_explore(self, project: Path, query: str) -> str:
        env = dict(os.environ)
        env["CODEGRAPH_NO_DAEMON"] = "1"
        proc = subprocess.Popen(
            [self.cli, "serve", "--mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(project.parent),
        )
        assert proc.stdin is not None
        assert proc.stdout is not None

        def send(payload: dict[str, object]) -> None:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

        def recv(timeout: float = 20.0) -> dict[str, object]:
            ready, _, _ = select.select([proc.stdout], [], [], timeout)
            if not ready:
                raise TimeoutError("MCP stdout timed out")
            line = proc.stdout.readline()
            if not line:
                err = proc.stderr.read() if proc.stderr else ""
                raise TimeoutError(f"MCP closed; stderr={err[:500]}")
            return json.loads(line)

        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "trellis-test", "version": "0"},
                    },
                }
            )
            init = recv()
            self.assertIn("result", init)
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "codegraph_explore",
                        "arguments": {
                            "query": query,
                            "projectPath": str(project),
                        },
                    },
                }
            )
            explored = recv()
            content = explored["result"]["content"]
            texts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict)
            ]
            return "\n".join(str(text) for text in texts)
        finally:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

    def test_cli_and_mcp_route_to_task_index_and_sync_rename(self) -> None:
        parent = Path(self.addCleanupContext(tempfile.TemporaryDirectory(prefix="cg-int-")))
        base = parent / "base"
        task = parent / "task"
        subprocess.run(["git", "init", "-q", "-b", "main", base], check=True)
        self.git(base, "config", "user.name", "CodeGraph Integration")
        self.git(base, "config", "user.email", "cg@example.invalid")
        (base / "app.py").write_text("def base_symbol_only():\n    return 1\n", encoding="utf-8")
        self.git(base, "add", "app.py")
        self.git(base, "commit", "-q", "-m", "base")
        subprocess.run([self.cli, "init", "-y", str(base)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(base), "worktree", "add", str(task), "-b", "task/cg-int"],
            check=True,
            capture_output=True,
        )
        self.addCleanup(
            lambda: subprocess.run(
                ["git", "-C", str(base), "worktree", "remove", "--force", str(task)],
                check=False,
                capture_output=True,
            )
        )
        (task / "app.py").write_text(
            "def base_symbol_only():\n    return 1\n\n"
            f"def {TASK_SYMBOL}():\n    return 2\n",
            encoding="utf-8",
        )
        prepared = subprocess.run(
            [
                "python3",
                str(HELPER),
                "prepare",
                "--base-worktree",
                str(base),
                "--worktree",
                str(task),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr + prepared.stdout)
        self.assertTrue((task / ".codegraph").is_dir())
        self.assertFalse((task / ".codegraph").is_symlink())
        self.assertIn(TASK_SYMBOL, self.names(self.query(task, TASK_SYMBOL)))
        self.assertNotIn(TASK_SYMBOL, self.names(self.query(base, TASK_SYMBOL)))
        task_src = f"def {TASK_SYMBOL}("
        renamed_src = f"def {RENAMED_SYMBOL}("
        mcp_task = self.mcp_explore(task, TASK_SYMBOL)
        self.assertIn(task_src, mcp_task)
        mcp_base = self.mcp_explore(base, TASK_SYMBOL)
        self.assertNotIn(task_src, mcp_base)
        (task / "app.py").write_text(
            "def base_symbol_only():\n    return 1\n\n"
            f"def {RENAMED_SYMBOL}():\n    return 2\n",
            encoding="utf-8",
        )
        synced = subprocess.run(
            ["python3", str(HELPER), "sync", "--worktree", str(task)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(synced.returncode, 0, synced.stderr + synced.stdout)
        self.assertIn(RENAMED_SYMBOL, self.names(self.query(task, RENAMED_SYMBOL)))
        self.assertNotIn(TASK_SYMBOL, self.names(self.query(task, TASK_SYMBOL)))
        self.assertNotIn(TASK_SYMBOL, self.names(self.query(base, TASK_SYMBOL)))
        mcp_renamed = self.mcp_explore(task, RENAMED_SYMBOL)
        self.assertIn(renamed_src, mcp_renamed)
        self.assertNotIn(task_src, mcp_renamed)
        mcp_old = self.mcp_explore(task, TASK_SYMBOL)
        self.assertNotIn(task_src, mcp_old)
        self.assertIn(PINNED_VERSION, self.version)


if __name__ == "__main__":
    unittest.main()
