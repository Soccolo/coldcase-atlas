#!/usr/bin/env python
"""Start Cold Case Atlas. Loads .env if present, then serves on :8000."""
import os
import pathlib
import webbrowser

env = pathlib.Path(__file__).with_name(".env")
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

if __name__ == "__main__":
    import uvicorn
    webbrowser.open("http://127.0.0.1:8000")
    uvicorn.run("atlas.server:app", host="127.0.0.1", port=8000, reload=False)
