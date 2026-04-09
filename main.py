import os
import re
import sys
import json
import time
import base64
import random
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

FAKE = Faker()

_PARSER = "lxml"

_OTP_TEXTS = (
    "verification code",
    "codigo de verificacion",
    "Enter the OTP",
    "Enter the code",
    "We texted you",
    "We sent a code",
    "check your phone",
    "SMS",
)
_OTP_FIELD_PATTERNS = ("otp", "code", "pin", "cvf_captcha_input", "verificationCode")
_SUCCESS_URLS = (
    "primevideo",
    "amazon.com/gp",
    "amazon.com/?ref",
    "amazon.com/ref",
    "www.amazon.com/",
)
_OTP_URL_PATTERNS = ("otp", "cvf", "verify", "code", "auth")

# -- CONFIG -------------------------------------------------------------------
capsolver.api_key = os.getenv("CAPSOLVER_KEY")
PROXY_URL = os.getenv("REQ_PROXY")


# =============================================================================
# CLASE PRINCIPAL - CADA INSTANCIA ES COMPLETAMENTE INDEPENDIENTE
# =============================================================================

class AmazonCreator:
    """Cada instancia tiene su propia sesión, sus propias variables, sin interferencia"""
    
    def __init__(self):
        # Datos únicos para esta generación
        self.fake = Faker()
        self.first_name = self.fake.first_name()
        self.last_name = self.fake.last_name()
        self.password = "dfbc1992"
        
        # Variables de estado (NO globales)
        self.activation_id = None
        self.phone = None
        
        # Logger único para esta instancia
        self.logger = beautiful_logger(f"amazon_{id(self)}")
        
        # Sesión curl_cffi INDEPENDIENTE (NO compartida)
        self.session = curl_cffi.Session(impersonate="chrome")
        self.session.trust_env = False
        if PROXY_URL:
            self.session.proxies = {"http": PROXY_URL, "https": PROXY_URL}
    
    # =========================================================================
    # MÉTODOS DE UTILIDAD
    # =========================================================================
    
    def req(self, method, url, **kw):
        """Petición HTTP usando la sesión propia de esta instancia"""
        kw.setdefault("timeout", 20)
        max_retries = 2
        
        for attempt in range(max_retries):
            try:
                return self.session.request(method, url, **kw)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                self.logger.warning(f"Retry {attempt + 1}/{max_retries}: {e}")
                time.sleep(0.5 * (attempt + 1))
    
    def find_between(self, data, first, last):
        s = data.find(first)
        if s == -1:
            return None
        s += len(first)
        e = data.find(last, s)
        if e == -1:
            return None
        return data[s:e]
    
    def bs_val(self, html, name, default=None):
        el = html.find("input", {"name": name})
        if el:
            return el.get("value", default or "")
        return default or ""
    
    def all_inputs(self, html) -> dict:
        result = {}
        for inp in html.find_all("input"):
            name = inp.get("name")
            val = inp.get("value", "")
            if name:
                result[name] = val
        return result
    
    def save(self, filename, content):
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        self.logger.info(f"  -> guardado: {filename}")
    
    def extract_asset_urls(self, html_text):
        urls = []
        scripts = re.findall(
            r"<script[\s\S]*?>[\s\S]*?<\/script>", html_text, re.IGNORECASE
        )
        for script in scripts:
            for pat in [
                r"load\.js\(['\"]( https?://[^'\"]+)['\"]\)",
                r"ue\.uels\(['\"]( https?://[^'\"]+\.js)['\"]\)",
                r"src=[\"'](https://static\.siege-amazon\.com/[^'\"]+\.js\?v=\d+)[\"']",
            ]:
                for m in re.finditer(pat, script):
                    urls.append(m.group(1).strip())
        urls.reverse()
        return urls, len(urls)
    
    def extract_resend_url(self, html_obj):
        for script in html_obj.find_all("script"):
            if script.string and "resendUrl" in script.string:
                match = re.search(r'"resendUrl":"([^"]+)"', script.string)
                if match:
                    return match.group(1)
        return None
    
    def detect_otp_page(self, html_obj, url):
        url_lower = url.lower()
        is_pv_page = "/ap/pv" in url_lower
        otp_url = any(x in url_lower for x in _OTP_URL_PATTERNS)
        otp_fields = []
        for inp in html_obj.find_all("input"):
            name = (inp.get("name") or "").lower()
            typ = (inp.get("type") or "text").lower()
            if typ == "hidden":
                continue
            if any(x in name for x in _OTP_FIELD_PATTERNS):
                otp_fields.append(inp.get("name"))
        page_text = html_obj.get_text().lower()
        otp_text = any(x in page_text for x in _OTP_TEXTS)
        return (is_pv_page or otp_url or otp_text or bool(otp_fields)), otp_fields
    
    def extract_form_data(self, html_obj, url, form_id=None):
        if form_id:
            form = html_obj.find("form", {"id": form_id})
        else:
            form = html_obj.find("form")
        if not form:
            return None, {}
        action = form.get("action", "")
        if action and not action.startswith("http"):
            from urllib.parse import urljoin
            action = urljoin(url, action)
        inputs = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                inputs[name] = inp.get("value", "")
        return action, inputs
    
    def extract_cookies_from_response(self, response) -> tuple[str, dict]:
        """Extraer cookies del response final"""
        jar = {}
        try:
            jar = self.session.cookies.get_dict(domain=None, path=None) or {}
        except Exception:
            try:
                jar = dict(self.session.cookies)
            except Exception:
                jar = {}
        
        response_cookies = {}
        try:
            response_cookies = dict(response.cookies)
        except Exception:
            pass
        
        try:
            set_cookie_headers = response.headers.get_list("set-cookie") if hasattr(response.headers, "get_list") else []
            if not set_cookie_headers:
                raw = response.headers.get("set-cookie", "")
                if raw:
                    set_cookie_headers = [raw]
            for sc in set_cookie_headers:
                pair = sc.split(";")[0].strip()
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    response_cookies[k.strip()] = v.strip()
        except Exception:
            pass
        
        merged = {**jar, **response_cookies}
        
        processed = {}
        for k, v in merged.items():
            if not k or not v:
                continue
            if k in ("session-token", "x-main"):
                inner = v.strip('"')
                processed[k] = f'"{inner}"'
            else:
                processed[k] = v
        
        cookie_str = ";".join(f"{k}={v}" for k, v in processed.items())
        return cookie_str, processed
    
    # =========================================================================
    # MÉTODOS DE HEROSMS (usando la sesión propia)
    # =========================================================================
    
    def herosms_api(self, action, **params):
        params["api_key"] = os.getenv("HEROSMS_KEY")
        params["action"] = action
        for attempt in range(2):
            try:
                r = self.session.get(
                    "https://hero-sms.com/stubs/handler_api.php", params=params, timeout=20
                )
                try:
                    return r.json()
                except Exception:
                    return r.text
            except Exception as e:
                if attempt == 1:
                    raise
                self.logger.warning(f"HeroSMS retry {attempt + 1}: {e}")
                time.sleep(0.5)
    
    def herosms_get_number(self, service="am", max_price=None):
        country = int(os.getenv("HEROSMS_COUNTRY", "1"))
        self.logger.info(f"[HEROSMS] Requesting number: service={service} country={country}")
        
        params = {"service": service, "country": country}
        if max_price is not None:
            params["maxPrice"] = max_price
        
        result = self.herosms_api("getNumberV2", **params)
        
        if isinstance(result, dict) and "activationId" in result:
            self.activation_id = result["activationId"]
            self.phone = result["phoneNumber"]
            self.logger.info(f"[OK] activationId={self.activation_id} phone={self.phone}")
            self.logger.info(f"  cost={result.get('activationCost')} operator={result.get('activationOperator')}")
            return True
        
        if isinstance(result, dict) and result.get("title"):
            self.logger.info(f"[ERROR] {result['title']}: {result['details']}")
            return False
        
        self.logger.info(f"[ERROR] {result}")
        return False
    
    def herosms_get_status(self):
        result = self.herosms_api("getStatusV2", id=self.activation_id)
        self.logger.info(f"[HEROSMS] status={result}")
        if isinstance(result, dict):
            return result
        return {"raw": str(result)}
    
    def herosms_set_status(self, status):
        status_map = {1: "SMS sent", 3: "retry SMS", 6: "finish", 8: "cancel"}
        self.logger.info(f"[HEROSMS] setStatus={status} ({status_map.get(status, '?')})")
        result = self.herosms_api("setStatus", id=self.activation_id, status=status)
        self.logger.info(f"[HEROSMS] response={result}")
        return result
    
    def herosms_poll_code(self, timeout=120, interval=2):
        self.logger.info(f"[HEROSMS] Polling for SMS code (timeout={timeout}s)...")
        start = time.time()
        
        while time.time() - start < timeout:
            status = self.herosms_get_status()
            raw = status.get("raw")
            
            if raw == "STATUS_WAIT_CODE":
                self.logger.info(f"Waiting for SMS... ({int(time.time() - start)}s)")
                time.sleep(interval)
                continue
                
            if raw == "STATUS_WAIT_RETRY":
                self.logger.info(f"SMS not yet received by Amazon, notifying...")
                self.herosms_set_status(3)
                time.sleep(interval)
                continue
                
            if raw == "STATUS_CANCEL":
                self.logger.info(f"[ERROR] Activation cancelled")
                return None
                
            sms = status.get("sms") or {}
            code = sms.get("code") if isinstance(sms, dict) else None
            
            if code:
                self.logger.info(f"[OK] Code received: {code}")
                return code
                
            self.logger.info(f"Status: {raw or status}")
            time.sleep(interval)
            
        self.logger.info(f"[ERROR] Timeout waiting for SMS")
        return None
    
    def herosms_finish(self):
        if self.activation_id:
            self.logger.info(f"[HEROSMS] Finishing activation {self.activation_id}")
            result = self.herosms_api("finishActivation", id=self.activation_id)
            self.logger.info(f"[HEROSMS] finish=True")
            return result
        return None
    
    def herosms_cancel(self):
        if self.activation_id:
            self.logger.info(f"[HEROSMS] Cancelling activation {self.activation_id}")
            result = self.herosms_api("cancelActivation", id=self.activation_id)
            self.logger.info(f"[HEROSMS] cancel={result}")
            return result
        return None
    
    # =========================================================================
    # MÉTODO PRINCIPAL - CREAR CUENTA
    # =========================================================================
    
    def create_account(self) -> dict:
        """Método principal - completamente aislado"""
        start_time = time.time()
        
        UA = "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        
        self.logger.info("[0] CREATION OF ACCOUNT...")
        self.logger.info(f"  first_name={self.first_name}")
        self.logger.info(f"  last_name={self.last_name}")
        
        # =====================================================================
        # PASO 1: GET /ap/signin
        # =====================================================================
        self.logger.info("[1] GET /ap/signin...")
        r1 = self.req(
            "GET",
            "https://www.amazon.com/ap/signin",
            headers={
                "User-Agent": UA,
                "Upgrade-Insecure-Requests": "1",
                "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
                "device-memory": "8",
                "sec-ch-device-memory": "8",
                "dpr": "1",
                "sec-ch-dpr": "1",
                "viewport-width": "548",
                "sec-ch-viewport-width": "548",
                "ect": "3g",
                "rtt": "400",
                "downlink": "1.35",
            },
            params={
                "showRememberMe": "true",
                "openid.pape.max_auth_age": "0",
                "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
                "siteState": "135-4789514-8844217",
                "language": "en_US",
                "pageId": "amzn_prime_video_ww",
                "openid.return_to": "https://na.primevideo.com/auth/return/ref=av_auth_ap?_t=placeholder&location=/",
                "prevRID": "AQDQ2AF57Y6W3GM45ABJ",
                "openid.assoc_handle": "amzn_prime_video_sso_us",
                "openid.mode": "checkid_setup",
                "prepopulatedLoginId": "",
                "failedSignInCount": "0",
                "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
                "openid.ns": "http://specs.openid.net/auth/2.0",
            },
        )
        # self.save("step1_signin.html", r1.text)
        self.logger.info(f"  status={r1.status_code}")
        
        h1 = BeautifulSoup(r1.text, _PARSER)
        siteState1 = self.bs_val(h1, "siteState")
        returnTo1 = self.bs_val(h1, "openid.return_to")
        prevRID1 = self.bs_val(h1, "prevRID")
        workflowState1 = self.bs_val(h1, "workflowState")
        self.logger.info(f"  siteState={siteState1[:40] if siteState1 else 'None'}...")
        
        # =====================================================================
        # PASO 2: GET /ap/register
        # =====================================================================
        self.logger.info("[2] GET /ap/register...")
        r2 = self.req(
            "GET",
            "https://www.amazon.com/ap/register",
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
            },
            params={
                "showRememberMe": "true",
                "openid.pape.max_auth_age": "0",
                "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
                "siteState": siteState1,
                "language": "en_US",
                "pageId": "amzn_prime_video_ww",
                "openid.return_to": returnTo1,
                "prevRID": prevRID1,
                "openid.assoc_handle": "amzn_prime_video_sso_us",
                "openid.mode": "checkid_setup",
                "prepopulatedLoginId": "",
                "failedSignInCount": "0",
                "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
                "openid.ns": "http://specs.openid.net/auth/2.0",
            },
        )
        # self.save("step2_register_form.html", r2.text)
        self.logger.info(f"  status={r2.status_code}")
        
        h2 = BeautifulSoup(r2.text, _PARSER)
        appActionToken2 = self.bs_val(h2, "appActionToken")
        return_to2 = self.bs_val(h2, "openid.return_to")
        prevRID2 = self.bs_val(h2, "prevRID")
        siteState2 = self.bs_val(h2, "siteState")
        workflowState2 = self.bs_val(h2, "workflowState")
        csrf2 = self.bs_val(h2, "anti-csrftoken-a2z")
        self.logger.info(f"  appActionToken={appActionToken2[:30] if appActionToken2 else 'None'}...")
        
        # =====================================================================
        # PASO 2.5: HeroSMS: get phone number
        # =====================================================================
        if not self.herosms_get_number():
            self.logger.error("Failed to get phone number from HeroSMS")
            raise Exception("Failed to get phone number from HeroSMS")
        
        self.logger.info(f"  Using HeroSMS phone: {self.phone}")
        
        # =====================================================================
        # PASO 3: POST /ap/register
        # =====================================================================
        self.logger.info("[3] POST /ap/register...")
        self.logger.info(f"Location for encryption: {r2.url}")
        
        data_json = generate_spoofed_auth(
            user_agent=UA,
            location=r2.url,
            email=self.phone,
            name=f"{self.first_name} {self.last_name}",
            password=self.password,
            html_b64=base64.b64encode(r2.text.encode("utf-8")).decode("utf-8"),
        )
        
        fp_amz = data_json.metadata1
        
        # Lista de contraseñas encriptadas
        passwords = [
            "AYAAFHtMjg0m8dFzdfdfXbYsbMcAAAABAAZzaTptZDUAIDk3MzkwMGFkZGIwNjFmYmU1YmI0ZWE4NzFlOWQ4MTYxAQCi6X/1M6Zr1dn9EWQ7/02Je++VREFWrqaMIlQViT94RHNbRRmCeZlx14XnmwQfHNwYm6z8Tchq8e+Qt+ARlsnQT4gYGqyHOBN+tpv8G6LklNtEGbILENZCio63RQgFL4+pX7c6A4Ntp/K3JIhe9iZEGua7FeBtLJudofTgD3SkBLU9TtpsNsoIi037DrajpLxktt9HHfLArys3fNNG2rig27ityg0+Ril14FaeGlKCzv2E7fsLPWQY0UeJYcDeHUlHD7StPz1jrJLQrYoCZvAa3EivQbnH+Y+PIRiy40CF5cVU8h5TjCq0WJKuQQEvrIcMzsDO2YISY/cEoIF9EDOGAgAAAAAMAAAACQAAAAAAAAAAAAAAAI9jr/feb7Rl1PMY2r/Kw1T/////AAAAAQAAAAAAAAAAAAAAAQAAAAjGC3HuggWEnXynhfdnu1h1R8tZH47PRR8=",
            "AYAAFC8mk1qm7agMTAWvY0CniRgAAAABAAZzaTptZDUAIDk3MzkwMGFkZGIwNjFmYmU1YmI0ZWE4NzFlOWQ4MTYxAQCDIh1zw8fXzP4eGVlSMxZsc0fChWXOq0zlEX9WKxQOEXfWnL0BrpWsomGui5+2pMJW+pwjXUt5fUpfETa3+daiv6MjNJuxcBwGJOwvVuz+gPz7m4Cq0zb6MvMc/Nmj1Ekfw9qTTqqgEq1ZpBP9iL18QyKo0ukjAFbPOa32lHtct1Jdm2f0OFDQ3hJQwcl1HF139/AH74PVjCVaW6+ErJ++W9XScz9XEW8hlITe2Ym6Er8JRa9zFxW8mc89s6154IiWc6muIPPwxKvc6UufQGYCKB5KiQinmddHy2iX4eb/PoHXHe6DrZSFGb9Bw1iqnQhlV5LoRk/3FmZP3tlP+rGmAgAAAAAMAAAACQAAAAAAAAAAAAAAAEHM9he/Xd8RQNFTecPdUkL/////AAAAAQAAAAAAAAAAAAAAAQAAAAjgv2QNxHhauWeM/+6tYQfn1fK87TJauoI=",
        ]
        encrypted_pwd = random.choice(passwords)
        
        r3 = self.req(
            "POST",
            "https://www.amazon.com/ap/register",
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.amazon.com",
                "Referer": r2.url,
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
            },
            data={
                "appActionToken": appActionToken2,
                "appAction": "REGISTER",
                "openid.return_to": return_to2,
                "prevRID": prevRID2,
                "siteState": siteState2,
                "workflowState": workflowState2,
                "anti-csrftoken-a2z": csrf2,
                "customerName": f"{self.first_name} {self.last_name}",
                "countryCode": "US",
                "email": self.phone,
                "encryptedPwd": encrypted_pwd,
                "metadata1": fp_amz,
                "encryptedPasswordExpected": "",
            },
        )
        # self.save("step3_post_register.html", r3.text)
        self.logger.info(f"  status={r3.status_code}  url={r3.url}")
        
        h3 = BeautifulSoup(r3.text, _PARSER)
        csrf3 = self.bs_val(h3, "anti-csrftoken-a2z") or csrf2
        
        data_context_list = re.findall(r'"data-context":\s*\'({[^\']*})\'', r3.text)
        data_context = data_context_list[0] if data_context_list else None
        if not data_context:
            data_context_list = re.findall(r'data-context="({[^"]*})"', r3.text)
            data_context = data_context_list[0] if data_context_list else None
        
        data_ext_id = self.find_between(r3.text, '"data-external-id": "', '"')
        if not data_ext_id:
            data_ext_id = self.find_between(r3.text, 'data-external-id="', '"')
        
        clientContext = self.bs_val(h3, "clientContext")
        verifyToken = self.bs_val(h3, "verifyToken")
        siteState3 = self.bs_val(h3, "siteState") or siteState2
        return_to3 = (self.bs_val(h3, "openid.return_to") or return_to2).replace("&amp;", "&")
        
        cvf_form_action = self.find_between(
            r3.text, 'id="cvf-aamation-challenge-form" method="post" action="', '"'
        )
        if not cvf_form_action:
            m = re.search(r'action="(/[^"]*cvf[^"]*)"', r3.text, re.IGNORECASE)
            cvf_form_action = m.group(1) if m else "/ap/cvf/verify"
        
        self.logger.info(f"  data_context={'[OK]' if data_context else '[ERROR]'}")
        self.logger.info(f"  data_ext_id={data_ext_id}")
        self.logger.info(f"  clientContext={clientContext[:30] if clientContext else 'None'}...")
        self.logger.info(f"  verifyToken={'[OK]' if verifyToken else '[ERROR]'}")
        self.logger.info(f"  cvf_form_action={cvf_form_action}")
        
        if not data_context:
            self.logger.warning("Sin data-context -- verifica step3_post_register.html")
            raise Exception("Missing data-context")
        
        # =====================================================================
        # PASO 4: GET /aaut/verify/cvf
        # =====================================================================
        self.logger.info("[4] GET /aaut/verify/cvf...")
        options4 = json.dumps(
            {
                "clientData": data_context,
                "challengeType": "WAF_ADVERSARIAL_SYNTHETIC_GRID_V2_LEVEL_1",
                "locale": "en-US",
                "externalId": data_ext_id,
                "enableHeaderFooter": False,
                "enableBypassMechanism": False,
                "enableModalView": False,
                "eventTrigger": None,
                "aaExternalToken": None,
                "forceJsFlush": False,
                "aamationToken": None,
            },
            separators=(",", ":"),
        )
        
        r4 = self.req(
            "GET",
            "https://www.amazon.com/aaut/verify/cvf",
            headers={
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Content-Type": "application/json",
                "Connection": "keep-alive",
                "Referer": r3.url,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
            },
            params={"options": options4},
        )
        # self.save("step4_aaut.html", r4.text)
        self.logger.info(f"  status={r4.status_code}")
        
        ctx4 = {}
        raw4 = r4.headers.get("amz-aamation-resp")
        if raw4:
            try:
                ctx4 = json.loads(raw4)
            except Exception:
                pass
        
        session_token4 = ctx4.get("sessionToken", "")
        client_side_ctx4 = ctx4.get("clientSideContext", "")
        self.logger.info(f"  sessionToken={session_token4[:40] if session_token4 else 'None'}...")
        self.logger.info(f"  clientSideContext={client_side_ctx4[:40] if client_side_ctx4 else 'None'}...")
        
        problem_version = self.find_between(r4.text, '"problem":"', '"')
        captcha_id = self.find_between(r4.text, '"id":"', '"')
        captcha_url = self.find_between(r4.text, '<script src="', '"')
        captcha_domain = (
            self.find_between(captcha_url, "https://", "/ait/") if captcha_url else None
        )
        
        self.logger.info(f"  problem={problem_version}")
        self.logger.info(f"  captcha_id={captcha_id}")
        self.logger.info(f"  captcha_domain={captcha_domain}")
        
        # =====================================================================
        # PASO 5-7: CAPTCHA RESOLUTION
        # =====================================================================
        max_retries = 3
        captcha_voucher = None
        
        for attempt in range(1, max_retries + 1):
            self.logger.info(f"\n[Intento {attempt}/{max_retries}] Resolviendo captcha...")
            
            if attempt == max_retries:
                self.logger.error(f"Falló después de {max_retries} intentos")
                raise Exception(f"Captcha failed after {max_retries} attempts")
            
            self.logger.info("\n[5] GET captcha problem...")
            
            r5 = self.req(
                "GET",
                f"https://{captcha_domain}/ait/ait/ait/problem",
                headers={
                    "User-Agent": UA,
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.amazon.com/",
                    "Origin": "https://www.amazon.com",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "cross-site",
                    "Pragma": "no-cache",
                    "Cache-Control": "no-cache",
                },
                params={
                    "kind": "visual",
                    "domain": "www.amazon.com",
                    "locale": "en-us",
                    "problem": problem_version,
                    "num_solutions_required": "1",
                    "id": captcha_id,
                },
            )
            # self.save("step5_captcha_problem.html", r5.text)
            self.logger.info(f"  status={r5.status_code}")
            
            prob = r5.json()
            assets = prob.get("assets") or {}
            target_raw = assets.get("target", "")
            images_raw = assets.get("images", "[]")
            hmac_tag = prob.get("hmac_tag", "")
            state5 = prob.get("state") or {}
            iv5 = state5.get("iv", "")
            payload5 = state5.get("payload", "")
            key5 = prob.get("key", "")
            
            images = json.loads(images_raw) if isinstance(images_raw, str) else images_raw
            target = re.sub(r'[\[\]"\']', "", target_raw).replace("_", " ").strip()
            self.logger.info(f"  target={target}")
            self.logger.info(f"  images={len(images)}")
            
            if not images or not target:
                self.logger.error("Sin imagenes o target")
                raise Exception("Missing images or target")
            
            # =================================================================
            # PASO 6: CapSolver
            # =================================================================
            self.logger.info("\n[6] Resolviendo captcha con CapSolver...")
            try:
                solution = capsolver.solve(
                    {
                        "type": "AwsWafClassification",
                        "question": f"aws:grid:{target}",
                        "images": images,
                    }
                )
                self.logger.info(f"  solution={solution}")
            except Exception as e:
                self.logger.error(f"CapSolver error: {e}")
                raise
            
            if not solution or not solution.get("objects"):
                self.logger.error(f"Sin objetos: {solution}")
                raise Exception("No solution objects from CapSolver")
            
            solution_objects = solution["objects"]
            self.logger.info(f"  [OK] {len(solution_objects)} objetos")
            
            # =================================================================
            # PASO 7: POST captcha verify
            # =================================================================
            self.logger.info("\n[7] POST captcha verify...")
            r7 = self.req(
                "POST",
                f"https://{captcha_domain}/ait/ait/ait/verify",
                headers={
                    "User-Agent": UA,
                    "Accept": "*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.amazon.com/",
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Origin": "https://www.amazon.com",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "cross-site",
                    "Pragma": "no-cache",
                    "Cache-Control": "no-cache",
                },
                json={
                    "hmac_tag": hmac_tag,
                    "state": {"iv": iv5, "payload": payload5},
                    "key": key5,
                    "client_solution": solution_objects,
                    "metrics": {"solve_time_millis": random.randint(8000, 20000)},
                    "locale": "en-us",
                },
            )
            # self.save("step7_captcha_verify.html", r7.text)
            self.logger.info(f"  status={r7.status_code}")
            
            r7j = r7.json()
            if r7j.get("success"):
                captcha_voucher = r7j.get("captcha_voucher", "")
                self.logger.info(f"  [OK] ¡Captcha resuelto! voucher={captcha_voucher[:40]}...")
                break
            else:
                self.logger.warning(f"  Verificación falló: {r7j}")
                if attempt == max_retries:
                    raise Exception(f"Captcha verification failed: {r7j}")
                continue
        
        # =====================================================================
        # PASO 8: GET /aaut/verify/cvf/{captcha_id}
        # =====================================================================
        self.logger.info("\n[8] GET /aaut/verify/cvf/{id} (canjear voucher)...")
        r8 = self.req(
            "GET",
            f"https://www.amazon.com/aaut/verify/cvf/{captcha_id}",
            headers={
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "content-type": "application/json",
                "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "cache-control": "no-cache",
                "pragma": "no-cache",
            },
            params={
                "context": client_side_ctx4,
                "options": options4,
                "response": '{"challengeType":"WAF_ADVERSARIAL_SYNTHETIC_GRID_V2_LEVEL_1","data":"\\"'
                + captcha_voucher
                + '\\""}',
            },
        )
        # self.save("step8_cvf_exchange.html", r8.text)
        self.logger.info(f"  status={r8.status_code}")
        
        ctx8 = {}
        raw8 = r8.headers.get("amz-aamation-resp")
        if raw8:
            try:
                ctx8 = json.loads(raw8)
            except Exception:
                pass
        
        final_session_token = ctx8.get("sessionToken", "")
        final_client_ctx = ctx8.get("clientSideContext", "")
        self.logger.info(f"  final sessionToken={final_session_token[:50] if final_session_token else 'None'}...")
        
        if not final_session_token:
            self.logger.info("  [WARN] Sin sessionToken final -- usando fallback del paso 4")
            final_session_token = session_token4
        
        # =====================================================================
        # PASO 9: POST /ap/cvf/verify
        # =====================================================================
        self.logger.info("\n[9] POST /ap/cvf/verify...")
        
        token_t = self.find_between(return_to3, "ref=av_auth_ap?_t=", "&") or self.find_between(
            return_to3, "_t=", "&"
        )
        if token_t:
            openid_return_to = f"https://na.primevideo.com/auth/return/ref=av_auth_ap?_t={token_t}&location=/?ref_%3Datv_auth_pre"
        else:
            openid_return_to = return_to3
        
        post_data = {
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
        }
        
        self.logger.info(f"  POST data keys: {list(post_data.keys())}")
        self.logger.info(f"  cvf_form_action: {cvf_form_action}")
        
        r9 = self.req(
            "POST",
            "https://www.amazon.com"
            + (cvf_form_action if cvf_form_action.startswith("/") else "/ap/cvf/verify"),
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.amazon.com",
                "Referer": r3.url,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
                "device-memory": "8",
                "sec-ch-device-memory": "8",
                "dpr": "1",
                "sec-ch-dpr": "1",
                "viewport-width": "380",
                "sec-ch-viewport-width": "380",
                "ect": "4g",
                "rtt": "50",
                "downlink": "50",
            },
            data=post_data,
            allow_redirects=True,
        )
        # self.save("step9_cvf_verify.html", r9.text)
        self.logger.info(f"  status={r9.status_code}  url={r9.url}")
        
        # =====================================================================
        # MANEJAR MOBILE CLAIM CONFLICT
        # =====================================================================
        if (
            r9.status_code == 200
            and "mobileclaimconflict" in r9.url.lower()
            or "mobileclaimconflict" in r9.text.lower()
        ):
            self.logger.info("  [INFO] Mobile claim conflict detected")
            
            html = BeautifulSoup(r9.text, _PARSER)
            mobileNumberReclaimJWTToken = self.bs_val(html, "mobileNumberReclaimJWTToken")
            appActionToken = self.bs_val(html, "appActionToken")
            appAction = self.bs_val(html, "appAction")
            return_to = self.bs_val(html, "openid.return_to")
            prevRID = self.bs_val(html, "prevRID")
            siteState = self.bs_val(html, "siteState")
            workflowState = self.bs_val(html, "workflowState")
            
            params = {
                "openid.pape.max_auth_age": "900",
                "openid.return_to": f"https://na.primevideo.com/auth/return/ref=av_auth_ap?_t=1{token_t}&location=/offers/nonprimehomepage?ref_%3Ddv_web_force_root",
                "prevRID": prevRID,
                "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
                "openid.assoc_handle": "amzn_prime_video_sso_us",
                "openid.mode": "checkid_setup",
                "siteState": siteState,
                "language": "en_US",
                "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
                "pageId": "amzn_prime_video_ww",
                "openid.ns": "http://specs.openid.net/auth/2.0",
            }
            
            data = {
                "mobileNumberReclaimJWTToken": mobileNumberReclaimJWTToken,
                "appActionToken": appActionToken,
                "appAction": appAction,
                "openid.return_to": return_to,
                "prevRID": prevRID,
                "siteState": siteState,
                "workflowState": workflowState,
            }
            
            r9 = self.req(
                "POST",
                "https://www.amazon.com/ap/mobileclaimconflict/ref=ap_register_mobile_claim_conflict_warned_popover_continue_verify",
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "accept-language": "en-US,en;q=0.9",
                    "cache-control": "no-cache",
                    "content-type": "application/x-www-form-urlencoded",
                    "origin": "https://www.amazon.com",
                    "pragma": "no-cache",
                    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="130", "Google Chrome";v="130"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "same-origin",
                    "sec-fetch-user": "?1",
                    "upgrade-insecure-requests": "1",
                    "user-agent": UA,
                },
                params=params,
                data=data,
                allow_redirects=True,
            )
            # self.save("step9_cvf_reclaim.html", r9.text)
            self.logger.info(f"  status={r9.status_code}  url={r9.url}")
        
        # =====================================================================
        # PASO 10: Detectar resultado y manejar OTP
        # =====================================================================
        self.logger.info("\n[10] Analizando respuesta...")
        
        h9 = BeautifulSoup(r9.text, _PARSER)
        url9 = r9.url
        
        is_otp, otp_fields = self.detect_otp_page(h9, url9)
        
        # -- Caso 1: OTP ------------------------------------------------------------
        if is_otp:
            self.logger.info(f"\n[SMS] OTP DETECTADO")
            self.logger.info(f"  URL: {url9}")
            self.logger.info(f"  OTP fields: {otp_fields}")
            
            form_action, form_inputs = self.extract_form_data(h9, url9, form_id="auth-pv-form")
            if not form_action:
                form_action, form_inputs = self.extract_form_data(h9, url9)
            self.logger.info(f"  Form action: {form_action}")
            self.logger.info(f"  Form inputs: {list(form_inputs.keys())}")
            
            resend_url = self.extract_resend_url(h9)
            if resend_url:
                self.logger.info(f"  Resend URL disponible: {resend_url[:100]}...")
            
            # Pollear el código SMS
            otp_code = self.herosms_poll_code()
            
            if otp_code and otp_fields and form_action:
                self.logger.info(f"\n[11] POST OTP: {otp_code}...")
                
                otp_data = form_inputs.copy()
                for field in otp_fields:
                    otp_data[field] = otp_code
                new_csrf = self.bs_val(h9, "anti-csrftoken-a2z")
                if new_csrf:
                    otp_data["anti-csrftoken-a2z"] = new_csrf
                
                otp_data["metadata1"] = ""
                
                self.logger.info(f"  OTP data keys: {list(otp_data.keys())}")
                
                r_otp = self.req(
                    "POST",
                    form_action,
                    headers={
                        "User-Agent": UA,
                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://www.amazon.com",
                        "Upgrade-Insecure-Requests": "1",
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-User": "?1",
                    },
                    data=otp_data,
                    allow_redirects=True,
                )
                
                self.logger.info(f"  OTP status={r_otp.status_code}  url={r_otp.url}")
                
                # Navegar a direcciónes
                r_otp = self.req(
                    "GET",
                    "https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post",
                    headers={
                        "User-Agent": UA,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Connection": "keep-alive",
                        "Upgrade-Insecure-Requests": "1",
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-User": "?1",
                        "Priority": "u=0, i",
                        "Pragma": "no-cache",
                        "Cache-Control": "no-cache",
                    },
                    allow_redirects=True,
                )
                
                addresss = BeautifulSoup(r_otp.text, _PARSER)
                csrf_token = self.bs_val(addresss, "csrfToken") or new_csrf
                address_ui_widgets_previous_address_form_state_token = self.bs_val(
                    addresss, "address-ui-widgets-previous-address-form-state-token"
                )
                address_ui_widgets_obfuscated_customerId = self.bs_val(
                    addresss, "address-ui-widgets-obfuscated-customerId"
                )
                address_ui_widgets_csrfToken = self.bs_val(
                    addresss, "address-ui-widgets-csrfToken"
                )
                address_ui_widgets_form_load_start_time = self.bs_val(
                    addresss, "address-ui-widgets-form-load-start-time"
                )
                address_ui_widgets_clickstream_related_request_id = self.bs_val(
                    addresss, "address-ui-widgets-clickstream-related-request-id"
                )
                address_ui_widgets_address_wizard_interaction_id = self.bs_val(
                    addresss, "address-ui-widgets-address-wizard-interaction-id"
                )
                
                headers = {
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "accept-language": "en-US,en;q=0.9",
                    "cache-control": "no-cache",
                    "content-type": "application/x-www-form-urlencoded",
                    "device-memory": "8",
                    "downlink": "10",
                    "dpr": "1",
                    "ect": "4g",
                    "origin": "https://www.amazon.com",
                    "pragma": "no-cache",
                    "priority": "u=0, i",
                    "referer": "https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post",
                    "rtt": "250",
                    "sec-ch-device-memory": "8",
                    "sec-ch-dpr": "1",
                    "sec-ch-viewport-width": "380",
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "same-origin",
                    "sec-fetch-user": "?1",
                    "upgrade-insecure-requests": "1",
                    "user-agent": UA,
                    "viewport-width": "380",
                }
                
                params = {
                    "ref": "ya_address_book_add_post",
                }
                
                data = {
                    "csrfToken": csrf_token,
                    "addressID": "",
                    "address-ui-widgets-countryCode": "US",
                    "address-ui-widgets-enterAddressFullName": f"{self.first_name} {self.last_name}",
                    "address-ui-widgets-enterAddressPhoneNumber": "+1" + self.phone,
                    "address-ui-widgets-enterAddressLine1": "Street23",
                    "address-ui-widgets-enterAddressLine2": "",
                    "address-ui-widgets-enterAddressCity": "New York",
                    "address-ui-widgets-enterAddressStateOrRegion": "NY",
                    "address-ui-widgets-enterAddressPostalCode": "10081",
                    "address-ui-widgets-urbanization": "",
                    "address-ui-widgets-previous-address-form-state-token": address_ui_widgets_previous_address_form_state_token,
                    "address-ui-widgets-use-as-my-default": "true",
                    "address-ui-widgets-delivery-instructions-desktop-expander-context": '{"deliveryInstructionsDisplayMode" : "CDP_ONLY", "deliveryInstructionsClientName" : "YourAccountAddressBook", "deliveryInstructionsDeviceType" : "desktop", "deliveryInstructionsIsEditAddressFlow" : "false"}',
                    "address-ui-widgets-addressFormButtonText": "save",
                    "address-ui-widgets-addressFormHideHeading": "true",
                    "address-ui-widgets-heading-string-id": "",
                    "address-ui-widgets-addressFormHideSubmitButton": "false",
                    "address-ui-widgets-enableAddressDetails": "true",
                    "address-ui-widgets-returnLegacyAddressID": "false",
                    "address-ui-widgets-enableDeliveryInstructions": "true",
                    "address-ui-widgets-enableAddressWizardInlineSuggestions": "true",
                    "address-ui-widgets-enableEmailAddress": "false",
                    "address-ui-widgets-enableAddressTips": "true",
                    "address-ui-widgets-amazonBusinessGroupId": "",
                    "address-ui-widgets-clientName": "YourAccountAddressBook",
                    "address-ui-widgets-enableAddressWizardForm": "true",
                    "address-ui-widgets-delivery-instructions-data": '{"initialCountryCode":"US"}',
                    "address-ui-widgets-ab-delivery-instructions-data": "",
                    "address-ui-widgets-address-wizard-interaction-id": address_ui_widgets_address_wizard_interaction_id,
                    "address-ui-widgets-obfuscated-customerId": address_ui_widgets_obfuscated_customerId,
                    "address-ui-widgets-locationData": "",
                    "address-ui-widgets-enableLatestAddressWizardForm": "false",
                    "address-ui-widgets-avsSuppressSoftblock": "false",
                    "address-ui-widgets-avsSuppressSuggestion": "false",
                    "address-ui-widgets-csrfToken": address_ui_widgets_csrfToken,
                    "address-ui-widgets-form-load-start-time": address_ui_widgets_form_load_start_time,
                    "address-ui-widgets-clickstream-related-request-id": address_ui_widgets_clickstream_related_request_id,
                    "address-ui-widgets-deliveryDestinationCity": "New&#32;York",
                    "address-ui-widgets-deliveryDestinationNonUciPostalCode": "10022",
                    "address-ui-widgets-autofill-location-spinner-loading-text": "Loading",
                    "address-ui-widgets-locale": "",
                }
                
                r_otp = self.req(
                    "POST",
                    "https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post",
                    headers=headers,
                    data=data,
                    params=params,
                )
                
                self.logger.info(f"  Amazon ADDRESS status={r_otp.status_code}  url={r_otp.url}")
                
                url_otp = r_otp.url
                
                if any(x in url_otp for x in _SUCCESS_URLS):
                    self.logger.info("\n[OK] CUENTA CREADA EXITOSAMENTE")
                    self.logger.info(f"  URL final: {url_otp}")
                    
                    self.logger.info("\n[12] Obteniendo cookies adicionales...")
                    
                    self.req(
                        "GET",
                        "https://www.amazon.com/gp/history",
                        headers={
                            "User-Agent": UA,
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.9",
                            "Referer": "https://www.amazon.com/",
                            "Upgrade-Insecure-Requests": "1",
                        },
                    )
                    
                    r_home = self.req(
                        "GET",
                        "https://www.amazon.com/ref=nav_logo",
                        headers={
                            "User-Agent": UA,
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.9",
                            "Referer": "https://www.amazon.com/gp/history",
                            "Upgrade-Insecure-Requests": "1",
                            "Sec-Fetch-Dest": "document",
                            "Sec-Fetch-Mode": "navigate",
                            "Sec-Fetch-Site": "same-origin",
                            "Sec-Fetch-User": "?1",
                            "Cache-Control": "no-cache",
                            "Pragma": "no-cache",
                        },
                    )
                    self.logger.info(f"  HOME status={r_home.status_code}  url={r_home.url}")
                    
                    cookie_str, cookies = self.extract_cookies_from_response(r_home)
                    self.logger.info(f"  Cookies extraídas: {len(cookies)} ({list(cookies.keys())})")
                    
                    self.herosms_finish()
                    
                    elapsed_time = time.time() - start_time
                    
                    return {
                        "phone": self.phone,
                        "password": self.password,
                        "name": f"{self.first_name} {self.last_name}",
                        "cookies": cookie_str,
                        "elapsed": round(elapsed_time, 2)
                    }
                elif "otp" in url_otp.lower() or "verify" in url_otp.lower():
                    self.logger.warning("\n[WARN] OTP incorrecto o expirado")
                    raise Exception("Invalid OTP")
                else:
                    self.logger.warning(f"\n[WARN] URL inesperada post-OTP: {url_otp}")
                    raise Exception(f"Unexpected URL after OTP: {url_otp}")
            else:
                if not otp_code:
                    raise Exception("No OTP code received from HeroSMS")
                if not otp_fields:
                    raise Exception("OTP fields not detected")
                if not form_action:
                    raise Exception("Form action not found")
        
        # -- Caso 2: Exito directo -------------------------------------------------
        elif any(x in url9 for x in _SUCCESS_URLS):
            self.logger.info("\n[OK] CUENTA CREADA EXITOSAMENTE")
            self.logger.info(f"  URL: {url9}")
            
            self.logger.info("\n[12] Obteniendo cookies...")
            
            self.req(
                "GET",
                "https://www.amazon.com/gp/history",
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.amazon.com/",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            
            r_home = self.req(
                "GET",
                "https://www.amazon.com/ref=nav_logo",
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.amazon.com/gp/history",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-User": "?1",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            self.logger.info(f"  HOME status={r_home.status_code}  url={r_home.url}")
            
            cookie_str, cookies = self.extract_cookies_from_response(r_home)
            self.logger.info(f"  Cookies extraídas: {len(cookies)} ({list(cookies.keys())})")
            
            self.herosms_finish()
            
            elapsed_time = time.time() - start_time
            
            return {
                "phone": self.phone,
                "password": self.password,
                "name": f"{self.first_name} {self.last_name}",
                "cookies": cookie_str,
                "elapsed": round(elapsed_time, 2)
            }
        
        # -- Caso 3: Error ---------------------------------------------------------
        else:
            self.logger.error(f"\n[ERROR] URL inesperada: {url9}")
            self.herosms_cancel()
            raise Exception(f"Unexpected response URL: {url9}")


# =============================================================================
# FUNCIÓN PRINCIPAL (wrapper para compatibilidad)
# =============================================================================

def create_account() -> dict:
    """
    Crea una cuenta Amazon de forma AISLADA.
    Cada llamada es independiente y puede ejecutarse en paralelo.
    """
    creator = AmazonCreator()
    return creator.create_account()


if __name__ == "__main__":
    result = create_account()
    print(f"Phone:{result['phone']}/Password:{result['password']}/Name:{result['name']}/Cookies:{result['cookies']}")