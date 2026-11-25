import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generator.validate_docs import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
