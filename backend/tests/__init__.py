"""ResolveAI backend test suite.

Run everything with:

    python -m unittest discover -s backend/tests -t backend -v

or from the repository root via ``python run_checks.py``.

The suite uses only the standard library (``unittest``) so it needs no test
runner to be installed. Every test runs against a throwaway SQLite database
created in a temporary directory - the developer's ``backend/support.db`` is
never touched.
"""