"""The container's command, `python -m assistant.serve`: the service under
uvicorn, on the port Cloud Run gives in $PORT (8000 locally)."""

import os

import uvicorn

from assistant import logs


def config() -> uvicorn.Config:
    # Cloud Run logs every request itself: uvicorn's access lines would be
    # each request twice.
    return uvicorn.Config(
        "assistant.main:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        log_config=logs.CONFIG,
        access_log=False,
    )


def main() -> None:
    uvicorn.Server(config()).run()


if __name__ == "__main__":
    main()
