"""
main_api.py — Adaptación de main.py para la API
================================================
create_account_api() ejecuta el flujo completo y retorna:
{
    "phone":      "12137299305",
    "password":   "dfbc1992",
    "name":       "John Doe",
    "cookies":    "session-id=...; session-token=\"...\"; x-main=\"...\"",
    "cookie_raw": { "session-id": "...", ... }
}
Lanza excepción si falla.
"""

import os, re, sys, json, time, base64, random
import urllib3
import curl_cffi
import capsolver
from faker import Faker
from bs4 import BeautifulSoup
from logger import beautiful_logger
from generator import generate_spoofed_auth
from dotenv import load_dotenv

load_dotenv()

logger = beautiful_logger("amazon_gen")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

FAKE    = Faker()
_PARSER = "lxml"

_OTP_TEXTS = (
    "verification code", "codigo de verificacion",
    "Enter the OTP", "Enter the code", "We texted you",
    "We sent a code", "check your phone", "SMS",
)
_OTP_FIELD_PATTERNS = ("otp", "code", "pin", "cvf_captcha_input", "verificationCode")
_SUCCESS_URLS = (
    "primevideo", "amazon.com/gp", "amazon.com/?ref",
    "amazon.com/ref", "www.amazon.com/",
)
_OTP_URL_PATTERNS = ("otp", "cvf", "verify", "code", "auth")

capsolver.api_key = os.getenv("CAPSOLVER_KEY")
PROXY_URL = os.getenv("REQ_PROXY")
PASSWORD  = "dfbc1992"

# ── HELPERS ───────────────────────────────────────────────────────────────────
def find_between(data, first, last):
    s = data.find(first)
    if s == -1: return None
    s += len(first)
    e = data.find(last, s)
    if e == -1: return None
    return data[s:e]

def bs_val(html, name, default=None):
    el = html.find("input", {"name": name})
    if el: return el.get("value", default or "")
    return default or ""

def all_inputs(html) -> dict:
    result = {}
    for inp in html.find_all("input"):
        name = inp.get("name")
        if name: result[name] = inp.get("value", "")
    return result

def detect_otp_page(html_obj, url):
    url_lower = url.lower()
    is_pv_page = "/ap/pv" in url_lower
    otp_url = any(x in url_lower for x in _OTP_URL_PATTERNS)
    otp_fields = []
    for inp in html_obj.find_all("input"):
        name = (inp.get("name") or "").lower()
        typ  = (inp.get("type") or "text").lower()
        if typ == "hidden": continue
        if any(x in name for x in _OTP_FIELD_PATTERNS):
            otp_fields.append(inp.get("name"))
    page_text = html_obj.get_text().lower()
    otp_text = any(x.lower() in page_text for x in _OTP_TEXTS)
    return (is_pv_page or otp_url or otp_text or bool(otp_fields)), otp_fields

def extract_form_data(html_obj, url, form_id=None):
    form = html_obj.find("form", {"id": form_id}) if form_id else html_obj.find("form")
    if not form: return None, {}
    action = form.get("action", "")
    if action and not action.startswith("http"):
        from urllib.parse import urljoin
        action = urljoin(url, action)
    inputs = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if name: inputs[name] = inp.get("value", "")
    return action, inputs

def extract_resend_url(html_obj):
    for script in html_obj.find_all("script"):
        if script.string and "resendUrl" in script.string:
            m = re.search(r'"resendUrl":"([^"]+)"', script.string)
            if m: return m.group(1)
    return None

def build_cookie_str(session) -> tuple[str, dict]:
    """
    Extraer cookies de la sesión, agregar comillas a session-token y x-main,
    eliminar espacios y retornar (cookie_str, cookie_dict).
    """
    raw = session.cookies.get_dict(domain=None, path=None)
    processed = {}
    for k, v in raw.items():
        if k in ("session-token", "x-main"):
            processed[k] = f'"{v}"'
        else:
            processed[k] = v
    cookie_str = "; ".join(f"{k}={v}" for k, v in processed.items())
    # Quitar todos los espacios del string resultante
    cookie_str = cookie_str.replace(" ", "")
    return cookie_str, processed


