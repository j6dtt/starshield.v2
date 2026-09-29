#!/usr/bin/env python3
"""Retrieve a Starlink/Starshield OAuth access token.

Usage: python3 get_token.py --client-id XXX --client-secret YYY [--account-type starshield]
"""
import argparse
import requests
import urllib3

urllib3.disable_warnings()

p = argparse.ArgumentParser(description="Retrieve a Starlink/Starshield access token")
p.add_argument("--client-id", required=True)
p.add_argument("--client-secret", required=True)
p.add_argument("--account-type", choices=["starlink", "starshield"], default="starlink")
p.add_argument("--timeout", type=int, default=15)
args = p.parse_args()

r = requests.post(
    f"https://api.{args.account_type}.com/auth/connect/token",
    data={"client_id": args.client_id,
          "client_secret": args.client_secret,
          "grant_type": "client_credentials"},
    verify=False,
    timeout=args.timeout,
)
r.raise_for_status()
print(r.json()["access_token"])
