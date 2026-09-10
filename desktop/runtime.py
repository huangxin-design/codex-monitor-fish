"""Compatibility entry point; the shared runtime ships inside the Skill."""
import importlib.util
from pathlib import Path
import sys

source = Path(__file__).resolve().parents[1] / 'skills/codex-token-monitor/assets/desktop/runtime.py'
spec = importlib.util.spec_from_file_location('_codex_monitor_runtime', source)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

if __name__ == '__main__':
    try:
        raise SystemExit(runtime.main())
    except (OSError, runtime.sqlite3.Error, ValueError) as exc:
        runtime.show_fatal_error(exc)
        raise SystemExit(1) from None
else:
    sys.modules[__name__] = runtime
