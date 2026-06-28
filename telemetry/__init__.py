# Re-export the CLI entrypoint so `import telemetry` exposes main()/build_parser().
import importlib.util as _ilu
from pathlib import Path as _Path

_cli_path = _Path(__file__).parent.parent / "telemetry.py"
_spec = _ilu.spec_from_file_location("telemetry._cli", _cli_path)
_cli = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_cli)
main = _cli.main
build_parser = _cli.build_parser
