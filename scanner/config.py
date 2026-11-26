from pathlib import Path

import yaml

CONFIG = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text())
