"""Run explicit local research input. No source lookup or broker connection."""
import argparse
import json
from pathlib import Path
from . import run_simulation, align_observations


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qhos2.laboratorio")
    parser.add_argument("acao", choices=("simular", "alinhar"))
    parser.add_argument("entrada", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.entrada.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("INPUT_TOO_LARGE")
        data = json.loads(args.entrada.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("INPUT_MUST_BE_OBJECT")
        if args.acao == "simular":
            result = run_simulation(data["events"], data.get("config"))
        else:
            result = align_observations(data["observations"], data["at_ns"],
                                        **data.get("config", {}))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, OSError, KeyError, ArithmeticError):
        print("laboratorio recusado: entrada ou cenario invalido")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
