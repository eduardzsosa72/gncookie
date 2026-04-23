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

logger = get_logger("amazon_gen")

sms_service = HeroSMS(os.getenv("HEROSMS_KEY"))

faker_instance = Faker()

# -- CONFIG -------------------------------------------------------------------
capsolver.api_key = os.getenv("CAPSOLVER_KEY")
PROXY_URL = os.getenv("REQ_PROXY")

HEROSMS_ACTIVATION_ID = None
HEROSMS_PHONE = None


# -- HELPERS ------------------------------------------------------------------
def find_between(data, first, last):
    s = data.find(first)
    if s == -1:
        return None
    s += len(first)
    e = data.find(last, s)
    if e == -1:
        return None
    return data[s:e]


def bs_val(html, name, load_html=False, default=None):
    if load_html:
        html = BeautifulSoup(html, "lxml")
    el = html.find("input", {"name": name})
    if el:
        return el.get("value", default or "")
    return default or ""


def all_inputs(html) -> dict:
    result = {}
    for inp in html.find_all("input"):
        name = inp.get("name")
        val = inp.get("value", "")
        if name:
            result[name] = val
    return result


def save(filename, content):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("Saved", filename=filename)

def extract_resend_url(html_obj):
    for script in html_obj.find_all("script"):
        if script.string and "resendUrl" in script.string:
            match = re.search(r'"resendUrl":"([^"]+)"', script.string)
            if match:
                return match.group(1)
    return None


def detect_otp_page(html_obj, url):
    save("otp_page.html", html_obj.prettify())
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
        "verification code",
        "codigo de verificacion",
        "Enter the OTP",
        "Enter the code",
        "We texted you",
        "We sent a code",
        "check your phone",
        "SMS",
    ))
    return (is_pv_page or otp_url or otp_text or bool(otp_fields)), otp_fields


def extract_form_data(html_obj, url, form_id=None):
    if form_id:
        form = html_obj.find("form", {"id": form_id})
    else:
        form = html_obj.find("form")
    if not form:
        return None, {}
    action = form.get("action", "")
    logger.info("Action", action=action)
    if action and not action.startswith("http"):
        from urllib.parse import urljoin

        action = urljoin(url, action)
    inputs = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if name:
            inputs[name] = inp.get("value", "")
    return action, inputs


# sms

async def order_hero_sms_number():
    number_info = await sms_service.getNumberV2(
        service="am", country=os.getenv("HEROSMS_COUNTRY")
    )
    if number_info and number_info.get("phoneNumber"):
        phone_number = number_info["phoneNumber"]
        activation_id = number_info["activationId"]
        cost = number_info["activationCost"]
        logger.info(f"✅ Numero Ordenado", activation_id=activation_id, phone_number=phone_number, cost=cost)
        number_whitouth_code = phone_number[1:]
        return activation_id, phone_number, number_whitouth_code
    else:
        logger.warning("⚠️ No se pudo ordenar un numero")
        return None, None, None


async def check_hero_sms_code(activationId: int, attempts=60, delay: int = 1):
    attempt_count = 0

    # Mapeo de estados para mejor legibilidad
    WAITING_STATES = {"STATUS_WAIT_CODE", "STATUS_WAIT_RETRY", "STATUS_WAIT_RESEND"}
    CANCEL_STATES = {"STATUS_CANCEL", "NO_ACTIVATION"}

    while True:
        try:
            logger.info(
                f"Intento {attempt_count + 1}: Verificando estado para activación {activationId}..."
            )
            # Verificar límite de intentos
            if attempts is not None and attempt_count >= attempts:
                logger.info(
                    f"⚠️ Límite de intentos ({attempts}) alcanzado para activación {activationId}"
                )
                return None

            status = await sms_service.getStatus(id=activationId)
            status_info = sms_service.activationStatus(
                status
            )  # Descomentar si necesitas el mensaje
            logger.info(f"📡 Estado actual de activación {activationId}: {status_info}")

            # Estados de espera
            if status in WAITING_STATES:
                attempt_count += 1
                await asyncio.sleep(delay)
                continue

            # Estados de cancelación/error
            if status in CANCEL_STATES:
                logger.warning(f"Activación {activationId} cancelada. Estado: {status}")
                return None

            # Estado exitoso con código
            if "STATUS_OK" in status:
                code = status.replace("STATUS_OK:", "").strip()
                if code:
                    logger.info(f"SMS recibido: {code}")
                    return code
                else:
                    logger.warning(
                        f"Código vacío recibido para activación {activationId}"
                    )
                    return None

            # Estados desconocidos
            logger.warning(f"Estado desconocido: {status}")
            attempt_count += 1
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            logger.info(f"Espera cancelada para activación {activationId}")
            raise

        except Exception as e:
            logger.error(f"Error verificando estado: {e}")
            attempt_count += 1
            await asyncio.sleep(delay)

