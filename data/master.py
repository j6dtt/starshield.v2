import json
import logging
import os
import sys
import time
import docker
import comlibv3
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

CONFIG_PATH    = './config.json'
WORKER_IMAGE   = 'python:3.13-slim-u9'
WORKER_TIMEOUT = 30


def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)['config']


def run_worker(docker_client, account, grant_type, request_timeout, client_secret, host_data_path):
    account_num = account['account_num']
    container = None
    try:
        container = docker_client.containers.run(
            WORKER_IMAGE,
            name=f'starshield.v2.{account_num}',
            command=['python3', '/app/worker.py'],
            environment={
                'ACCOUNT_JSON':    json.dumps(account),
                'CLIENT_SECRET':   client_secret,
                'GRANT_TYPE':      grant_type,
                'REQUEST_TIMEOUT': str(request_timeout),
            },
            volumes={host_data_path: {'bind': '/app', 'mode': 'ro'}},
            working_dir='/app',
            detach=True,
            stdout=True,
            stderr=False,
        )
        result   = container.wait(timeout=WORKER_TIMEOUT)
        output   = container.logs(stdout=True, stderr=False)
        exit_code = result['StatusCode']
        if exit_code != 0:
            logging.error(f'{account_num} - worker exited with code {exit_code}')
            return (account_num, None)
        last_line = output.decode('utf-8', errors='replace').strip().splitlines()[-1]
        terms = json.loads(last_line)
        account_name = terms[0]['accountName'] if terms else ''
        sl_count = len(set(t['serviceLineNumber'] for t in terms))
        label = f"{account_num} ({account_name})" if account_name else account_num
        logging.info(f"{label} - total terminals: {len(terms)}, total service lines: {sl_count}")
        return (account_num, terms)
    except docker.errors.APIError as e:
        logging.error(f'{account_num} - Docker API error: {e}')
    except json.JSONDecodeError as e:
        logging.error(f'{account_num} - could not parse worker stdout: {e}')
    except Exception as e:
        logging.error(f'{account_num} - worker error: {e}')
    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass
    return (account_num, None)


if __name__ == '__main__':
    client_secret  = os.getenv('CLIENT_SECRET', '')
    host_data_path = os.getenv('HOST_DATA_PATH', '')

    if not host_data_path:
        logging.error('HOST_DATA_PATH env var is not set.')
        sys.exit(1)

    try:
        docker_client = docker.DockerClient(base_url='unix://var/run/docker.sock', max_pool_size=100)
        docker_client.ping()
    except Exception as e:
        logging.error(f'Cannot reach Docker daemon: {e}')
        sys.exit(1)

    while True:
        cycle_start = time.time()
        try:
            config          = load_config()
            authentication  = config['authentication']
            grant_type      = authentication['grant_type']
            request_timeout = config['request_timeout']

            seen, accounts = set(), []
            for acct in authentication['accounts']:
                num = acct['account_num']
                if num in seen:
                    logging.warning(f'{num} - duplicate, skipping.')
                    continue
                if acct.get('accountquery', {}).get('mode') == 'skip':
                    logging.info(f'{num} - mode=skip, skipping.')
                    continue
                seen.add(num)
                accounts.append(acct)

            logging.info(f'Starting parallel cycle — {len(accounts)} accounts.')

            all_terms, failed = [], []
            with ThreadPoolExecutor(max_workers=len(accounts)) as executor:
                futures = {
                    executor.submit(
                        run_worker,
                        docker_client, acct, grant_type, request_timeout,
                        client_secret, host_data_path
                    ): acct['account_num']
                    for acct in accounts
                }
                for future in as_completed(futures):
                    account_num, terms = future.result()
                    if terms is not None:
                        all_terms.extend(terms)
                    else:
                        failed.append(account_num)

            logging.info(f'TOTAL ACCOUNTS: {len(accounts)}  FAILED: {len(failed)}  TERMINALS: {len(all_terms)}')
            if failed:
                logging.warning(f'Failed accounts: {failed}')

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

            with open('./_1.allterms.json', 'w') as f:
                json.dump(merged, f, indent=4, ensure_ascii=False)

            cef_messages = ''
            if config['cef']['enable'] and merged:
                cef_messages = comlibv3.data_to_cef(merged, config['cef']['headers1'])
                with open('./_2.cef.log', 'w', encoding='utf-8') as f:
                    f.write(cef_messages)

            if config['remote_server']['enable'] and cef_messages:
                comlibv3.send_events_over_udp(
                    cef_messages,
                    config['remote_server']['remote_addr'],
                    config['remote_server']['remote_port']
                )

        except Exception as e:
            logging.error(f'Master cycle error: {e}')

        elapsed   = time.time() - cycle_start
        sleep_for = max(0, 60 - elapsed)
        logging.info(f'Cycle took {elapsed:.1f}s — sleeping {sleep_for:.1f}s.')
        logging.info('=' * 48)
        time.sleep(sleep_for)
