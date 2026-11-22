# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins <ed@andexor.net>

"""Andexor Carmen API. Health only for now; feature 001 (skeleton and pipeline) adds the rest."""

from fastapi import FastAPI
from workers import asgi

app = FastAPI()


@app.get("/api/v1/health")
async def health():
    # Liveness only: touches no dependency (constitution Principle VIII).
    return {"ok": True}


Default = asgi.entrypoint(app)
