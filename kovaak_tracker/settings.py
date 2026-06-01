from pathlib import Path


OUTPUT_DIR = Path("output")


def ensure_output_dir(output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir

