import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TEST_DATA_DIR = ROOT / "data" / "test"
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
(TEST_DATA_DIR / "files").mkdir(parents=True, exist_ok=True)
(TEST_DATA_DIR / "noteapp-test.db").unlink(missing_ok=True)

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(TEST_DATA_DIR / 'noteapp-test.db').resolve()}"
os.environ["STORAGE_PATH"] = str((TEST_DATA_DIR / "files").resolve())
os.environ["SECRET_KEY"] = "test-secret-key-change-me-please"

sys.path.insert(0, str(ROOT))
