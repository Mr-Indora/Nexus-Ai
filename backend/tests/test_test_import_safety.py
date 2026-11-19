import json
import os
import subprocess
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent


def test_importing_backend_tests_preserves_existing_cwd_database(tmp_path):
    database = tmp_path / "test_nexus.db"
    original = b"existing disposable database"
    database.write_bytes(original)
    env = {
        key: value
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE")
        if (value := os.environ.get(key))
    }
    env["PYTHONPATH"] = os.pathsep.join((str(TESTS_DIR), str(BACKEND_DIR)))
    script = (
        "import json; import test_backend; "
        "from pathlib import Path; from sqlalchemy.engine import make_url; "
        "from config import settings; "
        "print(json.dumps({'db_path': str(Path(make_url(settings.DATABASE_URL).database).resolve())}))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    test_db = Path(json.loads(result.stdout.strip().splitlines()[-1])["db_path"])
    assert test_db != database.resolve()
    assert test_db != (BACKEND_DIR / "nexus.db").resolve()
    assert database.read_bytes() == original