def extract_cookies_from_response(session, response) -> tuple[str, dict]:
    """
    Extraer TODAS las cookies del último request (home).

    - Extrae primero las cookies del Set-Cookie del response final
    - Luego complementa con el jar de la sesión (session.cookies)
    - session-token y x-main llevan comillas dobles
    - Sin espacios en el string final

    Returns: (cookie_str, cookie_dict)
    """
    # 1. Cookies del jar completo de la sesión (todas las peticiones)
    jar = {}
    try:
        # curl_cffi: .cookies es un jar con .get_dict()
        jar = session.cookies.get_dict(domain=None, path=None) or {}
    except Exception:
        try:
            jar = dict(session.cookies)
        except Exception:
            jar = {}

    # 2. Cookies del Set-Cookie del response final (las más frescas/completas)
    response_cookies = {}
    try:
        response_cookies = dict(response.cookies)
    except Exception:
        pass

    # Parsear Set-Cookie header manualmente si .cookies no trae todo
    try:
        set_cookie_headers = (
            response.headers.get_list("set-cookie")
            if hasattr(response.headers, "get_list")
            else []
        )
        if not set_cookie_headers:
            # requests / curl_cffi: puede ser una sola string o lista
            raw = response.headers.get("set-cookie", "")
            if raw:
                set_cookie_headers = [raw]
        for sc in set_cookie_headers:
            # Tomar solo el par nombre=valor (primera parte antes de ";")
            pair = sc.split(";")[0].strip()
            if "=" in pair:
                k, _, v = pair.partition("=")
                response_cookies[k.strip()] = v.strip()
    except Exception:
        pass

    # 3. Merge: jar base + response cookies (response tiene prioridad)
    merged = {**jar, **response_cookies}

    # 4. Procesar: comillas en session-token y x-main
    processed = {}
    for k, v in merged.items():
        if not k or not v:
            continue
        # Evitar duplicar comillas si ya las tiene
        if k in ("session-token", "x-main"):
            inner = v.strip('"')
            processed[k] = f'"{inner}"'
        else:
            processed[k] = v

    # 5. Construir string sin espacios
    cookie_str = "; ".join(f"{k}={v}" for k, v in processed.items())

    return cookie_str, processed

