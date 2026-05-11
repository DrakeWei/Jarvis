import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8731,
        reload=os.getenv("JARVIS_RELOAD") == "1",
    )


if __name__ == "__main__":
    main()
