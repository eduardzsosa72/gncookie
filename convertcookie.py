"""
Amazon Cookie Domain Converter
================================
Convierte cookies de amazon.com a cualquier otro dominio Amazon
haciendo login con las cookies existentes en el dominio destino.

Uso:
    python3 cookie_converter.py
    python3 cookie_converter.py --from "session-id=...; x-main=..." --to .co.uk
    python3 cookie_converter.py --file cookies.txt --to .com.mx --out converted.txt

Formatos soportados de entrada:
    - String directo:  "session-id=abc; x-main=\"xyz\""
    - Archivo .txt:    Formato Phone:.../Password:.../Name:.../Cookies:...
    - Archivo .txt:    Líneas sueltas con cookies

Dominios disponibles:
    .com .co.uk .de .fr .es .it .co.jp .ca .com.mx .com.br .com.au
    .nl .pl .se .sg .ae .sa .in .eg .tr .be .com.tr
"""

import re
import sys
import time
import json
import argparse
import urllib.parse
from typing import Optional

# Intentar curl_cffi primero, fallback a requests
try:
    import curl_cffi
    _USE_CURL = True
except ImportError:
    import requests
    _USE_CURL = False

# ── CONFIG ─────────────────────────────────────────────────────────────────────
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

AMAZON_DOMAINS = {
    ".com":      "www.amazon.com",
    ".co.uk":    "www.amazon.co.uk",
    ".de":       "www.amazon.de",
    ".fr":       "www.amazon.fr",
    ".es":       "www.amazon.es",
    ".it":       "www.amazon.it",
    ".co.jp":    "www.amazon.co.jp",
    ".ca":       "www.amazon.ca",
    ".com.mx":   "www.amazon.com.mx",
    ".com.br":   "www.amazon.com.br",
    ".com.au":   "www.amazon.com.au",
    ".nl":       "www.amazon.nl",
    ".pl":       "www.amazon.pl",
    ".se":       "www.amazon.se",
    ".sg":       "www.amazon.sg",
    ".ae":       "www.amazon.ae",
    ".sa":       "www.amazon.sa",
    ".in":       "www.amazon.in",
    ".eg":       "www.amazon.eg",
    ".tr":       "www.amazon.com.tr",
    ".be":       "www.amazon.com.be",
}

# Cookies que se transfieren tal cual (no son de sesión autenticada)
TRANSFERABLE_COOKIES = {
    "i18n-prefs", "lc-main", "sp-cdn", "ubid-main",
    "x-main", "at-main", "sess-at-main",
}

# Cookies de sesión que Amazon genera nuevas en cada dominio
SESSION_COOKIES = {
    "session-id", "session-id-time", "session-token",
    "csm-hit", "aws-ubid-main",
}


# ── PARSERS ────────────────────────────────────────────────────────────────────
def parse_cookie_string(cookie_str: str) -> dict:
    """Parsear string de cookies a dict. Maneja comillas en valores."""
    result = {}
    # Limpiar espacios extra
    cookie_str = cookie_str.strip()
    if not cookie_str:
        return result

    # Split por ";" respetando comillas
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        v = v.strip()
        if k:
            result[k] = v
    return result


def parse_file(filepath: str) -> list[dict]:
    """
    Parsear archivo cookies.txt.
    Soporta dos formatos:
      1. Phone:xxx/Password:xxx/Name:xxx/Cookies:xxx
      2. Líneas sueltas con cookies
    """
    entries = []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Formato 1: Phone:.../Cookies:...
    pattern = re.compile(
        r"Phone:([^\n/]+)/Password:([^\n/]+)/Name:([^\n/]+)/Cookies:([^\n]+)",
        re.IGNORECASE,
    )
    matches = pattern.findall(content)
    if matches:
        for phone, password, name, cookies_raw in matches:
            entries.append({
                "phone":    phone.strip(),
                "password": password.strip(),
                "name":     name.strip(),
                "cookies":  parse_cookie_string(cookies_raw.strip()),
                "raw":      cookies_raw.strip(),
            })
        return entries

    # Formato 2: líneas con cookies (formato key=val; key=val)
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("="):
            continue
        if "session-id=" in line or "x-main=" in line or "session-token=" in line:
            entries.append({
                "phone":    "",
                "password": "",
                "name":     "",
                "cookies":  parse_cookie_string(line),
                "raw":      line,
            })

    return entries


# ── SESIÓN ────────────────────────────────────────────────────────────────────
def make_session(proxy: Optional[str] = None):
    if _USE_CURL:
        s = curl_cffi.Session(impersonate="chrome")
        s.trust_env = False
    else:
        s = requests.Session()
        s.headers.update({"User-Agent": UA})

    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def session_get(session, url: str, headers: dict = None, allow_redirects: bool = True):
    kw = {"timeout": 20, "allow_redirects": allow_redirects}
    if headers:
        kw["headers"] = headers
    return session.request("GET", url, **kw)