# ── HeroSMS ───────────────────────────────────────────────────────────────────
class HeroSMS:
    def __init__(self, session):
        self.session         = session
        self.activation_id   = None
        self.phone           = None

    def _api(self, action, **params):
        params["api_key"] = os.getenv("HEROSMS_KEY")
        params["action"]  = action
        for attempt in range(2):
            try:
                r = self.session.get(
                    "https://hero-sms.com/stubs/handler_api.php",
                    params=params, timeout=20,
                )
                try:    return r.json()
                except: return r.text
            except Exception as e:
                if attempt == 1: raise
                time.sleep(0.5)

    def get_number(self, service="am"):
        country = int(os.getenv("HEROSMS_COUNTRY", "187"))
        logger.info(f"[HEROSMS] Requesting number: service={service} country={country}")
        result = self._api("getNumberV2", service=service, country=country)
        if isinstance(result, dict) and "activationId" in result:
            self.activation_id = result["activationId"]
            self.phone         = result["phoneNumber"]
            logger.info(f"  [OK] id={self.activation_id} phone={self.phone}")
            return True
        logger.info(f"  [ERROR] {result}")
        return False

    def poll_code(self, timeout=120, interval=2):
        logger.info(f"[HEROSMS] Polling SMS code (timeout={timeout}s)...")
        start = time.time()
        while time.time() - start < timeout:
            status = self._api("getStatusV2", id=self.activation_id)
            logger.info(f"  status={status}")
            raw = status.get("raw") if isinstance(status, dict) else str(status)
            if raw == "STATUS_WAIT_CODE":
                time.sleep(interval); continue
            if raw == "STATUS_WAIT_RETRY":
                self._api("setStatus", id=self.activation_id, status=3)
                time.sleep(interval); continue
            if raw == "STATUS_CANCEL":
                return None
            sms  = status.get("sms") if isinstance(status, dict) else None
            code = sms.get("code") if isinstance(sms, dict) else None
            if code:
                logger.info(f"  [OK] Code: {code}")
                return code
            time.sleep(interval)
        return None

    def finish(self):
        if self.activation_id:
            self._api("finishActivation", id=self.activation_id)

    def cancel(self):
        if self.activation_id:
            self._api("cancelActivation", id=self.activation_id)


