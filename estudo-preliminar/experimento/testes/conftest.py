import os
import sys
from pathlib import Path

ALVO = Path(os.environ.get("ALVO", Path(__file__).resolve().parent.parent / "referencia")).resolve()
sys.path.insert(0, str(ALVO))