# ── CONVERSIÓN ────────────────────────────────────────────────────────────────
def inject_cookies(session, cookies: dict, domain: str):
    """Inyectar cookies en la sesión para el dominio dado."""
    for name, value in cookies.items():
        # Limpiar comillas del valor para curl_cffi
        clean_val = value.strip('"')
        try:
            session.cookies.set(name, clean_val, domain=domain)
        except Exception:
            try:
                session.cookies.set(name, clean_val)
            except Exception:
                pass


def build_cookie_header(cookies: dict) -> str:
    """Construir header Cookie manualmente."""
    parts = []
    for k, v in cookies.items():
        parts.append(f"{k}={v}")
    return "; ".join(parts)


def extract_response_cookies(session, response) -> dict:
    """Extraer todas las cookies del response + jar de la sesión."""
    jar = {}
    try:
        jar = session.cookies.get_dict(domain=None, path=None) or {}
    except Exception:
        try:
            jar = dict(session.cookies)
        except Exception:
            pass

    resp_cookies = {}
    try:
        resp_cookies = dict(response.cookies)
    except Exception:
        pass

    # Parsear Set-Cookie manualmente
    try:
        raw_sc = response.headers.get("set-cookie", "")
        if raw_sc:
            for sc in (raw_sc if isinstance(raw_sc, list) else [raw_sc]):
                pair = sc.split(";")[0].strip()
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    resp_cookies[k.strip()] = v.strip()
    except Exception:
        pass

    merged = {**jar, **resp_cookies}
    return {k: v for k, v in merged.items() if k and v}


def format_cookies(cookies: dict) -> str:
    """Formatear cookies para guardar. session-token y x-main llevan comillas."""
    processed = {}
    for k, v in cookies.items():
        if k in ("session-token", "x-main", "sess-at-main"):
            inner = v.strip('"')
            processed[k] = f'"{inner}"'
        else:
            processed[k] = v
    # Sin espacios
    return ";".join(f"{k}={v}" for k, v in processed.items())


def convert_cookies(
    source_cookies: dict,
    target_tld: str,
    proxy:         Optional[str] = None,
    verbose:       bool = True,
) -> Optional[dict]:
    """
    Convertir cookies de amazon.com al dominio target_tld.

    Estrategia:
    1. Crear sesión nueva
    2. Inyectar las cookies transferibles (ubid-main, x-main, at-main, etc.)
    3. GET al home del dominio destino con las cookies en el header
    4. Extraer las nuevas cookies de sesión (session-id, session-token, etc.)
    5. Merge: cookies originales transferibles + nuevas cookies del dominio

    Returns: dict de cookies para el dominio destino, o None si falla.
    """
    if target_tld not in AMAZON_DOMAINS:
        print(f"  ❌ Dominio desconocido: {target_tld}")
        print(f"  Disponibles: {list(AMAZON_DOMAINS.keys())}")
        return None

    host    = AMAZON_DOMAINS[target_tld]
    base_url = f"https://{host}"

    if verbose:
        print(f"  → Convirtiendo a {host}...")

    session = make_session(proxy)

    # Inyectar cookies transferibles en el jar
    transferable = {k: v for k, v in source_cookies.items()
                    if k in TRANSFERABLE_COOKIES}
    if verbose:
        print(f"  → Cookies transferibles: {list(transferable.keys())}")

    inject_cookies(session, transferable, host)
    inject_cookies(session, transferable, f".{host.split('.', 1)[1]}")  # dominio wildcard

    # Construir header Cookie con TODAS las cookies originales
    # (Amazon necesita ver x-main, ubid-main, etc. para reconocer la sesión)
    cookie_header = build_cookie_header({k: v.strip('"') for k, v in source_cookies.items()})

    headers = {
        "User-Agent":            UA,
        "Accept":                "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":       "en-US,en;q=0.9",
        "Accept-Encoding":       "gzip, deflate, br",
        "Cookie":                cookie_header,
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":        "document",
        "Sec-Fetch-Mode":        "navigate",
        "Sec-Fetch-Site":        "none",
        "Sec-Fetch-User":        "?1",
        "Cache-Control":         "no-cache",
        "Pragma":                "no-cache",
    }

    try:
        # GET home del dominio destino
        r = session_get(session, base_url, headers=headers)
        if verbose:
            print(f"  → {host}/ → {r.status_code} ({r.url})")
            # Verificar si está logueado
            if "Hello," in r.text or "Account & Lists" in r.text:
                name_match = re.search(r"Hello,\s+([^<\n]+)", r.text)
                if name_match:
                    print(f"  ✅ Sesión activa como: {name_match.group(1).strip()}")
            else:
                print(f"  ⚠️  No se detectó sesión activa en la respuesta")

        # Extraer cookies del response
        new_cookies = extract_response_cookies(session, r)

        # Merge: transferables originales + nuevas del dominio
        result = {}
        # Primero las transferables (valores originales)
        for k, v in source_cookies.items():
            if k in TRANSFERABLE_COOKIES:
                result[k] = v.strip('"')
        # Luego las nuevas (sesión del dominio destino)
        for k, v in new_cookies.items():
            result[k] = v.strip('"')

        if verbose:
            print(f"  → Cookies obtenidas: {list(result.keys())}")

        # Verificar que tenemos session-id y session-token
        if "session-id" not in result or "session-token" not in result:
            print(f"  ⚠️  Faltan cookies de sesión: session-id={bool(result.get('session-id'))}, session-token={bool(result.get('session-token'))}")

        time.sleep(0.5)  # Pequeña pausa entre requests
        return result

    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None


