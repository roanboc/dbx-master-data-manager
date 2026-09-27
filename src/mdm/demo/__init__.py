"""The demo world: invented source records, the landing simulator and the evaluation (owner: CLI, B.14).

`mdm.demo` may import `mdm.models`, `mdm.config`, `mdm.capacity`, `mdm.engine`
and `mdm.backend`. Every name, key and identifier is invented.
"""

from mdm.demo.evaluate import evaluate
from mdm.demo.generator import DemoConfig, DemoWorld, generate
from mdm.demo.lander import land

__all__ = ["DemoConfig", "DemoWorld", "evaluate", "generate", "land"]
