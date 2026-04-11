"""Module entrypoint — run with: python -m mars_agent.dashboard

Environment variables:
  DASHBOARD_HOST   bind address (default: 0.0.0.0)
  DASHBOARD_PORT   bind port    (default: 8000)
"""

from __future__ import annotations

import os

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "mars_agent.dashboard_app:app",
        host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        port=int(os.getenv("DASHBOARD_PORT", "8000")),
        reload=False,
    )
