"""TypeSafe AI System One client (Jev). POST https://api.typesafe.ai/v1/systemone"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")


class JevError(RuntimeError):
    pass


def choice(question: dict | str, criteria: dict) -> dict:
    return {"type": "choice", "instructions": question, "criteria": criteria}


class JevClient:
    def __init__(self, api_key: str, model: str = "jev-latest", timeout: float = 10.0, retries: int = 2):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retries = retries

    def system_one(self, state, questions: dict) -> dict:
        """Returns the full response: {"model", "answers": {name: {...}}, "usage": {...}}."""
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        req = urllib.request.Request(
            f"{BASE_URL}/v1/systemone", data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    return json.loads(res.read())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                if e.code in (408, 429) or e.code >= 500:
                    if attempt < self.retries:
                        time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                        continue
                raise JevError(f"HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise JevError(str(e)) from e
        raise JevError("unreachable")
