import requests
import json
import h3
from datetime import datetime, timezone, timedelta
from requests.packages.urllib3.exceptions import InsecureRequestWarning
import logging
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

# ------------------- Helpers --------------------
def convert_seconds_to_dhms(seconds):
    days = seconds // (24 * 3600)
    seconds %= 24 * 3600
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return days, hours, minutes, seconds

# ------------------------------------------------

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
            "client_secret": "C3ntC0mSt@rshieldAPI", #account["client_secret"],
            "grant_type": grant_type
        }
        token_resp = requests.post(
            "https://api.starlink.com/auth/connect/token",
            data=token_body
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

def get_starshield_data(headers, account):
    try:
        # --------------- Get account info ----------------
        getAccount = requests.get(
            "https://starlink.com/api/public/v2/account",
            headers=headers
        ).json()
        accountNumber = getAccount["content"]["accountNumber"]
        accountName = getAccount["content"]["accountName"]
    except Exception as e:
        logging.error(f"{account['account_num']} - /account failed: {e}")
        return []

    try:
        # ------- Get contact for Component info ----------
        getContacts = requests.get(
            "https://starlink.com/api/public/v2/contacts",
            headers=headers
        ).json()

        component = next(
            (
                item['email'].split('__', 1)[1].split('@', 1)[0].upper()
                for item in getContacts.get("content", {}).get("results", [])
                if item.get('email', '').startswith('__')
            ),
            ""
        )
    except Exception as e:
        logging.error(f"{account['account_num']} - /contacts failed: {e}")
        return []

    try:
        # ------------ Get all terminals --------------
        active_terms = []
        allterms = []
        i = 0
        mode = account['accountquery']['mode']
        if mode == 'skip':
            logging.warning(f"{account['account_num']} - mode is 'skip', skipping account.")
            return []
        elif mode == 'include' and not account['accountquery']['service_lines']:
            logging.warning(f"{account['account_num']} - mode is 'include' but service_lines is empty, skipping account.")
            return []
        elif mode not in ('full', 'include'):
            logging.error(f"{account['account_num']} - invalid accountquery mode '{mode}', skipping.")
            return []

        sl_query = ""
        if mode == 'include':
            for svcline in account['accountquery']['service_lines']:
                sl_query += f"serviceLineNumbers={svcline}&"

        while True:
            if mode == 'full':
                termurl = f"https://starlink.com/api/public/v2/user-terminals?page={i}"
            else:
                termurl = f"https://starlink.com/api/public/v2/user-terminals?{sl_query}page={i}"
            getAllTerms = requests.get(
                termurl,
                headers=headers
            ).json()
            metadata = getAllTerms["content"]
            Terms = getAllTerms["content"]["results"]
            for term in Terms:
                selected_columns = {
                    'userTerminalId': term['userTerminalId'],
                    'kitSerialNumber': term['kitSerialNumber'],
                    'dishSerialNumber': term['dishSerialNumber'],
                    'serviceLineNumber': term['serviceLineNumber']
                }
                allterms.append(selected_columns)
            i += 1
            if metadata['isLastPage']:
                break
    except Exception as e:
        logging.error(f"{account['account_num']} - /user-terminals failed: {e}")
        return []

    # -------------- Create dataset --------------
    for term in allterms:
        term['accountNumber'] = accountNumber
        term['accountName'] = (accountName).strip()
        term['component'] = component
        term['terminalName'] = f"{component}-{term['kitSerialNumber'][-5:]}" if component != "" else ""
        term['status'] = 0
        term['UtcTimestampNs'] = ''
        term['lastOnline'] = ''
        term['DownlinkThroughput'] = 0
        term['UplinkThroughput'] = 0
        term['PingDropRateAvg'] = 0
        term['PingLatencyMsAvg'] = 0
        term['ObstructionPercentTime'] = 0
        term['Uptime'] = 0
        term['SignalQuality'] = 0
        term['H3toGeo'] = ''
        term['dataPlan'] = ''
        term['PlanStartDate'] = ''
        term['PlanEndDate'] = ''
        term['billingCycleStartDate'] = ''
        term['billingCycleEndDate'] = ''
        term['usageLimitGB'] = 0
        term['currentTotalUsageGB'] = 0
        term['usageOverageAmountGB'] = 0
        term['usageOveragePrice'] = 0
        term['usageOveragePricePerGB'] = 0
        term['usageLastUpdated'] = ''
        term['ipv4'] = ''
        term['iptimestamp'] = ''
        term['Alert'] = ''
        term['AlertDescription'] = ''
        active_terms.append(term)

    # -------------- Get all servicelines ---------------
    #i = 0
    sl = []    # service lines list
    for term in active_terms:
        if term['serviceLineNumber']:
            sl.append(term['serviceLineNumber'])
    """
    while True:
        getAllServiceLines = requests.get(
            f"https://starlink.com/api/public/v2/service-lines?page={i}&orderByCreatedDateDescending=true",
            headers=headers
        ).json()
        metadata = getAllServiceLines["content"]
        allserviceLines = getAllServiceLines["content"]["results"]

        # Update dataset with service lines
        for line in allserviceLines:
            for term in active_terms:
                if term['serviceLineNumber'] == line['serviceLineNumber']:
                    term['dataPlan'] = line['productReferenceId']
                    term['PlanStartDate'] = line['startDate']
                    term['PlanEndDate'] = line ['endDate']
            sl.append(line['serviceLineNumber'])
        i += 1
        if metadata['isLastPage']:
            break
    """

    try:
        # ----------------- Get Data Usage -----------------

        # data usage query body
        payload = {
            "serviceLineNumbers": sl,
            "previousBillingCycles": 0
        }

        i = 0
        while True:
            getAllUsages = requests.post(
                f"https://starlink.com/api/public/v2/data-usage/query?page={i}&limit=250",
                json=payload,
                headers=headers
            ).json()
            metadata = getAllUsages["content"]
            allUsages = getAllUsages["content"]["results"]
            for usage in allUsages:
                for term in active_terms:
                    if term['serviceLineNumber'] == usage['serviceLineNumber']:
                        term['dataPlan'] = usage['servicePlan']['productId']
                        term['PlanStartDate'] = usage['servicePlan']['subscriptionActiveFrom']
                        term['PlanEndDate'] = usage['servicePlan']['subscriptionEndDate']
                        term['billingCycleStartDate'] = usage['startDate']
                        term['billingCycleEndDate'] = usage['endDate']
                        term['usageLimitGB'] = usage['servicePlan']['overageLine']['usageLimitGB'] if usage['servicePlan']['overageLine'] else usage['servicePlan']['usageLimitGB']
                        term['currentTotalUsageGB'] = round(usage['servicePlan']['overageLine']['consumedAmountGB'], 2) if usage['servicePlan']['overageLine'] else usage['billingCycles'][0]['totalPriorityGB']
                        term['usageOverageAmountGB'] = round(usage['servicePlan']['overageLine']['overageAmountGB'], 2) if usage['servicePlan']['overageLine'] else 0
                        term['usageOveragePrice'] = usage['servicePlan']['overageLine']['overagePrice'] if usage['servicePlan']['overageLine'] else 0
                        term['usageOveragePricePerGB'] = usage['servicePlan']['overageLine']['pricePerGB'] if usage['servicePlan']['overageLine'] else 0
                        term['usageLastUpdated'] = usage['lastUpdated']
            i += 1
            if metadata['isLastPage']:
                break
    except Exception as e:
        logging.error(f"{accountNumber} - /data-usage failed: {e}")

    try:
        # ---------------- Get Telemetry -------------------
        # Generate user terminal ids list
        ut = []    # terminal ids list
        for term in active_terms:
            if term['serviceLineNumber']:
                ut.append(term["userTerminalId"])

        # Telemetry query body
        body = {
            "includeUserTerminals": True,
            "userTerminalIds": ut
        }

        # Get last telemetry
        telemetry = requests.post(
            "https://starlink.com/api/public/v2/telemetry/query",
            headers=headers,
            json=body
        ).json()

        # Extract telemetry values
        lastTelemetry = list(telemetry["content"]["userTerminals"].values())

        #with open('./_structured_lasttelemetry.json', 'w') as file:
        #    json.dump(lastTelemetry, file, indent=4, ensure_ascii=False)

        # Update dataset with telemetry
        for last in lastTelemetry:
            for term in active_terms:
                alerts = []
                alertcodes = []
                alertdes = []
                if term['userTerminalId'] == last['userTerminalId']:
                    ts = datetime.fromisoformat(last['timestamp'])
                    now = datetime.now(timezone.utc)
                    if ts >= now - timedelta(seconds=60):
                        term['status'] = 1
                        term['UtcTimestampNs'] = last['timestamp']
                        term['lastOnline'] = last['timestamp']
                    else:
                        term['status'] = 0
                        term['UtcTimestampNs'] = ''
                        term['lastOnline'] = last['timestamp']
                    term['DownlinkThroughput'] = round(last['downlinkThroughputMbps'], 2)
                    term['UplinkThroughput'] = round(last['uplinkThroughputMbps'], 2)
                    term['PingDropRateAvg'] = round(last['popPingDropRateAvg'] * 100, 3)
                    term['PingLatencyMsAvg'] = last['popPingLatencyMsAvg']
                    term['ObstructionPercentTime'] = round(last['obstructionPercentTime'], 3)
                    days, hours, minutes, seconds = convert_seconds_to_dhms(last['uptimeSeconds'])
                    uptime = f"{days}d {hours}h {minutes}m {seconds}s"
                    term['Uptime'] = uptime
                    term['SignalQuality'] = last['signalQuality'] * 100
                    if len(str(last['h3CellId'])) == 15:
                        cell = h3.cell_to_latlng(last['h3CellId'])
                    else:
                        cell = ''
                    term['H3toGeo'] = str(cell).strip('()')
                    if last['ipAllocations']:
                        ipv4 = last.get('ipAllocations', {}).get('ipv4')
                        if isinstance(ipv4, list) and ipv4:
                            term['ipv4'] = ipv4[0]
                        else: term['ipv4'] = ''
                        term['iptimestamp'] = last['ipAllocations']['timestamp']
                    if last['alertSoftwareUpdateRebootPending']:
                        alerts.append('alertSoftwareUpdateRebootPending')
                        alertcodes.append('82')
                    if last['alertDataOverageRateLimited']:
                        alerts.append('alertDataOverageRateLimited')
                        alertcodes.append('97')
                    if last['alertEthernetSlowLink10']:
                        alerts.append('alertEthernetSlowLink10')
                        alertcodes.append('50')
                    if last['alertEthernetSlowLink100']:
                        alerts.append('alertEthernetSlowLink100')
                        alertcodes.append('51')
                    if last['alertPsuOtpThrottling']:
                        alerts.append('alertPsuOtpThrottling')
                        alertcodes.append('52')
                    if last['alertPopChange']:
                        alerts.append('alertPopChange')
                        alertcodes.append('62')
                    if last['alertActuatorMotorStuck']:
                        alerts.append('alertActuatorMotorStuck')
                        alertcodes.append('53')
                    if last['alertMastNotVertical']:
                        alerts.append('alertMastNotVertical')
                        alertcodes.append('54')
                    if last['alertUnableToAlign']:
                        alerts.append('alertUnableToAlign')
                        alertcodes.append('63')
                    if last['alertHighTimeObstruction']:
                        alerts.append('alertHighTimeObstruction')
                        alertcodes.append('66')
                    if last['alertDisabledNoActiveServiceLine']:
                        alerts.append('alertDisabledNoActiveServiceLine')
                        alertcodes.append('55')
                    if last['alertDisabledTooFarFromServiceAddress']:
                        alerts.append('alertDisabledTooFarFromServiceAddress')
                        alertcodes.append('56')
                    if last['alertDisabledNoServiceInOcean']:
                        alerts.append('alertDisabledNoServiceInOcean')
                        alertcodes.append('57')
                    if last['alertDisabledBlockedCountry']:
                        alerts.append('alertDisabledBlockedCountry')
                        alertcodes.append('83')
                    if last['alertDisabledMovingTooFast']:
                        alerts.append('alertDisabledMovingTooFast')
                        alertcodes.append('60')
                    if last['alertDisabledDataUsageExceededQuota']:
                        alerts.append('alertDisabledDataUsageExceededQuota')
                        alertcodes.append('84')
                    if last['alertDisabledCellIsDisabled']:
                        alerts.append('alertDisabledCellIsDisabled')
                        alertcodes.append('89')
                    if last['alertDisabledRoamRestricted']:
                        alerts.append('alertDisabledRoamRestricted')
                        alertcodes.append('93')
                    if last['alertDisabledUnknownLocation']:
                        alerts.append('alertDisabledUnknownLocation')
                        alertcodes.append('94')
                    if last['alertDisabledAccountDisabled']:
                        alerts.append('alertDisabledAccountDisabled')
                        alertcodes.append('95')
                    if last['alertDisabledUnsupportedSoftware']:
                        alerts.append('alertDisabledUnsupportedSoftware')
                        alertcodes.append('96')
                    term['Alert'] = (str((alertcodes)).strip("[']")).replace("', '","-")
                    term['AlertDescription'] = (str((alerts)).strip("[']")).replace("', '","-")
    except Exception as e:
        logging.error(f"{accountNumber} - /telemetry failed: {e}")

    logging.info(f"{accountNumber} - total terminal: {len(active_terms)}, total service lines: {len(sl)}")
    return active_terms


if __name__ == "__main__":
    config = load_config()
    authentication = config["authentication"]
    grant_type = authentication["grant_type"]
    all_terms = []
    for account in authentication["accounts"]:
        try:
            headers = get_auth_headers(account, grant_type)
            terms = get_starshield_data(headers, account)
            all_terms.extend(terms)
        except Exception as e:
            logging.error(f"{account['account_num']} - skipped: {e}")
    logging.info(f'TOTAL ACCOUNTS: {len(authentication["accounts"])}')
    logging.info(f"TOTAL TERMINALS: {len(all_terms)}")
    with open('./_allterms.json', 'w') as file:
        json.dump(all_terms, file, indent=4, ensure_ascii=False)