# ── FUNCIÓN PRINCIPAL ─────────────────────────────────────────────────────────
def create_account_api() -> dict:
    """
    Ejecuta el flujo completo de creación de cuenta.
    Retorna dict con phone, password, name, cookies, cookie_raw.
    Lanza RuntimeError si falla.
    """
    UA = "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

    first_name = FAKE.first_name()
    last_name  = FAKE.last_name()

    # Sesión curl_cffi
    s = curl_cffi.Session(impersonate="chrome")
    s.trust_env = False
    if PROXY_URL:
        s.proxies = {"http": PROXY_URL, "https": PROXY_URL}

    def req(method, url, **kw):
        kw.setdefault("timeout", 20)
        for attempt in range(2):
            try:
                return s.request(method, url, **kw)
            except Exception as e:
                if attempt == 1: raise
                time.sleep(0.5 * (attempt + 1))

    herosms = HeroSMS(s)

    # ── PASO 1 ─────────────────────────────────────────────────────────────────
    logger.info("[1] GET /ap/signin...")
    r1 = req("GET", "https://www.amazon.com/ap/signin",
        headers={
            "User-Agent": UA, "Upgrade-Insecure-Requests": "1",
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Linux"',
            "device-memory": "8", "sec-ch-device-memory": "8",
            "dpr": "1", "sec-ch-dpr": "1",
            "viewport-width": "548", "sec-ch-viewport-width": "548",
            "ect": "3g", "rtt": "400", "downlink": "1.35",
        },
        params={
            "showRememberMe": "true", "openid.pape.max_auth_age": "0",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "siteState": "135-4789514-8844217", "language": "en_US",
            "pageId": "amzn_prime_video_ww",
            "openid.return_to": "https://na.primevideo.com/auth/return/ref=av_auth_ap?_t=placeholder&location=/",
            "prevRID": "AQDQ2AF57Y6W3GM45ABJ",
            "openid.assoc_handle": "amzn_prime_video_sso_us",
            "openid.mode": "checkid_setup", "prepopulatedLoginId": "",
            "failedSignInCount": "0",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.ns": "http://specs.openid.net/auth/2.0",
        },
    )
    logger.info(f"  status={r1.status_code}")

    h1         = BeautifulSoup(r1.text, _PARSER)
    siteState1 = bs_val(h1, "siteState")
    returnTo1  = bs_val(h1, "openid.return_to")
    prevRID1   = bs_val(h1, "prevRID")

    # ── PASO 2 ─────────────────────────────────────────────────────────────────
    logger.info("[2] GET /ap/register...")
    r2 = req("GET", "https://www.amazon.com/ap/register",
        headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9", "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
            "Pragma": "no-cache", "Cache-Control": "no-cache",
        },
        params={
            "showRememberMe": "true", "openid.pape.max_auth_age": "0",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "siteState": siteState1, "language": "en_US",
            "pageId": "amzn_prime_video_ww",
            "openid.return_to": returnTo1, "prevRID": prevRID1,
            "openid.assoc_handle": "amzn_prime_video_sso_us",
            "openid.mode": "checkid_setup", "prepopulatedLoginId": "",
            "failedSignInCount": "0",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.ns": "http://specs.openid.net/auth/2.0",
        },
    )
    logger.info(f"  status={r2.status_code}")

    h2              = BeautifulSoup(r2.text, _PARSER)
    appActionToken2 = bs_val(h2, "appActionToken")
    return_to2      = bs_val(h2, "openid.return_to")
    prevRID2        = bs_val(h2, "prevRID")
    siteState2      = bs_val(h2, "siteState")
    workflowState2  = bs_val(h2, "workflowState")
    csrf2           = bs_val(h2, "anti-csrftoken-a2z")

    # ── PASO 2.5 — HeroSMS ─────────────────────────────────────────────────────
    if not herosms.get_number():
        raise RuntimeError("Failed to get HeroSMS number")

    # ── PASO 3 — POST /ap/register ─────────────────────────────────────────────
    logger.info("[3] POST /ap/register...")
    data_json = generate_spoofed_auth(
        user_agent=UA, location=r2.url,
        email=herosms.phone, name=f"{first_name} {last_name}",
        password=PASSWORD,
        html_b64=base64.b64encode(r2.text.encode("utf-8")).decode("utf-8"),
    )
    fp_amz        = data_json.metadata1
    encrypted_pwd = data_json.encrypted_pwd

    # Fallback: lista hardcodeada si encrypted_pwd está vacío
    if not encrypted_pwd:
        passwords = [
            "AYAAFHtMjg0m8dFzdfdfXbYsbMcAAAABAAZzaTptZDUAIDk3MzkwMGFkZGIwNjFmYmU1YmI0ZWE4NzFlOWQ4MTYxAQCi6X/1M6Zr1dn9EWQ7/02Je++VREFWrqaMIlQViT94RHNbRRmCeZlx14XnmwQfHNwYm6z8Tchq8e+Qt+ARlsnQT4gYGqyHOBN+tpv8G6LklNtEGbILENZCio63RQgFL4+pX7c6A4Ntp/K3JIhe9iZEGua7FeBtLJudofTgD3SkBLU9TtpsNsoIi037DrajpLxktt9HHfLArys3fNNG2rig27ityg0+Ril14FaeGlKCzv2E7fsLPWQY0UeJYcDeHUlHD7StPz1jrJLQrYoCZvAa3EivQbnH+Y+PIRiy40CF5cVU8h5TjCq0WJKuQQEvrIcMzsDO2YISY/cEoIF9EDOGAgAAAAAMAAAACQAAAAAAAAAAAAAAAI9jr/feb7Rl1PMY2r/Kw1T/////AAAAAQAAAAAAAAAAAAAAAQAAAAjGC3HuggWEnXynhfdnu1h1R8tZH47PRR8=",
            "AYAAFC8mk1qm7agMTAWvY0CniRgAAAABAAZzaTptZDUAIDk3MzkwMGFkZGIwNjFmYmU1YmI0ZWE4NzFlOWQ4MTYxAQCDIh1zw8fXzP4eGVlSMxZsc0fChWXOq0zlEX9WKxQOEXfWnL0BrpWsomGui5+2pMJW+pwjXUt5fUpfETa3+daiv6MjNJuxcBwGJOwvVuz+gPz7m4Cq0zb6MvMc/Nmj1Ekfw9qTTqqgEq1ZpBP9iL18QyKo0ukjAFbPOa32lHtct1Jdm2f0OFDQ3hJQwcl1HF139/AH74PVjCVaW6+ErJ++W9XScz9XEW8hlITe2Ym6Er8JRa9zFxW8mc89s6154IiWc6muIPPwxKvc6UufQGYCKB5KiQinmddHy2iX4eb/PoHXHe6DrZSFGb9Bw1iqnQhlV5LoRk/3FmZP3tlP+rGmAgAAAAAMAAAACQAAAAAAAAAAAAAAAEHM9he/Xd8RQNFTecPdUkL/////AAAAAQAAAAAAAAAAAAAAAQAAAAjgv2QNxHhauWeM/+6tYQfn1fK87TJauoI=",
        ]
        encrypted_pwd = random.choice(passwords)
        logger.warning("  [WARN] Using hardcoded encrypted_pwd fallback")

    r3 = req("POST", "https://www.amazon.com/ap/register",
        headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.amazon.com", "Referer": r2.url,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
            "Pragma": "no-cache", "Cache-Control": "no-cache",
        },
        data={
            "appActionToken": appActionToken2, "appAction": "REGISTER",
            "openid.return_to": return_to2, "prevRID": prevRID2,
            "siteState": siteState2, "workflowState": workflowState2,
            "anti-csrftoken-a2z": csrf2,
            "customerName": f"{first_name} {last_name}",
            "countryCode": "US", "email": herosms.phone,
            "encryptedPwd": encrypted_pwd, "metadata1": fp_amz,
            "encryptedPasswordExpected": "",
        },
    )
    logger.info(f"  status={r3.status_code}  url={r3.url}")

    h3   = BeautifulSoup(r3.text, _PARSER)
    csrf3 = bs_val(h3, "anti-csrftoken-a2z") or csrf2

    data_context_list = re.findall(r'"data-context":\s*\'({[^\']*})\'', r3.text)
    data_context = data_context_list[0] if data_context_list else None
    if not data_context:
        data_context_list = re.findall(r'data-context="({[^"]*})"', r3.text)
        data_context = data_context_list[0] if data_context_list else None

    data_ext_id = find_between(r3.text, '"data-external-id": "', '"') or \
                  find_between(r3.text, 'data-external-id="', '"')

    clientContext = bs_val(h3, "clientContext")
    verifyToken   = bs_val(h3, "verifyToken")
    siteState3    = bs_val(h3, "siteState") or siteState2
    return_to3    = (bs_val(h3, "openid.return_to") or return_to2).replace("&amp;", "&")

    cvf_form_action = find_between(r3.text, 'id="cvf-aamation-challenge-form" method="post" action="', '"')
    if not cvf_form_action:
        m = re.search(r'action="(/[^"]*cvf[^"]*)"', r3.text, re.IGNORECASE)
        cvf_form_action = m.group(1) if m else "/ap/cvf/verify"

    if not data_context:
        raise RuntimeError("No data-context found in step 3 response")

    # ── PASO 4 ─────────────────────────────────────────────────────────────────
    logger.info("[4] GET /aaut/verify/cvf...")
    options4 = json.dumps({
        "clientData": data_context, "challengeType": "WAF_ADVERSARIAL_SYNTHETIC_GRID_V2_LEVEL_1",
        "locale": "en-US", "externalId": data_ext_id,
        "enableHeaderFooter": False, "enableBypassMechanism": False,
        "enableModalView": False, "eventTrigger": None,
        "aaExternalToken": None, "forceJsFlush": False, "aamationToken": None,
    }, separators=(",", ":"))

    r4 = req("GET", "https://www.amazon.com/aaut/verify/cvf",
        headers={
            "User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json", "Connection": "keep-alive",
            "Referer": r3.url, "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
            "Pragma": "no-cache", "Cache-Control": "no-cache",
        },
        params={"options": options4},
    )
    logger.info(f"  status={r4.status_code}")

    ctx4 = {}
    raw4 = r4.headers.get("amz-aamation-resp")
    if raw4:
        try: ctx4 = json.loads(raw4)
        except: pass

    session_token4   = ctx4.get("sessionToken", "")
    client_side_ctx4 = ctx4.get("clientSideContext", "")
    problem_version  = find_between(r4.text, '"problem":"', '"')
    captcha_id       = find_between(r4.text, '"id":"', '"')
    captcha_url      = find_between(r4.text, '<script src="', '"')
    captcha_domain   = find_between(captcha_url, "https://", "/ait/") if captcha_url else None

    # ── PASO 5-7 — Captcha (con reintentos) ────────────────────────────────────
    captcha_voucher = None
    for attempt in range(1, 4):
        logger.info(f"\n[Intento {attempt}/3] Resolviendo captcha...")

        r5 = req("GET", f"https://{captcha_domain}/ait/ait/ait/problem",
            headers={
                "User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.amazon.com/", "Origin": "https://www.amazon.com",
                "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site", "Pragma": "no-cache", "Cache-Control": "no-cache",
            },
            params={
                "kind": "visual", "domain": "www.amazon.com", "locale": "en-us",
                "problem": problem_version, "num_solutions_required": "1", "id": captcha_id,
            },
        )

        prob      = r5.json()
        assets    = prob.get("assets") or {}
        target_raw= assets.get("target", "")
        images_raw= assets.get("images", "[]")
        hmac_tag  = prob.get("hmac_tag", "")
        state5    = prob.get("state") or {}
        iv5       = state5.get("iv", "")
        payload5  = state5.get("payload", "")
        key5      = prob.get("key", "")

        images = json.loads(images_raw) if isinstance(images_raw, str) else images_raw
        target = re.sub(r'[\[\]"\']', "", target_raw).replace("_", " ").strip()

        if not images or not target:
            raise RuntimeError("No captcha images or target")

        try:
            solution = capsolver.solve({
                "type": "AwsWafClassification",
                "question": f"aws:grid:{target}",
                "images": images,
            })
        except Exception as e:
            raise RuntimeError(f"CapSolver error: {e}")

        if not solution or not solution.get("objects"):
            if attempt < 3: continue
            raise RuntimeError(f"CapSolver no objects: {solution}")

        r7 = req("POST", f"https://{captcha_domain}/ait/ait/ait/verify",
            headers={
                "User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.amazon.com/",
                "Content-Type": "text/plain;charset=UTF-8",
                "Origin": "https://www.amazon.com",
                "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
                "Pragma": "no-cache", "Cache-Control": "no-cache",
            },
            json={
                "hmac_tag": hmac_tag,
                "state": {"iv": iv5, "payload": payload5},
                "key": key5, "client_solution": solution["objects"],
                "metrics": {"solve_time_millis": random.randint(8000, 20000)},
                "locale": "en-us",
            },
        )

        r7j = r7.json()
        if r7j.get("success"):
            captcha_voucher = r7j.get("captcha_voucher", "")
            logger.info(f"  [OK] Captcha resuelto!")
            break
        if attempt >= 3:
            raise RuntimeError(f"Captcha verification failed: {r7j}")

    # ── PASO 8 ─────────────────────────────────────────────────────────────────
    logger.info("[8] GET /aaut/verify/cvf/{id}...")
    r8 = req("GET", f"https://www.amazon.com/aaut/verify/cvf/{captcha_id}",
        headers={
            "User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "cache-control": "no-cache", "pragma": "no-cache",
        },
        params={
            "context": client_side_ctx4, "options": options4,
            "response": '{"challengeType":"WAF_ADVERSARIAL_SYNTHETIC_GRID_V2_LEVEL_1","data":"\\\"' + captcha_voucher + '\\\"\"}',
        },
    )

    ctx8 = {}
    raw8 = r8.headers.get("amz-aamation-resp")
    if raw8:
        try: ctx8 = json.loads(raw8)
        except: pass

    final_session_token = ctx8.get("sessionToken", "") or session_token4

    # ── PASO 9 — POST /ap/cvf/verify ───────────────────────────────────────────
    logger.info("[9] POST /ap/cvf/verify...")
    token_t = find_between(return_to3, "ref=av_auth_ap?_t=", "&") or find_between(return_to3, "_t=", "&")
    openid_return_to = (
        f"https://na.primevideo.com/auth/return/ref=av_auth_ap?_t={token_t}&location=/?ref_%3Datv_auth_pre"
        if token_t else return_to3
    )

    r9 = req("POST",
        "https://www.amazon.com" + (cvf_form_action if cvf_form_action.startswith("/") else "/ap/cvf/verify"),
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.amazon.com", "Referer": r3.url,
            "Cache-Control": "no-cache", "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "device-memory": "8", "sec-ch-device-memory": "8",
            "dpr": "1", "sec-ch-dpr": "1",
            "viewport-width": "380", "sec-ch-viewport-width": "380",
            "ect": "4g", "rtt": "50", "downlink": "50",
        },
        data={
            "anti-csrftoken-a2z": csrf3,
            "cvf_aamation_response_token": final_session_token,
            "cvf_captcha_captcha_action": "verifyAamationChallenge",
            "cvf_aamation_error_code": "",
            "clientContext": clientContext,
            "openid.pape.max_auth_age": "0",
            "openid.return_to": openid_return_to,
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.assoc_handle": "amzn_prime_video_sso_us",
            "openid.mode": "checkid_setup",
            "siteState": siteState3,
            "language": "en_US",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "pageId": "amzn_prime_video_ww",
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "verifyToken": verifyToken,
        },
        allow_redirects=True,
    )
    logger.info(f"  status={r9.status_code}  url={r9.url}")

    # ── PASO 10 — OTP / resultado ──────────────────────────────────────────────
    h9   = BeautifulSoup(r9.text, _PARSER)
    url9 = r9.url

    is_otp, otp_fields = detect_otp_page(h9, url9)

    def get_final_cookies():
        """Hacer la última petición home y extraer cookies."""
        req("GET", "https://www.amazon.com/gp/history", headers={
            "User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.amazon.com/",
        })
        # ÚLTIMA petición: GET /ref=nav_logo (home)
        req("GET", "https://www.amazon.com/ref=nav_logo", headers={
            "User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.amazon.com/gp/history",
        })
        cookie_str, cookie_raw = build_cookie_str(s)
        return cookie_str, cookie_raw

    if is_otp:
        logger.info(f"[SMS] OTP page detectada: {url9}")
        form_action, form_inputs = extract_form_data(h9, url9, form_id="auth-pv-form")
        if not form_action:
            form_action, form_inputs = extract_form_data(h9, url9)

        otp_code = herosms.poll_code()
        if not otp_code:
            herosms.cancel()
            raise RuntimeError("No OTP received from HeroSMS")

        if not otp_fields or not form_action:
            herosms.cancel()
            raise RuntimeError(f"OTP field not found. fields={otp_fields} action={form_action}")

        logger.info(f"[11] POST OTP {otp_code}...")
        otp_data = form_inputs.copy()
        for field in otp_fields:
            otp_data[field] = otp_code
        new_csrf = bs_val(h9, "anti-csrftoken-a2z")
        if new_csrf:
            otp_data["anti-csrftoken-a2z"] = new_csrf
        otp_data["metadata1"] = ""

        r_otp = req("POST", form_action, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.amazon.com",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1",
        }, data=otp_data, allow_redirects=True)
        logger.info(f"  OTP status={r_otp.status_code}  url={r_otp.url}")

        # POST dirección
        r_addr = req("GET", "https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post", headers={
            "User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9", "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }, allow_redirects=True)

        addr_html  = BeautifulSoup(r_addr.text, _PARSER)
        csrf_token = bs_val(addr_html, "csrfToken") or new_csrf

        r_otp2 = req("POST", "https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post",
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "accept-language": "en-US,en;q=0.9", "cache-control": "no-cache",
                "content-type": "application/x-www-form-urlencoded",
                "origin": "https://www.amazon.com", "pragma": "no-cache",
                "referer": "https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post",
                "sec-fetch-dest": "document", "sec-fetch-mode": "navigate",
                "sec-fetch-site": "same-origin", "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1", "user-agent": UA,
            },
            data={
                "csrfToken": csrf_token, "addressID": "",
                "address-ui-widgets-countryCode": "US",
                "address-ui-widgets-enterAddressFullName": f"{first_name} {last_name}",
                "address-ui-widgets-enterAddressPhoneNumber": "+1" + herosms.phone,
                "address-ui-widgets-enterAddressLine1": "Street23",
                "address-ui-widgets-enterAddressLine2": "",
                "address-ui-widgets-enterAddressCity": "New York",
                "address-ui-widgets-enterAddressStateOrRegion": "NY",
                "address-ui-widgets-enterAddressPostalCode": "10081",
                "address-ui-widgets-use-as-my-default": "true",
                "address-ui-widgets-addressFormButtonText": "save",
                "address-ui-widgets-clientName": "YourAccountAddressBook",
                "address-ui-widgets-previous-address-form-state-token": bs_val(addr_html, "address-ui-widgets-previous-address-form-state-token"),
                "address-ui-widgets-obfuscated-customerId": bs_val(addr_html, "address-ui-widgets-obfuscated-customerId"),
                "address-ui-widgets-csrfToken": bs_val(addr_html, "address-ui-widgets-csrfToken"),
                "address-ui-widgets-form-load-start-time": bs_val(addr_html, "address-ui-widgets-form-load-start-time"),
                "address-ui-widgets-clickstream-related-request-id": bs_val(addr_html, "address-ui-widgets-clickstream-related-request-id"),
                "address-ui-widgets-address-wizard-interaction-id": bs_val(addr_html, "address-ui-widgets-address-wizard-interaction-id"),
            },
            allow_redirects=True,
        )
        logger.info(f"  ADDRESS status={r_otp2.status_code}  url={r_otp2.url}")

        if not any(x in r_otp2.url for x in _SUCCESS_URLS):
            herosms.cancel()
            raise RuntimeError(f"Unexpected URL after address: {r_otp2.url}")

        # ── COOKIES — última petición: home ────────────────────────────────────
        cookie_str, cookie_raw = get_final_cookies()
        herosms.finish()

    elif any(x in url9 for x in _SUCCESS_URLS):
        cookie_str, cookie_raw = get_final_cookies()
        herosms.finish()

    elif "ap/register" in url9:
        herosms.cancel()
        raise RuntimeError(f"Amazon rejected — redirected to /ap/register: {url9}")

    else:
        herosms.cancel()
        raise RuntimeError(f"Unexpected URL after step 9: {url9}")

    logger.info(f"[OK] Cuenta creada: {herosms.phone}")

    return {
        "phone":      herosms.phone,
        "password":   PASSWORD,
        "name":       f"{first_name} {last_name}",
        "cookies":    cookie_str,
        "cookie_raw": cookie_raw,
    }
