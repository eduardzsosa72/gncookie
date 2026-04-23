import os, re, sys, json, time, base64, random, uuid
import urllib3
import urllib.parse
from curl_cffi import AsyncSession
import capsolver
import structlog
from structlog import get_logger
from faker import Faker
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import asyncio
from sms_service import HeroSMS

load_dotenv()

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Constantes globales (no modificables por instancia)
CAPSOLVER_KEY = os.getenv("CAPSOLVER_KEY")
PROXY_URL = os.getenv("REQ_PROXY")
HEROSMS_KEY = os.getenv("HEROSMS_KEY")
HEROSMS_COUNTRY = os.getenv("HEROSMS_COUNTRY", "us")
DEBUG_SAVE = os.getenv("DEBUG_SAVE", "false").lower() == "true"

capsolver.api_key = CAPSOLVER_KEY
faker_instance = Faker()

# ========== CLASE PRINCIPAL ==========
class AmazonCreator:
    def __init__(self):
        self.instance_id = uuid.uuid4().hex[:8]
        self.logger = get_logger(f"amazon_{self.instance_id}")
        self.fake = Faker()
        self.first_name = self.fake.first_name()
        self.last_name = self.fake.last_name()
        self.full_name = f"{self.first_name} {self.last_name}"
        self.address = {
            "street": "Street23",
            "city": "New York",
            "state": "NY",
            "zip": "10081",
            "country": "US"
        }
        self.password = f"Pass{random.randint(1000, 9999)}{uuid.uuid4().hex[:8]}"
        self.activation_id = None
        self.phone = None
        self.phone_without_code = None
        self.session = None
        if DEBUG_SAVE:
            self.debug_dir = f"debug_outputs/{self.instance_id}"
            os.makedirs(self.debug_dir, exist_ok=True)
        else:
            self.debug_dir = None

    def save(self, filename, content):
        if not DEBUG_SAVE or not self.debug_dir:
            return
        path = os.path.join(self.debug_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.logger.info(f"Saved: {path}")

    def find_between(self, data, first, last):
        try:
            return data.split(first)[1].split(last)[0]
        except:
            return None

    def bs_val(self, html, name, load_html=False, default=None):
        if load_html:
            html = BeautifulSoup(html, "lxml")
        el = html.find("input", {"name": name})
        if el:
            return el.get("value", default or "")
        return default or ""

    def extract_resend_url(self, html_obj):
        for script in html_obj.find_all("script"):
            if script.string and "resendUrl" in script.string:
                match = re.search(r'"resendUrl":"([^"]+)"', script.string)
                if match:
                    return match.group(1)
        return None

    def detect_otp_page(self, html_obj, url):
        if DEBUG_SAVE:
            self.save("otp_page.html", html_obj.prettify())
        url_lower = url.lower()
        is_pv_page = "/ap/pv" in url_lower
        otp_url = any(x in url_lower for x in ("otp", "cvf", "verify", "code", "auth"))
        otp_fields = []
        for inp in html_obj.find_all("input"):
            name = (inp.get("name") or "").lower()
            typ = (inp.get("type") or "text").lower()
            if typ == "hidden":
                continue
            if any(x in name for x in ("otp", "code", "pin", "cvf_captcha_input", "verificationCode")):
                otp_fields.append(inp.get("name"))
        page_text = html_obj.get_text().lower()
        otp_text = any(x in page_text for x in (
            "verification code", "codigo de verificacion", "Enter the OTP", "Enter the code",
            "We texted you", "We sent a code", "check your phone", "SMS"
        ))
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

    # ---------- SMS ----------
    async def order_hero_sms_number(self, sms_service):
        number_info = await sms_service.getNumberV2(service="am", country=HEROSMS_COUNTRY)
        if number_info and number_info.get("phoneNumber"):
            phone_number = number_info["phoneNumber"]
            activation_id = number_info["activationId"]
            cost = number_info["activationCost"]
            self.logger.info(f"✅ Numero Ordenado", activation_id=activation_id, phone_number=phone_number, cost=cost)
            self.phone = phone_number
            self.activation_id = activation_id
            self.phone_without_code = phone_number[1:]
            return True
        else:
            self.logger.warning("⚠️ No se pudo ordenar un numero")
            return False

    async def check_hero_sms_code(self, sms_service, attempts=60, delay=1):
        attempt_count = 0
        WAITING_STATES = {"STATUS_WAIT_CODE", "STATUS_WAIT_RETRY", "STATUS_WAIT_RESEND"}
        CANCEL_STATES = {"STATUS_CANCEL", "NO_ACTIVATION"}
        while True:
            try:
                if attempts is not None and attempt_count >= attempts:
                    return None
                status = await sms_service.getStatus(id=self.activation_id)
                status_info = sms_service.activationStatus(status)
                self.logger.info(f"📡 Estado actual: {status_info}")
                if status in WAITING_STATES:
                    attempt_count += 1
                    await asyncio.sleep(delay)
                    continue
                if status in CANCEL_STATES:
                    return None
                if "STATUS_OK" in status:
                    code = status.replace("STATUS_OK:", "").strip()
                    if code:
                        self.logger.info(f"SMS recibido: {code}")
                        return code
                    else:
                        return None
                attempt_count += 1
                await asyncio.sleep(delay)
            except Exception as e:
                self.logger.error(f"Error verificando estado: {e}")
                attempt_count += 1
                await asyncio.sleep(delay)

    # ---------- Extracción de cookies (solo amazon.com) ----------
    def extract_amazon_cookies(self) -> tuple[str, list]:
        cookies_dict = {}
        try:
            # curl_cffi guarda cookies en session.cookies._cookies
            if hasattr(self.session.cookies, '_cookies'):
                for domain, paths in self.session.cookies._cookies.items():
                    if 'amazon.com' not in domain:
                        continue
                    for path, cookies in paths.items():
                        for name, cookie in cookies.items():
                            value = cookie.value if hasattr(cookie, 'value') else cookie
                            cookies_dict[name] = value
            else:
                cookies_dict = self.session.cookies.get_dict(domain='.amazon.com') or {}
                if not cookies_dict:
                    cookies_dict = self.session.cookies.get_dict(domain='amazon.com') or {}
        except Exception as e:
            self.logger.error(f"Error extrayendo cookies: {e}")
            for cookie in self.session.cookies:
                if hasattr(cookie, 'name') and hasattr(cookie, 'value'):
                    domain = getattr(cookie, 'domain', '')
                    if 'amazon.com' in domain:
                        cookies_dict[cookie.name] = cookie.value

        # Reemplazar comillas dobles por simples
        cookie_parts = []
        for k, v in cookies_dict.items():
            if v:
                v_clean = v.replace('"', "'")
                cookie_parts.append(f"{k}={v_clean}")
        cookie_str = "; ".join(cookie_parts)
        cookies_list = [{"name": k, "value": v} for k, v in cookies_dict.items()]
        self.logger.info(f"Cookies extraídas: {len(cookies_dict)} items")
        return cookie_str, cookies_list

    # ---------- Función principal ----------
    async def create_account(self) -> dict:
        start_time = time.time()
        self.logger.info("Starting Creation of Account - From Prime Video")
        self.logger.info("Data", first_name=self.first_name, last_name=self.last_name, password=self.password)

        sms_service = HeroSMS(HEROSMS_KEY)
        sms_task = asyncio.create_task(self.order_hero_sms_number(sms_service))

        self.session = AsyncSession(retry=3, impersonate="firefox144")
        self.session.trust_env = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-GPC': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Priority': 'u=0, i',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
        })
        if PROXY_URL:
            self.session.proxies = {"http": PROXY_URL, "https": PROXY_URL}

        # Paso 1: Redirección Prime Video
        params = {'signin': '1', 'returnUrl': '/offers/nonprimehomepage/ref=dv_web_force_root'}
        response = await self.session.get('https://www.primevideo.com/auth-redirect/ref=atv_nb_sign_in', params=params)
        self.save("prime_redirect.html", response.text)

        create_url_raw = self.find_between(response.text, 'createAccountSubmit" href="', '"')
        if not create_url_raw:
            return {"status": False, "error": "No create account URL found"}
        create_url = create_url_raw.replace("&amp;", "&")
        create_url = urllib.parse.unquote(create_url)

        new_url = self.find_between(create_url, "openid.return_to=", "&prevRID")
        site_state = urllib.parse.parse_qs(urllib.parse.urlparse(create_url).query)["siteState"][0]
        prev_rid = self.bs_val(response.text, "prevRID", load_html=True)

        params2 = {
            'showRememberMe': 'true',
            'openid.pape.max_auth_age': '0',
            'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
            'siteState': site_state,
            'language': 'en_US',
            'pageId': 'amzn_prime_video_ww',
            'openid.return_to': new_url,
            'prevRID': prev_rid,
            'openid.assoc_handle': 'amzn_prime_video_sso_us',
            'openid.mode': 'checkid_setup',
            'prepopulatedLoginId': '',
            'failedSignInCount': '0',
            'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select',
            'openid.ns': 'http://specs.openid.net/auth/2.0',
        }
        response = await self.session.get('https://www.amazon.com/ap/register', params=params2)
        self.save("register.html", response.text)
        self.logger.info("Response 2", url=response.url, status=response.status_code)

        # Esperar número SMS
        sms_ok = await sms_task
        if not sms_ok:
            return {"status": False, "error": "Failed to obtain SMS number"}

        # Paso 2: Enviar formulario de registro
        html_obj = BeautifulSoup(response.text, 'html.parser')
        app_action_token = self.bs_val(html_obj, "appActionToken")
        return_to = self.bs_val(html_obj, "openid.return_to")
        prev_rid = self.bs_val(html_obj, "prevRID")
        site_state = self.bs_val(html_obj, "siteState")
        workflow_state = self.bs_val(html_obj, "workflowState")
        anti_csrftoken_a2z = self.bs_val(html_obj, "anti-csrftoken-a2z")

        data = {
            'appActionToken': app_action_token,
            'appAction': 'REGISTER',
            'openid.return_to': return_to,
            'prevRID': prev_rid,
            'siteState': site_state,
            'workflowState': workflow_state,
            'anti-csrftoken-a2z': anti_csrftoken_a2z,
            'customerName': self.full_name,
            'countryCode': 'US',
            'email': self.phone_without_code,
            'password': self.password,
            'showPasswordChecked': 'true',
            'encryptedPasswordExpected': '',
        }
        response = await self.session.post('https://www.amazon.com/ap/register', data=data)
        self.logger.info("Response 3", url=response.url, status=response.status_code)
        self.save("register2.html", response.text)

        if "ARKOSE_" in response.text:
            return {"status": False, "error": "ARKOSE_ detected"}

        # Paso 3: WAF / captcha
        return_to3 = (self.bs_val(response.text, "openid.return_to", True) or return_to).replace("&amp;", "&")
        data_context_list = re.findall(r'"data-context":\s*\'({[^\']*})\'', response.text)
        data_context = data_context_list[0] if data_context_list else None
        if not data_context:
            data_context_list = re.findall(r'data-context="({[^"]*})"', response.text)
            data_context = data_context_list[0] if data_context_list else None

        data_ext_id = self.find_between(response.text, '"data-external-id": "', '"')
        if not data_ext_id:
            data_ext_id = self.find_between(response.text, 'data-external-id="', '"')
        csrf_token = self.bs_val(response.text, "anti-csrftoken-a2z", True)
        siteState = self.bs_val(response.text, "siteState", True)
        clientContext = self.bs_val(response.text, "clientContext", True)
        verifyToken = self.bs_val(response.text, "verifyToken", True)

        cvf_form_action = self.find_between(response.text, 'id="cvf-aamation-challenge-form" method="post" action="', '"')
        if not cvf_form_action:
            m = re.search(r'action="(/[^"]*cvf[^"]*)"', response.text, re.IGNORECASE)
            cvf_form_action = m.group(1) if m else "/ap/cvf/verify"

        self.logger.info("Data context", data_context=data_context, clientContext=clientContext)

        option_data = json.dumps({
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
        }, separators=(",", ":"))

        response = await self.session.get("https://www.amazon.com/aaut/verify/cvf", params={"options": option_data})
        self.save("register3.html", response.text)
        self.logger.info("Status", status=response.status_code, url=response.url)

        ctx4 = {}
        raw4 = response.headers.get("amz-aamation-resp")
        if raw4:
            try:
                ctx4 = json.loads(raw4)
            except Exception:
                return {"status": False, "error": "Error parsing amz-aamation-resp"}

        session_token4 = ctx4.get("sessionToken", "")
        client_side_ctx4 = ctx4.get("clientSideContext", "")
        problem_version = self.find_between(response.text, '"problem":"', '"')
        captcha_id = self.find_between(response.text, '"id":"', '"')
        captcha_url = self.find_between(response.text, '<script src="', '"')
        captcha_domain = self.find_between(captcha_url, "https://", "/ait/") if captcha_url else None
        self.logger.info("Data", session_token=session_token4, client_side_ctx=client_side_ctx4, problem=problem_version, captcha_id=captcha_id, captcha_domain=captcha_domain)

        # Resolver captcha
        max_retries = 3
        captcha_voucher = None
        for attempt in range(1, max_retries + 1):
            self.logger.info("Attempt", attempt=attempt, max_retries=max_retries)
            if attempt == max_retries:
                return {"status": False, "error": "Captcha failed after retries"}

            resp = await self.session.get(
                f"https://{captcha_domain}/ait/ait/ait/problem",
                params={"kind": "visual", "domain": "www.amazon.com", "locale": "en-us",
                        "problem": problem_version, "num_solutions_required": "1", "id": captcha_id}
            )
            prob = resp.json()
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
            if not images or not target:
                return {"status": False, "error": "No images or target for captcha"}

            try:
                solution = await asyncio.to_thread(
                    capsolver.solve,
                    {"type": "AwsWafClassification", "question": f"aws:grid:{target}", "images": images}
                )
            except Exception as e:
                return {"status": False, "error": f"CapSolver error: {e}"}
            if not solution or not solution.get("objects"):
                return {"status": False, "error": "CapSolver returned no objects"}
            solution_objects = solution["objects"]
            resp2 = await self.session.post(
                f"https://{captcha_domain}/ait/ait/ait/verify",
                json={"hmac_tag": hmac_tag, "state": {"iv": iv5, "payload": payload5}, "key": key5,
                      "client_solution": solution_objects, "metrics": {"solve_time_millis": random.randint(8000, 20000)},
                      "locale": "en-us"}
            )
            r7j = resp2.json()
            if r7j.get("success"):
                captcha_voucher = r7j.get("captcha_voucher", "")
                self.logger.info("Captcha resuelto")
                break
            else:
                self.logger.warning("Verificación falló")
                continue

        # Canjear voucher
        resp3 = await self.session.get(
            f"https://www.amazon.com/aaut/verify/cvf/{captcha_id}",
            params={"context": client_side_ctx4, "options": option_data,
                    "response": '{"challengeType":"WAF_ADVERSARIAL_SYNTHETIC_GRID_V2_LEVEL_1","data":"\\"' + captcha_voucher + '\\""}'}
        )
        self.save("register4.html", resp3.text)
        ctx8 = {}
        raw8 = resp3.headers.get("amz-aamation-resp")
        if raw8:
            try:
                ctx8 = json.loads(raw8)
            except: pass
        final_session_token = ctx8.get("sessionToken", "") or session_token4

        # POST /ap/cvf/verify
        token_t = self.find_between(return_to3, "ref=av_auth_ap?_t=", "&") or self.find_between(return_to3, "_t=", "&")
        if token_t:
            openid_return_to = f"https://na.primevideo.com/auth/return/ref=av_auth_ap?_t={token_t}&location=/?ref_%3Datv_auth_pre"
        else:
            openid_return_to = return_to3

        post_data = {
            "anti-csrftoken-a2z": csrf_token,
            "cvf_aamation_response_token": final_session_token,
            "cvf_captcha_captcha_action": "verifyAamationChallenge",
            "cvf_aamation_error_code": "",
            "clientContext": clientContext,
            "openid.pape.max_auth_age": "0",
            "openid.return_to": openid_return_to,
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.assoc_handle": "amzn_prime_video_sso_us",
            "openid.mode": "checkid_setup",
            "siteState": siteState,
            "language": "en_US",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "pageId": "amzn_prime_video_ww",
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "verifyToken": verifyToken,
        }
        response = await self.session.post(
            "https://www.amazon.com" + (cvf_form_action if cvf_form_action.startswith("/") else "/ap/cvf/verify"),
            data=post_data,
            allow_redirects=True,
        )
        self.save("step9_cvf_verify.html", response.text)

        # Manejar conflicto de número móvil
        if (response.status_code == 200 and "mobileclaimconflict" in response.url.lower()) or "mobileclaimconflict" in response.text.lower():
            self.logger.error("Mobile claim conflict")
            html_obj = BeautifulSoup(response.text, "lxml")
            form_action, form_inputs = self.extract_form_data(html_obj, response.url, "ap_account_conflict_warning_customer_actions")
            if form_action:
                response = await self.session.post(form_action, data=form_inputs)
                self.save("mobile_claim_conflict.html", response.text)

        # OTP
        h9 = BeautifulSoup(response.text, 'lxml')
        is_otp, otp_fields = self.detect_otp_page(h9, response.url)
        if not is_otp:
            return {"status": False, "error": "OTP page not found"}

        self.logger.info("OTP DETECTED")
        form_action, form_inputs = self.extract_form_data(h9, response.url, form_id="auth-pv-form")
        if not form_action:
            form_action, form_inputs = self.extract_form_data(h9, response.url)

        otp_code = await self.check_hero_sms_code(sms_service)
        if otp_code is None:
            return {"status": False, "error": "SMS code timeout"}

        if otp_code and otp_fields and form_action:
            otp_data = form_inputs.copy()
            for field in otp_fields:
                otp_data[field] = otp_code
            new_csrf = self.bs_val(h9, "anti-csrftoken-a2z")
            if new_csrf:
                otp_data["anti-csrftoken-a2z"] = new_csrf
            otp_data["metadata1"] = ""

            response = await self.session.post(form_action, data=otp_data, allow_redirects=True)
            self.save("step10_otp_submit.html", response.text)

            # Añadir dirección
            response = await self.session.get("https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post", allow_redirects=True)
            self.save("step12_amazon.html", response.text)

            addresss = BeautifulSoup(response.text, 'lxml')
            csrf_token = self.bs_val(addresss, "csrfToken") or new_csrf
            address_ui_widgets_previous_address_form_state_token = self.bs_val(addresss, "address-ui-widgets-previous-address-form-state-token")
            address_ui_widgets_obfuscated_customerId = self.bs_val(addresss, "address-ui-widgets-obfuscated-customerId")
            address_ui_widgets_csrfToken = self.bs_val(addresss, "address-ui-widgets-csrfToken")
            address_ui_widgets_form_load_start_time = self.bs_val(addresss, "address-ui-widgets-form-load-start-time")
            address_ui_widgets_clickstream_related_request_id = self.bs_val(addresss, "address-ui-widgets-clickstream-related-request-id")
            address_ui_widgets_address_wizard_interaction_id = self.bs_val(addresss, "address-ui-widgets-address-wizard-interaction-id")

            data_address = {
                "csrfToken": csrf_token,
                "addressID": "",
                "address-ui-widgets-countryCode": "US",
                "address-ui-widgets-enterAddressFullName": self.full_name,
                "address-ui-widgets-enterAddressPhoneNumber": self.phone,
                "address-ui-widgets-enterAddressLine1": self.address["street"],
                "address-ui-widgets-enterAddressLine2": "",
                "address-ui-widgets-enterAddressCity": self.address["city"],
                "address-ui-widgets-enterAddressStateOrRegion": self.address["state"],
                "address-ui-widgets-enterAddressPostalCode": self.address["zip"],
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

            response = await self.session.post("https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post", data=data_address, params={"ref": "ya_address_book_add_post"})
            self.logger.info("ADDRESS response", url=response.url, status=response.status_code)
            self.save("step12_amazon.html", response.text)

            # Consolidar sesión
            self.logger.info("Consolidando sesión en amazon.com...")
            urls_amazon = [
                "https://www.amazon.com/",
                "https://www.amazon.com/gp/css/order-history",
                "https://www.amazon.com/hz/wishlist/intro",
                "https://www.amazon.com/gp/css/account/wallet",
                "https://www.amazon.com/gp/css/account/addresses/view.html",
                "https://www.amazon.com/",
            ]
            for url in urls_amazon:
                await self.session.get(url, allow_redirects=True)
                await asyncio.sleep(1.5)

            await self.session.get("https://www.amazon.com/", allow_redirects=True)
            await asyncio.sleep(2)

            # Extraer cookies
            cookie_str, cookies_list = self.extract_amazon_cookies()

            # Verificar cookies críticas
            critical = ['session-id', 'ubid-main', 'session-token', 'at-main', 'x-main']
            missing = [c for c in critical if c not in cookie_str]
            if missing:
                self.logger.warning(f"Faltan cookies críticas: {missing}")
            else:
                self.logger.info("Todas las cookies críticas están presentes")

            return {
                "status": True,
                "email": self.phone_without_code,
                "password": self.password,
                "phone": self.phone,
                "cookies": cookie_str,
                "cookies_list": cookies_list,
                "name": self.full_name,
                "address": self.address,
                "creation_time": round(time.time() - start_time, 2),
                "activation_id": self.activation_id
            }
        else:
            return {"status": False, "error": "OTP submission failed"}

# ========== FUNCIÓN DE ENTRADA (para mantener compatibilidad) ==========
async def create() -> dict:
    creator = AmazonCreator()
    return await creator.create_account()

if __name__ == "__main__":
    result = asyncio.run(create())
    print(json.dumps(result, indent=2))