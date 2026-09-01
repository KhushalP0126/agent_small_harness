from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.mathbox import add  # noqa: E402

def test_add():
    assert add(2, 3) == 5
