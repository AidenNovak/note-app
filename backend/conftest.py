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
os.environ["SECRET_KEY"] = "xK9mPqR7vW2nT5jL8hF3bY6cZ0aE4dGx"  # 33-char test-only key

sys.path.insert(0, str(ROOT))
