import json
import logging
import os
import comlibv3
import time
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from starshield_data import get_starshield_data

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

# ---------------- Load config --------------------
def load_config():
    try:
        with open('./config.json', 'r') as f:
            return json.load(f)["config"]
    except Exception as e:
        logging.error(f"load_config failed: {e}")
        raise

# ---------------- Token request -----------------
def get_auth_headers(account, grant_type):
    try:
        token_body = {
            "client_id": account["client_id"],
            "client_secret": os.getenv("CLIENT_SECRET"),
            "grant_type": grant_type
        }
        token_resp = requests.post(
            "https://api.starlink.com/auth/connect/token",
            data=token_body,
            verify=False
        )
        token = token_resp.json()["access_token"]
        return {
            "accept": "application/json",
            "content-type": "application/*+json",
            "authorization": f"Bearer {token}"
        }
    except Exception as e:
        logging.error(f"{account['account_num']} - get_auth_headers failed: {e}")
        raise

if __name__ == "__main__":
    try:
        while True:
            try:
                config = load_config()
                authentication = config["authentication"]
                grant_type = authentication["grant_type"]
                request_timeout = config["request_timeout"]
                all_terms = []
                failed = []
                seen = set()
                accounts = []
                for account in authentication["accounts"]:
                    if account["account_num"] in seen:
                        logging.warning(f"{account['account_num']} - duplicate account number in account list, skipping.")
                    else:
                        seen.add(account["account_num"])
                        accounts.append(account)
                logging.info("Starting data retrieval...")
                for account in accounts:
                    try:
                        headers = get_auth_headers(account, grant_type)
                        terms = get_starshield_data(headers, account, request_timeout)
                        all_terms.extend(terms)
                    except Exception as e:
                        logging.error(f"{account['account_num']} - error: {e} — skipping account.")
                        failed.append(account["account_num"])
                logging.info(f'TOTAL ACCOUNTS: {len(accounts)}  FAILED: {len(failed)}  TERMINALS: {len(all_terms)}')
                if failed:
                    logging.warning(f"Failed accounts: {failed}")

                # Load last known good dataset
                try:
                    with open('./_1.allterms.json', 'r') as f:
                        existing = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    existing = []

                # Preserve terminals from failed accounts; replace all others with fresh data
                failed_set = set(failed)
                stale = [t for t in existing if t.get('accountNumber') in failed_set]
                merged = all_terms + stale
                logging.info(f'MERGED TERMINALS: {len(merged)} ({len(stale)} preserved from {len(failed)} failed accounts)')

                with open('./_1.allterms.json', 'w') as file:
                    json.dump(merged, file, indent=4, ensure_ascii=False)

                # Convert data to CEF
                cef_conversion = config['cef']['enable']
                if cef_conversion and merged:
                    cef_headers = config['cef']['headers1']
                    cef_messages = comlibv3.data_to_cef(merged, cef_headers)
                    with open('./_2.cef.log', 'w', encoding='utf-8') as file:
                        file.write(cef_messages)

                # Send CEF messages to connection proxy
                forward_data = config['remote_server']['enable']
                if forward_data and cef_messages:
                    proxy_addr = config['remote_server']['remote_addr']
                    proxy_port = config['remote_server']['remote_port']
                    logging.info(f"Sending messages to channel {proxy_port}...")
                    comlibv3.send_events_over_udp(cef_messages, proxy_addr, proxy_port)

                logging.info("Waiting for next cycle to run...")
                logging.info("========================================")
                time.sleep(60)
            except Exception as e:
                logging.error(f"Error getting Starshield terminals data (Main): {str(e)}")
                logging.info("Restarting process...")
                time.sleep(3)

    except KeyboardInterrupt:
        logging.info("Stopped by user.")
