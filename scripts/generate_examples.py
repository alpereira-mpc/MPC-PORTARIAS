"""Create examples in a new folder, isolated from production data and numbering."""

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database.store import Store, ROOT
from document_generator.docx import generate

# Test cases intentionally live outside the application data path.
from tests.cases import sample


def main():
    folder = ROOT / "exports" / "exemplos" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory(prefix="mpc_examples_") as temp:
        store = Store(Path(temp) / "examples.db")
        for number in (5, 6, 8):
            target = folder / f"Portaria_PROGE_{number:03d}_2026.docx"
            with target.open("xb") as stream:
                stream.write(generate(sample(store, number), number))
            print(target)
    return folder


if __name__ == "__main__":
    main()
