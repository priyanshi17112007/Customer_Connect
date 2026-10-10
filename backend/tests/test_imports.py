"""Every backend module must compile and import cleanly.

Catches syntax errors, stale imports and accidental import-time failures -
including the failure mode where a module needs a package that is not installed.
"""

import compileall
import importlib
import pathlib
import unittest

from .helpers import BACKEND_ROOT

APP_DIR = BACKEND_ROOT / "app"


def _module_names() -> list[str]:
    names = []
    for path in sorted(APP_DIR.rglob("*.py")):
        parts = list(path.relative_to(BACKEND_ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts[-1] == "__main__":
            continue
        names.append(".".join(parts))
    return names


class TestCompilation(unittest.TestCase):
    def test_every_module_compiles(self):
        ok = compileall.compile_dir(str(APP_DIR), quiet=2, force=True)
        self.assertTrue(ok, "one or more backend modules failed to compile")

    def test_every_module_imports(self):
        failures = []
        for name in _module_names():
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual([], failures, "modules failed to import:\n" + "\n".join(failures))

    def test_application_factory_builds(self):
        from app.main import create_app

        app = create_app()
        self.assertIsNotNone(app)

    def test_no_module_defines_a_stale_chat_contract(self):
        """Only app/api/schemas.py may define the public chat contract.

        A second, divergent copy of these models is a maintenance hazard: it
        drifts and misleads. This asserts there is exactly one definition.
        """
        offenders = []
        for path in sorted(APP_DIR.rglob("*.py")):
            if path.name == "schemas.py" and path.parent.name == "api":
                continue
            text = path.read_text(encoding="utf-8")
            if "class ChatResponse" in text or "class ChatRequest" in text:
                offenders.append(str(path.relative_to(BACKEND_ROOT)))
        self.assertEqual([], offenders, "duplicate chat schema definitions found")

    def test_requirements_lists_only_imported_packages(self):
        """requirements.txt must not reintroduce packages nothing imports.

        Only real dependency lines are checked - the file's comment block names
        removed packages deliberately, to stop them coming back.
        """
        raw = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
        declared = {
            line.strip().split("==")[0].strip().lower()
            for line in raw.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        for removed in ("python-jose", "passlib", "bcrypt", "slowapi",
                        "aiofiles", "alembic", "pydantic-settings", "langgraph",
                        "langchain-community"):
            self.assertNotIn(
                removed, declared,
                f"{removed} is unused and must not be a declared dependency",
            )

    def test_no_duplicate_dependency_lines(self):
        lines = [
            line.strip().lower()
            for line in (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertEqual(len(lines), len(set(lines)), "duplicate dependency entries")


if __name__ == "__main__":
    unittest.main()