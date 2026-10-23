"""Travel Job candidate collection tools."""

def main() -> None:
    from .albamon import main as collect
    raise SystemExit(collect())
