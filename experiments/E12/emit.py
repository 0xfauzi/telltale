"""Print an archived stream-json file to stdout, the way the agent that produced it did.

`telltale run` tees the child stdout to the receiver when `--output-format stream-json`
is in the argv, so the real receiver parses the real bytes. The flag is accepted and
ignored here for that reason.
"""

import sys
from pathlib import Path

sys.stdout.write(Path(sys.argv[1]).read_text(encoding="utf-8"))
