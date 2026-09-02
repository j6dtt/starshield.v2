import json
import logging
import os
import sys
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from starshield_data import get_starshield_data

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    stream=sys.stderr)

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


def resolve_client_secret(account, env_secret):
    """Per-account client_secret from config.json; CLIENT_SECRET env is a fallback."""
    secret = (account.get('client_secret') or '').strip()
    if not secret or secret == '_secret_':
        return env_secret
    return secret


def get_auth_headers(account, client_secret, grant_type):
    account_type = account.get('account_type', 'starlink')
    token_body = {
        'client_id':     account['client_id'],
        'client_secret': client_secret,
        'grant_type':    grant_type,
    }
    token_resp = requests.post(
        f'https://api.{account_type}.com/auth/connect/token',
        data=token_body,
        verify=False,
    )
    token_resp.raise_for_status()
    token = token_resp.json()['access_token']
    return {
        'accept':        'application/json',
        'content-type':  'application/*+json',
        'authorization': f'Bearer {token}',
    }


if __name__ == '__main__':
    try:
        account_json    = os.environ['ACCOUNT_JSON']
        env_secret      = os.environ.get('CLIENT_SECRET', '')
        grant_type      = os.environ['GRANT_TYPE']
        request_timeout = int(os.environ['REQUEST_TIMEOUT'])
    except KeyError as e:
        logging.error(f'Missing required env var: {e}')
        sys.exit(1)

    try:
        account = json.loads(account_json)
    except json.JSONDecodeError as e:
        logging.error(f'Could not parse ACCOUNT_JSON: {e}')
        sys.exit(1)

    account_num = account.get('account_num', 'UNKNOWN')
    client_secret = resolve_client_secret(account, env_secret)

    try:
        headers = get_auth_headers(account, client_secret, grant_type)
    except Exception as e:
        logging.error(f'{account_num} - auth failed: {e}')
        sys.exit(1)

    try:
        terms = get_starshield_data(headers, account, request_timeout)
    except Exception as e:
        logging.error(f'{account_num} - get_starshield_data failed: {e}')
        sys.exit(1)

    print(json.dumps(terms))
    sys.exit(0)