async def create():
    first_name = faker_instance.first_name()
    last_name = faker_instance.last_name()
    password = f"Pass{random.randint(1000, 9999)}{uuid.uuid4().hex[:8]}"
 
    logger.info("Starting Creation of Account - From Prime Video")
    logger.info("Data", first_name=first_name, last_name=last_name)

    logger.info("[0] CREATION OF ACCOUNT...")
    logger.info("Data", first_name=first_name, last_name=last_name, password=password)
    logger.info("[1] GET /ap/signin...")
    
    # Start ordering the SMS number concurrently
    sms_task = asyncio.create_task(order_hero_sms_number())

    user_agent = 'Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0'
    session = AsyncSession(retry=3, impersonate="firefox144")
    session.trust_env = False
    session.headers.update({
        'User-Agent': user_agent,
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

    session.proxies = {"http": PROXY_URL, "https": PROXY_URL}

    params = {
        'signin': '1',
        'returnUrl': '/offers/nonprimehomepage/ref=dv_web_force_root',
    }

    # ?ref=ya_address_book_add_post
    response = await session.get(
        'https://www.primevideo.com/auth-redirect/ref=atv_nb_sign_in',
        params=params
    )

    save("prime_redirect.html", response.text)
    
    create_url_raw = find_between(response.text, 'createAccountSubmit" href="', '"')
    # format url
    create_url = create_url_raw.replace("&amp;", "&")
    # urldecode
    create_url = urllib.parse.unquote(create_url)

    new_url = find_between(create_url, "openid.return_to=", "&prevRID")
    # get SiteState query from url 
    site_state = urllib.parse.parse_qs(urllib.parse.urlparse(create_url).query)["siteState"][0]
    prev_rid = bs_val(response.text, "prevRID", load_html=True)
    

    params = {
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

    response = await session.get('https://www.amazon.com/ap/register', params=params)

    save("register.html", response.text)
    logger.info("Response 2", url=response.url, status=response.status_code)


    # wait for order number task
    activation_id, phone_number, number_whitouth_code = await sms_task

    logger.info("HeroSMS", activation_id=activation_id, phone_number=phone_number, number_whitouth_code=number_whitouth_code)

    html_obj = BeautifulSoup(response.text, 'html.parser')
    app_action_token = bs_val(html_obj, "appActionToken")
    return_to = bs_val(html_obj, "openid.return_to")
    prev_rid = bs_val(html_obj, "prevRID")
    site_state = bs_val(html_obj, "siteState")
    workflow_state = bs_val(html_obj, "workflowState")
    anti_csrftoken_a2z = bs_val(html_obj, "anti-csrftoken-a2z")

    data = {
        'appActionToken': app_action_token,
        'appAction': 'REGISTER',
        'openid.return_to': return_to,
        'prevRID': prev_rid,
        'siteState': site_state,
        'workflowState': workflow_state,
        'anti-csrftoken-a2z': anti_csrftoken_a2z,
        'customerName': f"{first_name} {last_name}",
        'countryCode': 'US',
        'email': number_whitouth_code,
        'password': password,                    
        'showPasswordChecked': 'true',
        'encryptedPasswordExpected': '',         
    }

    response = await session.post('https://www.amazon.com/ap/register', data=data)

    logger.info("Response 3", url=response.url, status=response.status_code)
    save("register2.html", response.text)

    if "ARKOSE_" in response.text:
        logger.error("ARKOSE_ detected")
        # exit()

    return_to3 = (bs_val(response.text, "openid.return_to", True) or return_to).replace("&amp;", "&")
    data_context_list = re.findall(r'"data-context":\s*\'({[^\']*})\'', response.text)
    data_context = data_context_list[0] if data_context_list else None
    if not data_context:
        data_context_list = re.findall(r'data-context="({[^"]*})"', response.text)
        data_context = data_context_list[0] if data_context_list else None

    data_ext_id = find_between(response.text, '"data-external-id": "', '"')
    if not data_ext_id:
        data_ext_id = find_between(response.text, 'data-external-id="', '"')
    csrf_token = bs_val(response.text, "anti-csrftoken-a2z", True)
    siteState = bs_val(response.text, "siteState", True)
    clientContext = bs_val(response.text, "clientContext", True)
    verifyToken = bs_val(response.text, "verifyToken", True)

    cvf_form_action = find_between(
        response.text, 'id="cvf-aamation-challenge-form" method="post" action="', '"'
    )
    if not cvf_form_action:
        m = re.search(r'action="(/[^"]*cvf[^"]*)"', response.text, re.IGNORECASE)
        cvf_form_action = m.group(1) if m else "/ap/cvf/verify"
    
    logger.info("Data context", data_context=data_context, clientContext=clientContext)

    option_data = json.dumps(
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

    response = await session.get(
        "https://www.amazon.com/aaut/verify/cvf",
        params={"options": option_data},
    )

    save("register3.html", response.text)
    logger.info("Status", status=response.status_code, url=response.url)

    ctx4 = {}
    raw4 = response.headers.get("amz-aamation-resp")
    if raw4:
        try:
            ctx4 = json.loads(raw4)
        except Exception:
            logger.error("Error parsing amz-aamation-resp")
            sys.exit(1)

    session_token4 = ctx4.get("sessionToken", "")
    client_side_ctx4 = ctx4.get("clientSideContext", "")
    problem_version = find_between(response.text, '"problem":"', '"')
    captcha_id = find_between(response.text, '"id":"', '"')
    captcha_url = find_between(response.text, '<script src="', '"')
    captcha_domain = (
        find_between(captcha_url, "https://", "/ait/") if captcha_url else None
    )
    logger.info("Data", session_token=session_token4, client_side_ctx=client_side_ctx4, problem=problem_version, captcha_id=captcha_id, captcha_domain=captcha_domain)

    # =============================================================================
    # PASO 5 -- GET captcha problem
    # =============================================================================

    max_retries = 3
    captcha_voucher = None

    for attempt in range(1, max_retries + 1):
        logger.info("Attempt", attempt=attempt, max_retries=max_retries)

        if attempt == max_retries:
            logger.error("Failed", max_retries=max_retries)
            sys.exit(1)

        logger.info("[5] GET", url="/ait/ait/ait/problem")
        response = await session.get(
            f"https://{captcha_domain}/ait/ait/ait/problem",
            params={
                "kind": "visual",
                "domain": "www.amazon.com",
                "locale": "en-us",
                "problem": problem_version,
                "num_solutions_required": "1",
                "id": captcha_id,
            },
        )
        # save("step5_captcha_problem.html", response.text)
        logger.info("Status", status=response.status_code, url=response.url)

        prob = response.json()
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
        logger.info("Data", target=target, images=len(images))

        if not images or not target:
            logger.error("Sin imagenes o target")
            sys.exit(1)

        # =============================================================================
        # PASO 6 -- CapSolver
        # =============================================================================
        logger.info("[6] Resolviendo Captcha")
        try:
            solution = await asyncio.to_thread(
                capsolver.solve,
                {
                    "type": "AwsWafClassification",
                    "question": f"aws:grid:{target}",
                    "images": images,
                }
            )
            logger.info("Solution", solution=solution)
        except Exception as e:
            logger.error("error solving", error=e)
            sys.exit(1)

        if not solution or not solution.get("objects"):
            logger.error("Sin objetos", solution=len(solution))
            sys.exit(1)

        solution_objects = solution["objects"]
        logger.info("Solution", objects=len(solution_objects))

        # =============================================================================
        # PASO 7 -- POST captcha verify
        # =============================================================================
        logger.info("[7] POST", url="/ait/ait/ait/verify")
        response = await session.post(
            f"https://{captcha_domain}/ait/ait/ait/verify",
            json={
                "hmac_tag": hmac_tag,
                "state": {"iv": iv5, "payload": payload5},
                "key": key5,
                "client_solution": solution_objects,
                "metrics": {"solve_time_millis": random.randint(8000, 20000)},
                "locale": "en-us",
            },
        )
        # save("step7_captcha_verify.html", r7.text)
        logger.info("Status", status=response.status_code, url=response.url)

        r7j = response.json()
        if r7j.get("success"):
            captcha_voucher = r7j.get("captcha_voucher", "")
            logger.info("Captcha resuelto", captcha_voucher=captcha_voucher)
            break  # Salir del bucle si es exitoso
        else:
            logger.warning("Verificación falló", r7j=r7j)
            if attempt == max_retries:
                logger.error("Falló después de", max_retries=max_retries)
                sys.exit(1)
            continue


    # verify
    
    logger.info("[8] GET", url="/aaut/verify/cvf/{captcha_id}")
    response = await session.get(
        f"https://www.amazon.com/aaut/verify/cvf/{captcha_id}",
        params={
            "context": client_side_ctx4,
            "options": option_data,
            "response": '{"challengeType":"WAF_ADVERSARIAL_SYNTHETIC_GRID_V2_LEVEL_1","data":"\\"'
            + captcha_voucher
            + '\\""}',
        },
    )
    save("register4.html", response.text)
    logger.info("Status", status=response.status_code, url=response.url)

    ctx8 = {}
    raw8 = response.headers.get("amz-aamation-resp")
    if raw8:
        try:
            ctx8 = json.loads(raw8)
        except Exception:
            pass

    final_session_token = ctx8.get("sessionToken", "")
    final_client_ctx = ctx8.get("clientSideContext", "")
    logger.info(
        "sessionToken",
        final_session_token=final_session_token,
    )

    if not final_session_token:
        final_session_token = session_token4
        logger.warning("Fallback", final_session_token=final_session_token)

    # =============================================================================
    # PASO 9 -- POST /ap/cvf/verify
    # =============================================================================

    logger.info("[9] POST", url="/ap/cvf/verify")

    token_t = find_between(return_to3, "ref=av_auth_ap?_t=", "&") or find_between(
        return_to3, "_t=", "&"
    )
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

    logger.info("POST data keys", keys=list(post_data.keys()))
    logger.info("cvf_form_action", cvf_form_action=cvf_form_action)

    response = await session.post(
        "https://www.amazon.com"
        + (cvf_form_action if cvf_form_action.startswith("/") else "/ap/cvf/verify"),
        data=post_data,
        allow_redirects=True,
    )
    save("step9_cvf_verify.html", response.text)
    logger.info("Status", status=response.status_code, url=response.url)


    # if MOBILE_PHONE_REGISTRATION_CONFLICT_WARNED_VERIFY

    if (response.status_code == 200 and "mobileclaimconflict" in response.url.lower() or "mobileclaimconflict" in response.text.lower()):
        logger.error("Mobile claim conflict", number_used="True")
        # get form params from id ap_account_conflict_warning_customer_actions
        html_obj = BeautifulSoup(response.text, "lxml")
        form_action, form_inputs = extract_form_data(html_obj, response.url, "ap_account_conflict_warning_customer_actions")
        
        
        logger.info("data_claim", params=form_inputs)
        logger.info("data_claim", data=form_action)
        
        response = await session.post(
            form_action,
            data=form_inputs,
        )

        save("mobile_claim_conflict.html", response.text)
        logger.info("Status", status=response.status_code, url=response.url)

    # =============================================================================
    # PASO 10 -- Detectar resultado
    # =============================================================================
    logger.info("[10] Analyzing response...")

    h9 = BeautifulSoup(response.text, 'lxml')
    url9 = response.url

    is_otp, otp_fields = detect_otp_page(h9, url9)

    # -- Caso 1: OTP --------------------------------------------------------------
    if is_otp:
        
        logger.info("[SMS] OTP DETECTED")
        logger.info("URL", url=url9)
        logger.info("OTP fields", otp_fields=otp_fields)

        form_action, form_inputs = extract_form_data(h9, url9, form_id="auth-pv-form")
        if not form_action:
            form_action, form_inputs = extract_form_data(h9, url9)
        logger.info("Form action", form_action=form_action)
        logger.info("Form inputs", form_inputs=list(form_inputs.keys()))


        otp_code = await check_hero_sms_code(int(activation_id))

        if otp_code is None:
            logger.error("Orden de SMS expiró")
            return None

        if otp_code and otp_fields and form_action:
            otp_data = form_inputs.copy()
            for field in otp_fields:
                otp_data[field] = otp_code
            new_csrf = bs_val(h9, "anti-csrftoken-a2z")
            if new_csrf:
                otp_data["anti-csrftoken-a2z"] = new_csrf

            otp_data["metadata1"] = ""

            logger.info("OTP data", otp_data=otp_data)
            logger.info("POST OTP", url=form_action, otp_code=otp_code)
            response = await session.post(
                form_action,
                data=otp_data,
                allow_redirects=True,
            )

            save("step10_otp_submit.html", response.text)
            logger.info("OTP status", status=response.status_code, url=response.url)
            print(response.headers)

            # ir a amazon.com
            response = await session.get(
                "https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post",
                allow_redirects=True,
            )

            save("step12_amazon.html", response.text)

            addresss = BeautifulSoup(response.text, 'lxml')
            csrf_token = bs_val(addresss, "csrfToken") or new_csrf
            address_ui_widgets_previous_address_form_state_token = bs_val(
                addresss, "address-ui-widgets-previous-address-form-state-token"
            )
            address_ui_widgets_obfuscated_customerId = bs_val(
                addresss, "address-ui-widgets-obfuscated-customerId"
            )
            address_ui_widgets_csrfToken = bs_val(
                addresss, "address-ui-widgets-csrfToken"
            )
            address_ui_widgets_form_load_start_time = bs_val(
                addresss, "address-ui-widgets-form-load-start-time"
            )
            address_ui_widgets_clickstream_related_request_id = bs_val(
                addresss, "address-ui-widgets-clickstream-related-request-id"
            )
            address_ui_widgets_address_wizard_interaction_id = bs_val(
                addresss, "address-ui-widgets-address-wizard-interaction-id"
            )

            # go to amazon.com
            logger.info("Amazon status", status=response.status_code, url=response.url)

            params = {
                "ref": "ya_address_book_add_post",
            }

            data = {
                "csrfToken": csrf_token,
                "addressID": "",
                "address-ui-widgets-countryCode": "US",
                "address-ui-widgets-enterAddressFullName": f"{first_name} {last_name}",
                "address-ui-widgets-enterAddressPhoneNumber": "+1" + number_whitouth_code,
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

            response = await session.post(
                "https://www.amazon.com/a/addresses/add?ref=ya_address_book_add_post",
                data=data,
                params=params,
            )

            logger.info("ADDRESS response", url=response.url, status=response.status_code)

            save("step12_amazon.html", response.text)

            cookie_str, cookies = extract_cookies_from_response(session, response)
            logger.info(
                "Cookies extraídas",
                count=len(cookies),
                cookies=cookie_str,
            )

    else:
        logger.info("No OTP detected")
        save("step12_amazon.html", response.text)
if __name__ == "__main__":
    start_time = time.time()
    asyncio.run(create())
    end_time = time.time()
    logger.info("Total time", time=end_time - start_time)