def convert_to_multiple(
    source_cookies: dict,
    target_tlds:    list[str],
    proxy:          Optional[str] = None,
    verbose:        bool = True,
) -> dict[str, dict]:
    """Convertir a múltiples dominios."""
    results = {}
    for tld in target_tlds:
        print(f"\n[{tld}]")
        converted = convert_cookies(source_cookies, tld, proxy, verbose)
        if converted:
            results[tld] = converted
    return results


# ── CLI ────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Amazon Cookie Domain Converter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--from",   dest="source",  help="String de cookies fuente (amazon.com)")
    parser.add_argument("--file",   dest="file",    help="Archivo .txt con cookies")
    parser.add_argument("--to",     dest="target",  help="TLD destino (.co.uk, .de, all, ...)", default=".co.uk")
    parser.add_argument("--proxy",  dest="proxy",   help="Proxy URL http://user:pass@host:port")
    parser.add_argument("--out",    dest="out",     help="Archivo de salida (default: stdout)")
    parser.add_argument("--list",   action="store_true", help="Listar dominios disponibles")
    args = parser.parse_args()

    if args.list:
        print("Dominios disponibles:")
        for tld, host in AMAZON_DOMAINS.items():
            print(f"  {tld:12} → {host}")
        return

    # Determinar dominios destino
    if args.target == "all":
        target_tlds = list(AMAZON_DOMAINS.keys())
        target_tlds.remove(".com")  # No convertir a sí mismo
    else:
        target_tlds = [t.strip() for t in args.target.split(",")]

    output_lines = []

    # Parsear fuente
    if args.source:
        entries = [{
            "phone": "", "password": "", "name": "",
            "cookies": parse_cookie_string(args.source),
            "raw": args.source,
        }]
    elif args.file:
        entries = parse_file(args.file)
        print(f"Cargadas {len(entries)} entradas de {args.file}")
    else:
        # Leer de stdin
        print("Pega las cookies (Enter + Ctrl+D para terminar):")
        raw = sys.stdin.read().strip()
        entries = [{
            "phone": "", "password": "", "name": "",
            "cookies": parse_cookie_string(raw),
            "raw": raw,
        }]

    for i, entry in enumerate(entries, 1):
        print(f"\n{'='*60}")
        print(f"Entrada {i}/{len(entries)}")
        if entry["phone"]:
            print(f"  Phone:    {entry['phone']}")
            print(f"  Password: {entry['password']}")
            print(f"  Name:     {entry['name']}")
        print(f"  Cookies originales: {list(entry['cookies'].keys())}")

        source_cookies = entry["cookies"]

        if not source_cookies:
            print("  ❌ Sin cookies para convertir")
            continue

        for tld in target_tlds:
            converted = convert_cookies(source_cookies, tld, args.proxy, verbose=True)
            if not converted:
                continue

            cookie_str = format_cookies(converted)
            host       = AMAZON_DOMAINS[tld]

            if entry["phone"]:
                line = f"Phone:{entry['phone']}/Password:{entry['password']}/Name:{entry['name']}/Domain:{host}/Cookies:{cookie_str}"
            else:
                line = f"Domain:{host}/Cookies:{cookie_str}"

            output_lines.append(line)
            print(f"\n  [{tld}] Cookie string:")
            print(f"  {cookie_str[:100]}...")

    print(f"\n{'='*60}")
    print(f"Total convertidas: {len(output_lines)}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n\n".join(output_lines))
        print(f"Guardado en: {args.out}")
    else:
        print("\n--- RESULTADO ---")
        for line in output_lines:
            print(line)


# ── USO COMO MÓDULO ────────────────────────────────────────────────────────────
def quick_convert(cookie_str: str, target_tld: str, proxy: str = None) -> Optional[str]:
    """
    Uso rápido como módulo:
        from cookie_converter import quick_convert
        result = quick_convert("session-id=...; x-main=...", ".co.uk")
    """
    cookies   = parse_cookie_string(cookie_str)
    converted = convert_cookies(cookies, target_tld, proxy, verbose=False)
    if not converted:
        return None
    return format_cookies(converted)


if __name__ == "__main__":
    main()

    