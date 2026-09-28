"""cTrader OAuth token helpers.

  python -m advisor.tokens authorize   # one-time, on your own machine: get a fresh token pair
  python -m advisor.tokens refresh     # in GitHub Actions: refresh and write back the repo secrets

cTrader refresh tokens are single use, so the rotated pair must be stored back.
Use a token pair of its own for this bot: if the Zorro plugin refreshes the same
refresh token, one of the two will lose access.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.parse
import urllib.request

TOKEN_URL = "https://openapi.ctrader.com/apps/token"
AUTH_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"


def _get_json(url: str, headers: dict | None = None, data: bytes | None = None, method: str = "GET") -> dict:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as res:
        body = res.read()
    return json.loads(body) if body else {}


def _token_request(params: dict) -> dict:
    res = _get_json(f"{TOKEN_URL}?{urllib.parse.urlencode(params)}", {"Accept": "application/json"})
    if not res.get("accessToken"):
        raise RuntimeError(f"token request failed: {res.get('errorCode')} {res.get('description', '')}")
    return res


def authorize() -> None:
    client_id = os.environ.get("CTRADER_CLIENT_ID") or input("Client ID: ").strip()
    client_secret = os.environ.get("CTRADER_CLIENT_SECRET") or input("Client secret: ").strip()
    redirect = os.environ.get("CTRADER_REDIRECT_URI") or input("Redirect URI (as registered for the app): ").strip()
    q = urllib.parse.urlencode({"client_id": client_id, "redirect_uri": redirect, "scope": "trading", "product": "web"})
    print(f"\nOpen this URL, log in, allow access:\n  {AUTH_URL}?{q}\n")
    got = input("Paste the full redirected URL (or just the code): ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(got).query).get("code", [got])[0]
    res = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                          "client_id": client_id, "client_secret": client_secret})
    print("\nStore these as GitHub repository secrets:")
    print(f"  CTRADER_ACCESS_TOKEN  = {res['accessToken']}")
    print(f"  CTRADER_REFRESH_TOKEN = {res['refreshToken']}")
    print(f"(access token valid for ~{int(res.get('expiresIn', 0)) // 86400} days)")


def _put_secret(repo: str, pat: str, name: str, value: str) -> None:
    from nacl import encoding, public

    api = f"https://api.github.com/repos/{repo}/actions/secrets"
    headers = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    key = _get_json(f"{api}/public-key", headers)
    box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    encrypted = base64.b64encode(box.encrypt(value.encode())).decode()
    _get_json(f"{api}/{name}", {**headers, "Content-Type": "application/json"},
              json.dumps({"encrypted_value": encrypted, "key_id": key["key_id"]}).encode(), "PUT")


def refresh() -> None:
    env = os.environ
    res = _token_request({"grant_type": "refresh_token", "refresh_token": env["CTRADER_REFRESH_TOKEN"],
                          "client_id": env["CTRADER_CLIENT_ID"], "client_secret": env["CTRADER_CLIENT_SECRET"]})
    repo, pat = env["GITHUB_REPOSITORY"], env["GH_SECRETS_PAT"]
    # refresh token first: if the second write failed we could still refresh next time
    _put_secret(repo, pat, "CTRADER_REFRESH_TOKEN", res["refreshToken"])
    _put_secret(repo, pat, "CTRADER_ACCESS_TOKEN", res["accessToken"])
    print(f"tokens rotated, access token valid for ~{int(res.get('expiresIn', 0)) // 86400} days")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "authorize":
        authorize()
    elif cmd == "refresh":
        refresh()
    else:
        sys.exit(__doc__)
