"""`python -m smith` / installed `smith` script — launch the local HTTP
process. Defaults to 127.0.0.1:18099 (next to TEMPER on 18088).
"""
from __future__ import annotations

import uvicorn

from smith.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run("smith.server:app", host=s.smith_host, port=s.smith_port, reload=False)


if __name__ == "__main__":
    main()
