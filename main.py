from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    print("Jarvis workspace scaffold is ready.")
    print("Frontend:", root / "frontend")
    print("Backend:", root / "backend")
    print("Tauri:", root / "src-tauri")


if __name__ == "__main__":
    main()
