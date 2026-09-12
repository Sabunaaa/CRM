import os

import uvicorn

from . import models  # noqa: F401 - registers database tables
from .database import Base, engine


def main() -> None:
    Base.metadata.create_all(bind=engine)
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")), proxy_headers=True)


if __name__ == "__main__":
    main()
