# based on https://stackoverflow.com/questions/1057431/loading-all-modules-in-a-folder-in-python
from pathlib import Path
files = Path(__file__).parent.glob("*.py")
moduleNames = [f.name[:-3] for f in files if f.is_file() and f.name != "__init__.py"]
__all__ = moduleNames
