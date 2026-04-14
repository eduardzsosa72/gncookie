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
# CLASE PRINCIPAL
# =============================================================================

class AmazonCreator:
    def __init__(self):
        self.fake = Faker()
        self.first_name = self.fake.first_name()
        self.last_name = self.fake.last_name()
        self.password = "dfbc1992"
        self.activation_id = None
        self.phone = None
        self.logger = beautiful_logger(f"amazon_{id(self)}")
        self.session = curl_cffi.Session(impersonate="chrome")
        self.session.trust_env = False
        if PROXY_URL:
            self.session.proxies = {"http": PROXY_URL, "https": PROXY_URL}
    
    # =========================================================================
    # MÉTODOS BASE
    # =========================================================================
    def req(self, method, url, **kw):
        kw.setdefault("timeout", 60)          # Aumentado a 60 segundos
        max_retries = 4                       # Más reintentos
        delays = [1, 2, 4, 8]
        for attempt in range(max_retries):
            try:
                return self.session.request(method, url, **kw)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                self.logger.warning(f"Retry {attempt + 1}/{max_retries}: {e}")
                time.sleep(delays[attempt])
    
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
    
    # =========================================================================
    # MÉTODOS DE COOKIES (MEJORADOS)
    # =========================================================================
    def get_all_cookies(self, final_response=None) -> dict:
        """
        Extrae TODAS las cookies de la sesión actual.
        Si se pasa final_response, también añade sus cookies.
        Prioriza el jar de la sesión (que acumula todas las cookies durante todo el flujo).
        """
        all_cookies = {}
        
        # 1. Cookies del jar de la sesión (más completas)
        try:
            jar = self.session.cookies.get_dict(domain=None, path=None) or {}
            all_cookies.update(jar)
        except Exception:
            try:
                jar = dict(self.session.cookies)
                all_cookies.update(jar)
            except Exception:
                pass
        
        # 2. Cookies de la respuesta final (si se proporciona)
        if final_response is not None:
            try:
                # Cookies directas del objeto response
                resp_cookies = dict(final_response.cookies)
                all_cookies.update(resp_cookies)
            except Exception:
                pass
            try:
                # Parsear manualmente set-cookie headers
                set_cookie_headers = []
                if hasattr(final_response.headers, "get_list"):
                    set_cookie_headers = final_response.headers.get_list("set-cookie")
                if not set_cookie_headers:
                    raw = final_response.headers.get("set-cookie", "")
                    if raw:
                        set_cookie_headers = [raw]
                for sc in set_cookie_headers:
                    pair = sc.split(";")[0].strip()
                    if "=" in pair:
                        k, _, v = pair.partition("=")
                        all_cookies[k.strip()] = v.strip()
            except Exception:
                pass
        
        # 3. Formatear las cookies: añadir comillas a session-token y x-main
        formatted = {}
        for k, v in all_cookies.items():
            if not k or not v:
                continue
            if k in ("session-token", "x-main"):
                inner = v.strip('"')
                formatted[k] = f'"{inner}"'
            else:
                formatted[k] = v
        
        return formatted
    
    def extract_cookie_string(self, cookies_dict) -> str:
        """Convierte un diccionario de cookies en un string separado por ;"""
        return ";".join(f"{k}={v}" for k, v in cookies_dict.items())
    
    # =========================================================================
    # MÉTODOS DE HEROSMS
    # =========================================================================
    def herosms_api(self, action, **params):
        params["api_key"] = os.getenv("HEROSMS_KEY")
        params["action"] = action
        for attempt in range(2):
            try:
                r = self.session.get("https://hero-sms.com/stubs/handler_api.php", params=params, timeout=20)
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
    # FLUJO PRINCIPAL DE CREACIÓN (igual hasta el final, pero la extracción de cookies es más robusta)
    # =========================================================================
    def create_account(self) -> dict:
        start_time = time.time()
        UA = "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        
        self.logger.info("[0] CREATION OF ACCOUNT...")
        self.logger.info(f"  first_name={self.first_name}")
        self.logger.info(f"  last_name={self.last_name}")
        
        # ------------------------------------------------------------
        # PASO 1: GET /ap/signin
        # ------------------------------------------------------------
        self.logger.info("[1] GET /ap/signin...")
        r1 = self.req(
            "GET", "https://www.amazon.com/ap/signin",
            headers={"User-Agent": UA, "Upgrade-Insecure-Requests": "1",
                     "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
                     "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Linux"', "device-memory": "8",
                     "sec-ch-device-memory": "8", "dpr": "1", "sec-ch-dpr": "1",
                     "viewport-width": "548", "sec-ch-viewport-width": "548",
                     "ect": "3g", "rtt": "400", "downlink": "1.35"},
            params={
                "showRememberMe": "true", "openid.pape.max_auth_age": "0",
                "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
                "siteState": "135-4789514-8844217", "language": "en_US",
                "pageId": "amzn_prime_video_ww",
                "openid.return_to": "https://na.primevideo.com/auth/return/ref=av_auth_ap?_t=placeholder&location=/",
                "prevRID": "AQDQ2AF57Y6W3GM45ABJ",
                "openid.assoc_handle": "amzn_prime_video_sso_us",
                "openid.mode": "checkid_setup", "prepopulatedLoginId": "", "failedSignInCount": "0",
                "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
                "openid.ns": "http://specs.openid.net/auth/2.0"
            }
        )
        self.logger.info(f"  status={r1.status_code}")
        h1 = BeautifulSoup(r1.text, _PARSER)
        siteState1 = self.bs_val(h1, "siteState")
        returnTo1 = self.bs_val(h1, "openid.return_to")
        prevRID1 = self.bs_val(h1, "prevRID")
        
        # ------------------------------------------------------------
        # PASO 2: GET /ap/register
        # ------------------------------------------------------------
        self.logger.info("[2] GET /ap/register...")
        r2 = self.req(
            "GET", "https://www.amazon.com/ap/register",
            headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                     "Accept-Language": "en-US,en;q=0.9", "Connection": "keep-alive",
                     "Upgrade-Insecure-Requests": "1", "Sec-Fetch-Dest": "document",
                     "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin",
                     "Sec-Fetch-User": "?1", "Pragma": "no-cache", "Cache-Control": "no-cache"},
            params={
                "showRememberMe": "true", "openid.pape.max_auth_age": "0",
                "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
                "siteState": siteState1, "language": "en_US", "pageId": "amzn_prime_video_ww",
                "openid.return_to": returnTo1, "prevRID": prevRID1,
                "openid.assoc_handle": "amzn_prime_video_sso_us", "openid.mode": "checkid_setup",
                "prepopulatedLoginId": "", "failedSignInCount": "0",
                "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
                "openid.ns": "http://specs.openid.net/auth/2.0"
            }
        )
        self.logger.info(f"  status={r2.status_code}")
        h2 = BeautifulSoup(r2.text, _PARSER)
        appActionToken2 = self.bs_val(h2, "appActionToken")
        return_to2 = self.bs_val(h2, "openid.return_to")
        prevRID2 = self.bs_val(h2, "prevRID")
        siteState2 = self.bs_val(h2, "siteState")
        workflowState2 = self.bs_val(h2, "workflowState")
        csrf2 = self.bs_val(h2, "anti-csrftoken-a2z")
        
        # ------------------------------------------------------------
        # HeroSMS: obtener número
        # ------------------------------------------------------------
        if not self.herosms_get_number():
            raise Exception("Failed to get phone number from HeroSMS")
        self.logger.info(f"  Using HeroSMS phone: {self.phone}")
        
        # ------------------------------------------------------------
        # PASO 3: POST /ap/register
        # ------------------------------------------------------------
        self.logger.info("[3] POST /ap/register...")
        data_json = generate_spoofed_auth(
            user_agent=UA, location=r2.url, email=self.phone,
            name=f"{self.first_name} {self.last_name}", password=self.password,
            html_b64=base64.b64encode(r2.text.encode("utf-8")).decode("utf-8")
        )
        fp_amz = data_json.metadata1
        passwords = ["AYAAFHtMjg0m8dFzdfdfXbYsbMcAAAABAAZzaTptZDUAIDk3MzkwMGFkZGIwNjFmYmU1YmI0ZWE4NzFlOWQ4MTYxAQCi6X/1M6Zr1dn9EWQ7/02Je++VREFWrqaMIlQViT94RHNbRRmCeZlx14XnmwQfHNwYm6z8Tchq8e+Qt+ARlsnQT4gYGqyHOBN+tpv8G6LklNtEGbILENZCio63RQgFL4+pX7c6A4Ntp/K3JIhe9iZEGua7FeBtLJudofTgD3SkBLU9TtpsNsoIi037DrajpLxktt9HHfLArys3fNNG2rig27ityg0+Ril14FaeGlKCzv2E7fsLPWQY0UeJYcDeHUlHD7StPz1jrJLQrYoCZvAa3EivQbnH+Y+PIRiy40CF5cVU8h5TjCq0WJKuQQEvrIcMzsDO2YISY/cEoIF9EDOGAgAAAAAMAAAACQAAAAAAAAAAAAAAAI9jr/feb7Rl1PMY2r/Kw1T/////AAAAAQAAAAAAAAAAAAAAAQAAAAjGC3HuggWEnXynhfdnu1h1R8tZH47PRR8="]
        encrypted_pwd = random.choice(passwords)
        r3 = self.req(
            "POST", "https://www.amazon.com/ap/register",
            headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": r2.url},
            data={
                "appActionToken": appActionToken2, "appAction": "REGISTER",
                "openid.return_to": return_to2, "prevRID": prevRID2,
                "siteState": siteState2, "workflowState": workflowState2,
                "anti-csrftoken-a2z": csrf2,
                "customerName": f"{self.first_name} {self.last_name}", "countryCode": "US",
                "email": self.phone, "encryptedPwd": encrypted_pwd,
                "metadata1": fp_amz, "encryptedPasswordExpected": ""
            }
        )
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
        cvf_form_action = self.find_between(r3.text, 'id="cvf-aamation-challenge-form" method="post" action="', '"')
        if not cvf_form_action:
            m = re.search(r'action="(/[^"]*cvf[^"]*)"', r3.text, re.IGNORECASE)
            cvf_form_action = m.group(1) if m else "/ap/cvf/verify"
        if not data_context:
            raise Exception("Missing data-context")
        
        # ------------------------------------------------------------
        # PASO 4: GET /aaut/verify/cvf
        # ------------------------------------------------------------
        self.logger.info("[4] GET /aaut/verify/cvf...")
        options4 = json.dumps({
            "clientData": data_context,
            "challengeType": "WAF_ADVERSARIAL_SYNTHETIC_GRID_V2_LEVEL_1",
            "locale": "en-US", "externalId": data_ext_id,
            "enableHeaderFooter": False, "enableBypassMechanism": False,
            "enableModalView": False, "eventTrigger": None,
            "aaExternalToken": None, "forceJsFlush": False, "aamationToken": None
        }, separators=(",", ":"))
        r4 = self.req(
            "GET", "https://www.amazon.com/aaut/verify/cvf",
            headers={"User-Agent": UA, "Referer": r3.url},
            params={"options": options4}
        )
        self.logger.info(f"  status={r4.status_code}")
        ctx4 = {}
        raw4 = r4.headers.get("amz-aamation-resp")
        if raw4:
            try:
                ctx4 = json.loads(raw4)
            except:
                pass
        session_token4 = ctx4.get("sessionToken", "")
        client_side_ctx4 = ctx4.get("clientSideContext", "")
        problem_version = self.find_between(r4.text, '"problem":"', '"')
        captcha_id = self.find_between(r4.text, '"id":"', '"')
        captcha_url = self.find_between(r4.text, '<script src="', '"')
        captcha_domain = self.find_between(captcha_url, "https://", "/ait/") if captcha_url else None
        if not captcha_domain:
            raise Exception("Missing captcha domain")
        
        # ------------------------------------------------------------
        # PASO 5-7: Resolver captcha
        # ------------------------------------------------------------
        captcha_voucher = None
        for attempt in range(1, 4):
            self.logger.info(f"\n[Intento {attempt}/3] Resolviendo captcha...")
            r5 = self.req(
                "GET", f"https://{captcha_domain}/ait/ait/ait/problem",
                headers={"User-Agent": UA, "Referer": "https://www.amazon.com/"},
                params={
                    "kind": "visual", "domain": "www.amazon.com", "locale": "en-us",
                    "problem": problem_version, "num_solutions_required": "1", "id": captcha_id
                }
            )
            prob = r5.json()
            assets = prob.get("assets", {})
            target_raw = assets.get("target", "")
            images_raw = assets.get("images", "[]")
            hmac_tag = prob.get("hmac_tag", "")
            state5 = prob.get("state", {})
            iv5 = state5.get("iv", "")
            payload5 = state5.get("payload", "")
            key5 = prob.get("key", "")
            images = json.loads(images_raw) if isinstance(images_raw, str) else images_raw
            target = re.sub(r'[\[\]"\']', "", target_raw).replace("_", " ").strip()
            if not images or not target:
                raise Exception("Missing images or target")
            solution = capsolver.solve({
                "type": "AwsWafClassification",
                "question": f"aws:grid:{target}",
                "images": images,
            })
            if not solution or not solution.get("objects"):
                raise Exception("CapSolver failed")
            r7 = self.req(
                "POST", f"https://{captcha_domain}/ait/ait/ait/verify",
                headers={"User-Agent": UA, "Content-Type": "text/plain;charset=UTF-8"},
                json={
                    "hmac_tag": hmac_tag,
                    "state": {"iv": iv5, "payload": payload5},
                    "key": key5,
                    "client_solution": solution["objects"],
                    "metrics": {"solve_time_millis": random.randint(8000, 20000)},
                    "locale": "en-us"
                }
            )
            if r7.json().get("success"):
                captcha_voucher = r7.json().get("captcha_voucher", "")
                break
            elif attempt == 3:
                raise Exception("Captcha verification failed")
        
        # ------------------------------------------------------------
        # PASO 8: GET /aaut/verify/cvf/{captcha_id}
        # ------------------------------------------------------------
        r8 = self.req(
            "GET", f"https://www.amazon.com/aaut/verify/cvf/{captcha_id}",
            headers={"User-Agent": UA},
            params={
                "context": client_side_ctx4,
                "options": options4,
                "response": f'{{"challengeType":"WAF_ADVERSARIAL_SYNTHETIC_GRID_V2_LEVEL_1","data":"\\"{captcha_voucher}\\""}}'
            }
        )
        ctx8 = {}
        raw8 = r8.headers.get("amz-aamation-resp")
        if raw8:
            try:
                ctx8 = json.loads(raw8)
            except:
                pass
        final_session_token = ctx8.get("sessionToken", "") or session_token4
        
        # ------------------------------------------------------------
        # PASO 9: POST /ap/cvf/verify
        # ------------------------------------------------------------
        token_t = self.find_between(return_to3, "ref=av_auth_ap?_t=", "&") or self.find_between(return_to3, "_t=", "&")
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
        r9 = self.req(
            "POST",
            "https://www.amazon.com" + (cvf_form_action if cvf_form_action.startswith("/") else "/ap/cvf/verify"),
            headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": r3.url},
            data=post_data,
            allow_redirects=True
        )
        
        # Manejar mobile claim conflict si ocurre
        if "mobileclaimconflict" in r9.url.lower() or "mobileclaimconflict" in r9.text.lower():
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
                headers={"user-agent": UA},
                params=params,
                data=data,
                allow_redirects=True
            )
        
        # ------------------------------------------------------------
        # PASO 10: Detectar OTP y completar
        # ------------------------------------------------------------
        h9 = BeautifulSoup(r9.text, _PARSER)
        url9 = r9.url
        is_otp, otp_fields = self.detect_otp_page(h9, url9)
        
        if is_otp:
            self.logger.info(f"\n[SMS] OTP DETECTADO")
            form_action, form_inputs = self.extract_form_data(h9, url9, form_id="auth-pv-form")
            if not form_action:
                form_action, form_inputs = self.extract_form_data(h9, url9)
            otp_code = self.herosms_poll_code()
            if not otp_code:
                raise Exception("No OTP code")
            otp_data = form_inputs.copy()
            for field in otp_fields:
                otp_data[field] = otp_code
            new_csrf = self.bs_val(h9, "anti-csrftoken-a2z")
            if new_csrf:
                otp_data["anti-csrftoken-a2z"] = new_csrf
            otp_data["metadata1"] = ""
            r_otp = self.req("POST", form_action, data=otp_data, allow_redirects=True)
            # Ir a direcciónes para completar perfil
            r_otp = self.req("GET", "https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post", allow_redirects=True)
            addresss = BeautifulSoup(r_otp.text, _PARSER)
            csrf_token = self.bs_val(addresss, "csrfToken") or new_csrf
            # (Aquí se podría enviar la dirección, pero no es obligatorio para las cookies)
            # Simplemente navegamos a una página que sabemos que establece muchas cookies
            # Vamos a la página de inicio para obtener todas las cookies
            r_home = self.req("GET", "https://www.amazon.com/ref=nav_logo", allow_redirects=True)
            final_url = r_home.url
        else:
            final_url = url9
        
        # ------------------------------------------------------------
        # EXTRAER TODAS LAS COOKIES (el objetivo principal)
        # ------------------------------------------------------------
        self.logger.info("\n[12] Extrayendo TODAS las cookies de la sesión...")
        # Hacemos una petición final a una página que sabemos que establece muchas cookies (ej: tu cuenta)
        # Esto asegura que se hayan enviado todas las cookies posibles.
        try:
            # Petición a una página que requiere autenticación y devuelve muchas cookies
            r_final = self.req("GET", "https://www.amazon.com/gp/yourstore/home", timeout=45, allow_redirects=True)
            self.logger.info(f"  Página de cuenta status={r_final.status_code}")
        except Exception as e:
            self.logger.warning(f"No se pudo acceder a /gp/yourstore/home: {e}")
            r_final = None
        
        # Obtener todas las cookies acumuladas hasta ahora
        all_cookies_dict = self.get_all_cookies(final_response=r_final)
        cookie_str = self.extract_cookie_string(all_cookies_dict)
        
        self.logger.info(f"  Total cookies extraídas: {len(all_cookies_dict)}")
        self.logger.info(f"  Cookies: {list(all_cookies_dict.keys())}")
        
        # Guardar en archivo
        output_line = f"Phone:{self.phone}/Password:{self.password}/Name:{self.first_name} {self.last_name}/Cookies:{cookie_str}"
        with open("cookies.txt", "a") as f:
            f.write(output_line + "\n\n")
        print(output_line)
        
        self.herosms_finish()
        elapsed_time = time.time() - start_time
        
        return {
            "phone": self.phone,
            "password": self.password,
            "name": f"{self.first_name} {self.last_name}",
            "cookies": cookie_str,
            "cookies_dict": all_cookies_dict,
            "elapsed": round(elapsed_time, 2)
        }


def create_account() -> dict:
    creator = AmazonCreator()
    return creator.create_account()


if __name__ == "__main__":
    result = create_account()
    print(f"Phone:{result['phone']}/Password:{result['password']}/Name:{result['name']}/Cookies:{result['cookies']}")