

import json
import base64
import time
import random
import re
import struct
import hashlib
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# =============================================================================
# SPOOFING HELPERS
# =============================================================================

def _parse_ua_info(ua: str) -> Dict[str, Any]:
    """Parse user agent to extract browser, OS, device info."""
    ua_lower = ua.lower()
    info = {
        "browser": "unknown",
        "browser_version": 0,
        "os": "unknown",
        "os_version": "",
        "device": "desktop",
        "is_mobile": False,
        "is_chrome": False,
        "is_firefox": False,
        "is_safari": False,
        "is_edge": False,
    }
    
    if "firefox" in ua_lower:
        info["browser"] = "Firefox"
        info["is_firefox"] = True
        try:
            info["browser_version"] = int(ua.split("Firefox/")[1].split(".")[0])
        except:
            pass
    elif "edg" in ua_lower:
        info["browser"] = "Edge"
        info["is_edge"] = True
        try:
            info["browser_version"] = int(ua.split("Edge/")[1].split(".")[0])
        except:
            pass
    elif "chrome" in ua_lower and "chromium" not in ua_lower:
        info["browser"] = "Chrome"
        info["is_chrome"] = True
        try:
            info["browser_version"] = int(ua.split("Chrome/")[1].split(".")[0])
        except:
            pass
    elif "safari" in ua_lower:
        info["browser"] = "Safari"
        info["is_safari"] = True
        try:
            info["browser_version"] = int(ua.split("Version/")[1].split(".")[0])
        except:
            pass
    
    if "android" in ua_lower:
        info["os"] = "Android"
        info["device"] = "mobile"
        info["is_mobile"] = True
    elif "iphone" in ua_lower or "ipad" in ua_lower or "ipod" in ua_lower:
        info["os"] = "iOS"
        info["device"] = "mobile"
        info["is_mobile"] = True
    elif "windows phone" in ua_lower:
        info["os"] = "Windows Phone"
        info["device"] = "mobile"
        info["is_mobile"] = True
    elif "windows nt" in ua_lower:
        info["os"] = "Windows"
        try:
            info["os_version"] = ua.split("Windows NT ")[1].split(";")[0]
        except:
            pass
    elif "mac os x" in ua_lower:
        info["os"] = "macOS"
        try:
            info["os_version"] = ua.split("Mac OS X ")[1].split(";")[0].replace("_", ".")
        except:
            pass
    elif "linux" in ua_lower:
        info["os"] = "Linux"
        if "ubuntu" in ua_lower:
            info["linux_distro"] = "Ubuntu"
        elif "fedora" in ua_lower:
            info["linux_distro"] = "Fedora"
        elif "debian" in ua_lower:
            info["linux_distro"] = "Debian"
        elif "arch" in ua_lower:
            info["linux_distro"] = "Arch"
        elif "gentoo" in ua_lower:
            info["linux_distro"] = "Gentoo"
        elif "opensuse" in ua_lower or "suse" in ua_lower:
            info["linux_distro"] = "openSUSE"
        else:
            info["linux_distro"] = random.choice(["Ubuntu", "Fedora", "Debian"])
    
    return info


def _generate_canvas_advanced(ua: str, email: str = "") -> Dict[str, Any]:
    """Generate canvas fingerprint by simulating real browser rendering with PIL."""
    ua_info = _parse_ua_info(ua)
    
    img = Image.new('RGBA', (200, 50), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except:
        font = ImageFont.load_default()
    
    colors = [
        (0, 0, 0, 255),
        (50, 100, 150, 255),
        (100, 50, 100, 255),
        (0, 100, 0, 255),
    ]
    
    for i, color in enumerate(colors):
        draw.text((10 + i * 20, 5), f"T{i}", font=font, fill=color)
    
    draw.rectangle([5, 35, 80, 45], outline=(0, 0, 0, 255), width=1)
    draw.ellipse([90, 35, 120, 45], fill=(200, 100, 100, 255))
    
    base_str = f"{ua_info['browser']}|{ua_info['os']}|{email}"
    hash_input = hashlib.md5(base_str.encode()).hexdigest()
    draw.text((130, 35), hash_input[:4], font=font, fill=(0, 0, 0, 255))
    
    pixels = list(img.getdata())
    red_channel = [p[0] for p in pixels]
    
    canvas_hash = int(hashlib.md5(bytes(red_channel[:1000])).hexdigest()[:8], 16)
    email_hash = int(hashlib.md5(email.encode()).hexdigest()[:8], 16) if email else canvas_hash
    
    histogram = [0] * 256
    for p in pixels:
        r = min(255, p[0])
        g = min(255, p[1])
        b = min(255, p[2])
        histogram[r] += 1
        histogram[g] += 1
        histogram[b] += 1
    
    bins = []
    target_bins = 200
    bin_size = max(1, len(histogram) // target_bins)
    for i in range(0, len(histogram), bin_size):
        bins.append(sum(histogram[i:i + bin_size]))
    while len(bins) < target_bins:
        bins.append(0)
    bins = bins[:target_bins]
    
    bins[0] = len(pixels) // 10
    bins[-1] = len(pixels) // 10
    
    return {
        "hash": canvas_hash if canvas_hash else random.randint(-2147483648, 2147483647),
        "emailHash": email_hash if email_hash else random.randint(-2147483648, 2147483647),
        "histogramBins": bins
    }


def _generate_mouse_human(
    num_clicks: int = 3,
    form_width: int = 312,
    form_height: int = 32,
    is_mobile: bool = False
) -> Dict[str, Any]:
    """Generate human-like mouse events."""
    clicks = []
    cycles = []
    positions = []
    
    base_y = random.randint(100, 400)
    
    for i in range(num_clicks):
        field_offset = i * (form_height + 10)
        x_range = form_width if not is_mobile else form_width - 20
        
        click_x = random.randint(10, x_range)
        click_y = base_y + field_offset + random.randint(5, form_height - 5)
        
        positions.append(f"{click_x},{click_y}")
        
        delay = random.randint(50, 200)
        cycles.append(delay)
        
        if i > 0:
            prev_x = int(positions[i-1].split(",")[0])
            prev_y = int(positions[i-1].split(",")[1])
            move_time = random.randint(30, 150)
            cycles.append(move_time)
    
    return {
        "clicks": num_clicks,
        "mouseClickPositions": positions,
        "mouseCycles": cycles
    }


def _get_gpu_for_ua(ua: str) -> Dict[str, Any]:
    """Get GPU info spoofed based on browser/OS with random models (matching real fingerprints)."""
    ua_info = _parse_ua_info(ua)
    
    gpu = {"vendor": "", "model": "", "renderer": ""}
    
    if ua_info["is_chrome"] or ua_info["is_edge"]:
        if ua_info["os"] == "Windows":
            models = ["GTX 750", "GTX 950", "GTX 1050", "GTX 1060", "GTX 1070", "GTX 1080", "RTX 2060", "RTX 3060"]
            model = random.choice(models)
            gpu["vendor"] = "NVIDIA"
            gpu["model"] = f"NVIDIA GeForce {model} Direct3D11 vs_5_0 ps_5_0"
            gpu["renderer"] = f"ANGLE (NVIDIA, NVIDIA GeForce {model} Direct3D11 vs_5_0 ps_5_0), or similar"
        elif ua_info["os"] == "Linux":
            models = ["GTX 750", "GTX 950", "GTX 960", "GTX 980", "GTX 1050", "GTX 1060"]
            model = random.choice(models)
            gpu["vendor"] = "NVIDIA"
            gpu["model"] = f"NVIDIA GeForce {model}"
            gpu["renderer"] = f"NVIDIA GeForce {model}/OpenGL"
        else:
            gpu["vendor"] = "Google Inc. (NVIDIA)"
            gpu["model"] = "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar"
            gpu["renderer"] = "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar"
    elif ua_info["is_firefox"]:
        if ua_info["os"] == "Windows":
            models = ["HD Graphics 400", "HD Graphics 440", "HD Graphics 520", "HD Graphics 615", "HD Graphics 620", "Iris Xe"]
            model = random.choice(models)
            gpu["vendor"] = "Intel"
            gpu["model"] = f"Intel(R) {model}"
        elif ua_info["os"] == "Linux":
            amd_cards = ["RX 6500 XT", "RX 6600 XT", "RX 6700 XT", "RX 6800", "RX 6900 XT"]
            intel_cards = ["Iris(R) Xe Graphics", "HD Graphics 400", "HD Graphics 440", "HD Graphics 520"]
            if random.random() < 0.6:
                model = random.choice(amd_cards)
                gpu["vendor"] = "ATI Technologies Inc."
                gpu["model"] = f"Mesa AMD Radeon {model} (OpenGL 4.6)"
            else:
                model = random.choice(intel_cards)
                gpu["vendor"] = "Intel Inc."
                gpu["model"] = f"Mesa Intel(R) {model} (OpenGL 4.6)"
        elif ua_info["os"] == "macOS":
            gpu["vendor"] = "Intel"
            gpu["model"] = "Intel(R) HD Graphics 4000"
        else:
            gpu["vendor"] = "Intel"
            gpu["model"] = "Intel(R) HD Graphics"
        gpu["renderer"] = f"{gpu['model']}, or similar"
    elif ua_info["is_safari"] or ua_info["os"] == "macOS":
        silicon_models = ["M1", "M1 Pro", "M1 Max", "M1 Ultra", "M2", "M2 Pro", "M2 Max", "M2 Ultra", "M3", "M3 Pro", "M3 Max"]
        model = random.choice(silicon_models)
        gpu["vendor"] = "Apple Inc."
        gpu["model"] = f"Apple {model}"
        gpu["renderer"] = f"ANGLE (Apple, Apple {model} OpenGL Engine), or similar"
    elif ua_info["is_mobile"]:
        if ua_info["os"] == "Android":
            gpu["vendor"] = "Qualcomm"
            models = ["Adreno 304", "Adreno 305", "Adreno 306", "Adreno 308", "Adreno 405", "Adreno 506", "Adreno 508", "Adreno 509", "Adreno 610", "Adreno 618", "Adreno 650"]
            gpu["model"] = random.choice(models)
        else:
            gpu["vendor"] = "Apple Inc."
            models = ["A13 GPU", "A14 GPU", "A15 GPU", "A16 GPU", "A17 Pro GPU"]
            gpu["model"] = random.choice(models)
        gpu["renderer"] = f"{gpu['model']}, or similar"
    else:
        gpu["vendor"] = "Intel"
        gpu["model"] = "Intel(R) HD Graphics"
        gpu["renderer"] = "Intel(R) HD Graphics, or similar"
    
    return gpu


def _get_gpu_extensions(ua: str, gpu_info: Dict[str, Any]) -> List[str]:
    """Get standard GPU extensions list."""
    return [
        "ANGLE_instanced_arrays",
        "EXT_blend_minmax",
        "EXT_color_buffer_half_float",
        "EXT_float_blend",
        "EXT_frag_depth",
        "EXT_shader_texture_lod",
        "EXT_sRGB",
        "EXT_texture_compression_bptc",
        "EXT_texture_compression_rgtc",
        "EXT_texture_filter_anisotropic",
        "OES_element_index_uint",
        "OES_fbo_render_mipmap",
        "OES_standard_derivatives",
        "OES_texture_float",
        "OES_texture_float_linear",
        "OES_texture_half_float",
        "OES_texture_half_float_linear",
        "OES_vertex_array_object",
        "WEBGL_color_buffer_float",
        "WEBGL_compressed_texture_astc",
        "WEBGL_compressed_texture_etc",
        "WEBGL_compressed_texture_s3tc",
        "WEBGL_compressed_texture_s3tc_srgb",
        "WEBGL_debug_renderer_info",
        "WEBGL_debug_shaders",
        "WEBGL_depth_texture",
        "WEBGL_draw_buffers",
        "WEBGL_lose_context",
    ]


def _get_capabilities_for_ua(ua: str) -> Dict[str, Any]:
    """Get browser capabilities based on user agent."""
    ua_info = _parse_ua_info(ua)
    
    return {
        "css": {
            "textShadow": 1,
            "WebkitTextStroke": 1,
            "boxShadow": 1,
            "borderRadius": 1,
            "borderImage": 1,
            "opacity": 1,
            "transform": 1,
            "transition": 1
        },
        "js": {
            "audio": True,
            "geolocation": True,
            "localStorage": "supported",
            "touch": ua_info["is_mobile"],
            "video": True,
            "webWorker": True
        },
        "elapsed": 0
    }


def _get_plugins_for_ua(ua: str, screen_width: int = 1280, screen_height: int = 1024) -> str:
    """Get plugins string based on browser and screen."""
    ua_info = _parse_ua_info(ua)
    
    if ua_info["browser"] == "Firefox" or ua_info["is_firefox"]:
        plugins = "PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer WebKit built-in PDF"
    elif ua_info["browser"] == "Chrome" or ua_info["is_chrome"]:
        plugins = "Chrome PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer PDF Viewer"
    elif ua_info["browser"] == "Safari":
        plugins = "PDF Viewer Chrome PDF Viewer Chromium PDF Viewer WebKit built-in PDF"
    else:
        plugins = "PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer WebKit built-in PDF"
    
    plugins += f" ||{screen_width}-{screen_height}-{screen_height}-24-*-*-*"
    return plugins


# =============================================================================
# XXTEA ENCRYPTION (for FWCIM)
# =============================================================================

XXTEA_DELTA = 0x9E3779B9

# Hardcoded FWCIM key (extracted from Amazon's JS)
FWCIM_KEY = [1888420705, 2576816180, 2347232058, 874813317]


def _to_uint32(x: int) -> int:
    return x & 0xFFFFFFFF


def _to_int32(x: int) -> int:
    x = x & 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


def _xxtea_mx(z: int, y: int, sum_val: int, key: List[int], p: int, e: int) -> int:
    return _to_uint32(
        (((z >> 5) ^ (y << 2)) + ((y >> 3) ^ (z << 4))) ^
        ((sum_val ^ y) + (key[(p & 3) ^ e] ^ z))
    )


def xxtea_encrypt(data: bytes, key: List[int]) -> bytes:
    """XXTEA encrypt data with 128-bit key (4 x uint32)."""
    # Pad to multiple of 4 bytes
    padding = (4 - len(data) % 4) % 4
    if padding:
        data = data + b'\x00' * padding
    
    n = len(data) // 4
    v = list(struct.unpack(f'<{n}I', data))
    
    if n < 2:
        v.append(0)
        n = 2
    
    rounds = 6 + 52 // n
    sum_val = 0
    z = v[n - 1]
    
    for _ in range(rounds):
        sum_val = _to_uint32(sum_val + XXTEA_DELTA)
        e = (sum_val >> 2) & 3
        
        for p in range(n - 1):
            y = v[p + 1]
            v[p] = _to_uint32(v[p] + _xxtea_mx(z, y, sum_val, key, p, e))
            z = v[p]
        
        y = v[0]
        v[n - 1] = _to_uint32(v[n - 1] + _xxtea_mx(z, y, sum_val, key, n - 1, e))
        z = v[n - 1]
    
    return struct.pack(f'<{n}I', *v)


# =============================================================================
# CRC32 (for FWCIM)
# =============================================================================

def _build_crc32_table() -> List[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
        table.append(_to_int32(crc))
    return table


CRC32_TABLE = _build_crc32_table()


def crc32(data: str) -> int:
    """Calculate CRC32 matching FWCIM's implementation."""
    crc = 0xFFFFFFFF
    for char in data:
        crc = _to_uint32((crc >> 8) ^ CRC32_TABLE[(crc ^ ord(char)) & 0xFF])
    return _to_int32(crc ^ 0xFFFFFFFF)


def int_to_hex(value: int) -> str:
    """Convert signed int32 to 8-char uppercase hex."""
    if value < 0:
        value = value + 0x100000000
    return format(value, '08X')


# =============================================================================
# FWCIM FINGERPRINT GENERATOR
# =============================================================================

def generate_fingerprint(
    url: str = "https://www.amazon.com/",
    user_agent: str = "Zappos Android",
    screen_width: int = 1080,
    screen_height: int = 1920,
    timezone_offset: int = -8,
    dynamic_urls: Optional[List[str]] = None,
    dynamic_urls_count: int = 0
) -> Dict[str, Any]:
    """Generate device fingerprint data structure."""
    now = int(time.time() * 1000)
    start_time = now - random.randint(1000, 5000)
    
    return {
        "metrics":{
            "el":0,
            "script":0,
            "h":0,
            "batt":0,
            "perf":0,
            "auto":0,
            "tz":0,
            "fp2":0,
            "lsubid":0,
            "browser":0,
            "capabilities":0,
            "gpu":0,
            "dnt":0,
            "math":0,
            "tts":0,
            "input":0,
            "canvas":0,
            "captchainput":0,
            "pow":0
        },
        "start":start_time,
        "interaction":{
            "clicks":random.randint(0, 3),
            "touches":random.randint(0, 5),
            "keyPresses":random.randint(5, 20),
            "cuts":0,
            "copies":0,
            "pastes":0,
            "keyPressTimeIntervals":[],
            "mouseClickPositions":[],
            "keyCycles":[],
            "mouseCycles":[],
            "touchCycles":[]
        },
        "scripts":{
            "dynamicUrls": dynamic_urls,
            "inlineHashes":[random.randint(-2000000000, 2000000000) for _ in range(10)],
            "elapsed":random.randint(1, 10),
            "dynamicUrlCount": dynamic_urls_count,
            "inlineHashesCount":10
        },
        "history":{
            "length":random.randint(1, 20)
        },
        "performance":{
            "timing":{
                "connectStart": start_time - 1000,
                "secureConnectionStart": 0,
                "domComplete": start_time - 610,
                "navigationStart": start_time - 1005,
                "loadEventEnd": start_time - 600,
                "responseEnd": start_time - 700,
                "fetchStart": start_time - 1000,
            }
        },
        "automation":{
            "wd":{
                "properties":{
                    "document":[
                    
                    ],
                    "window":[
                    
                    ],
                    "navigator":[
                    
                    ]
                }
            },
            "phantom":{
                "properties":{
                    "window":[
                    
                    ]
                }
            }
        },
        "end": now,
        "timeZone": timezone_offset,
        "flashVersion":"None",
        "plugins":"PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer WebKit built-in PDF ||1920-1080-1080-24-*-*-*",
        "dupedPlugins":"PDF Viewer Chrome PDF Viewer Chromium PDF Viewer Microsoft Edge PDF Viewer WebKit built-in PDF ||1920-1080-1080-24-*-*-*",
        "screenInfo":f"{screen_width}-{screen_height}-{screen_height}-0-*-*-*",
        "lsUbid":f"X{random.randint(10,99)}-{random.randint(1000000,9999999)}-{random.randint(1000000,9999999)}:{now}",
        "referrer":url,
        "userAgent": user_agent,
        "location":url,
        "webDriver":False,
        "capabilities":{
            "css":{
                "textShadow":1,
                "WebkitTextStroke":1,
                "boxShadow":1,
                "borderRadius":1,
                "borderImage":1,
                "opacity":1,
                "transform":1,
                "transition":1
            },
            "js":{
                "audio":True,
                "geolocation":True,
                "localStorage":"supported",
                "touch":False,
                "video":True,
                "webWorker":True
            },
            "elapsed":0
        },
        "gpu":{
            "vendor":"Google Inc. (NVIDIA)",
            "model":"ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar",
            "extensions":[
                "ANGLE_instanced_arrays",
                "EXT_blend_minmax",
                "EXT_color_buffer_half_float",
                "EXT_float_blend",
                "EXT_frag_depth",
                "EXT_shader_texture_lod",
                "EXT_sRGB",
                "EXT_texture_compression_bptc",
                "EXT_texture_compression_rgtc",
                "EXT_texture_filter_anisotropic",
                "OES_element_index_uint",
                "OES_fbo_render_mipmap",
                "OES_standard_derivatives",
                "OES_texture_float",
                "OES_texture_float_linear",
                "OES_texture_half_float",
                "OES_texture_half_float_linear",
                "OES_vertex_array_object",
                "WEBGL_color_buffer_float",
                "WEBGL_compressed_texture_s3tc",
                "WEBGL_compressed_texture_s3tc_srgb",
                "WEBGL_debug_renderer_info",
                "WEBGL_debug_shaders",
                "WEBGL_depth_texture",
                "WEBGL_draw_buffers",
                "WEBGL_lose_context",
                "WEBGL_provoking_vertex"
            ]
        },
        "dnt":"None",
        "math":{
            "tan":"-1.4214488238747245",
            "sin":"0.8178819121159085",
            "cos":"-0.5753861119575491"
        },
        "form":{
            "email":{
                "clicks":0,
                "touches":0,
                "keyPresses":0,
                "cuts":0,
                "copies":0,
                "pastes":0,
                "keyPressTimeIntervals":[
                    
                ],
                "mouseClickPositions":[
                    
                ],
                "keyCycles":[
                    
                ],
                "mouseCycles":[
                    
                ],
                "touchCycles":[
                    
                ],
                "width":312,
                "height":32,
                "totalFocusTime":1029,
                "prefilled":False
            },
            "ap_customer_name":{
                "clicks":4,
                "touches":0,
                "keyPresses":11,
                "cuts":0,
                "copies":0,
                "pastes":0,
                "keyPressTimeIntervals":[],
                "mouseClickPositions":[],
                "keyCycles":[],
                "mouseCycles":[],
                "touchCycles":[
                    
                ],
                "width":312,
                "height":32,
                "totalFocusTime":4043,
                "prefilled":False
            },
            "password":{
                "clicks":0,
                "touches":0,
                "keyPresses":12,
                "cuts":0,
                "copies":0,
                "pastes":0,
                "keyPressTimeIntervals":[],
                "mouseClickPositions":[
                    
                ],
                "keyCycles":[],
                "mouseCycles":[
                    
                ],
                "touchCycles":[
                    
                ],
                "width":312,
                "height":32,
                "totalFocusTime":3703,
                "prefilled":False
            },
            "ap_password_check":{
                "clicks":0,
                "touches":0,
                "keyPresses":11,
                "cuts":0,
                "copies":0,
                "pastes":0,
                "keyPressTimeIntervals":[],
                "mouseClickPositions":[
                    
                ],
                "keyCycles":[],
                "mouseCycles":[
                    
                ],
                "touchCycles":[
                    
                ],
                "width":312,
                "height":32,
                "totalFocusTime":0,
                "prefilled":False
            }
        },
        "canvas":{
            "hash":random.randint(-2147483648, 2147483647),
            "emailHash":random.randint(-2147483648, 2147483647),
            "histogramBins":[]
        },
        "token":{
            "isCompatible":True,
            "pageHasCaptcha":0
        },
        "auth":{
            "form":{
                "method":"post"
            }
        },
        "errors":[
            
        ]
        }


def generate_metadata1(
        url: str = "https://www.amazon.com/",
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36",
        screen_width: int = 1080,
        screen_height: int = 1920,
        timezone_offset: int = -8,
        dynamic_urls: Optional[List[str]] = None,
        dynamic_urls_count: int = 0,
        fingerprint: Optional[Dict[str, Any]] = None
    ) -> str:
    """
    Generate complete FWCIM metadata1 payload.
    
    Returns:
        String in format: "ECdITeCs:BASE64..."
    """
    if fingerprint is None:
        fingerprint = generate_fingerprint(
            url, user_agent, screen_width, screen_height, timezone_offset, dynamic_urls, dynamic_urls_count
        )
    
    # Serialize JSON (compact)
    json_str = json.dumps(fingerprint, separators=(',', ':'))
    
    # Calculate CRC32 prefix
    crc = crc32(json_str)
    hex_crc = int_to_hex(crc)
    
    # Combine: HEX#JSON
    combined = f"{hex_crc}#{json_str}"
    
    # XXTEA encrypt
    encrypted = xxtea_encrypt(combined.encode('utf-8'), FWCIM_KEY)
    
    # Base64 encode and add prefix
    b64 = base64.b64encode(encrypted).decode('ascii')
    
    return f"ECdITeCs:{b64}"


# =============================================================================
# SIEGE CSE PASSWORD ENCRYPTION
# =============================================================================

# Siege RSA-OAEP public key (from Amazon's JS)
# {"kty":"RSA","e":"AQAB","n":""}
SIEGE_PUBLIC_KEY = {
    "kty": "RSA",
    "n": "rwLCVK_8hcUgil9KQiN7RbtmcJV5Pt12CwbhZ1h9fvdbVRILCanjv2RNSW9l-Mq0fnRq6DLTLzX3J3TuVCZQ1wjfa-Ef1BDeXnVNaY4q0Vvl2e1e9UF-uwyK5mDyiftlPt5JcsRuFXU1dMSb5TwDiFV1UlGOc-db33zi1MlmrL5L7iyfqBQmlEoa5el5pFbmeK2wSOKBZtJja-dbVzde0jrpGlVhHDZOAlH7g8aTftqwHLVP27T9Pr0UJtaj9LIX-sg_K9-Pl7H2W9BJDTJLJi_EAAqBHTrRueejO3XbEuSGrsrphCk0ZlYqoLkobey-kubWTba5kzsWL-huF--tzQ",
    "e": "AQAB"
}


def _base64url_decode(data: str) -> bytes:
    """Decode base64url (no padding)."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += '=' * padding
    return base64.urlsafe_b64decode(data)


def _int_from_bytes(b: bytes) -> int:
    """Convert bytes to big-endian integer."""
    return int.from_bytes(b, 'big')


def _int_to_bytes(n: int, length: int) -> bytes:
    """Convert integer to big-endian bytes of specified length."""
    return n.to_bytes(length, 'big')


def _mgf1(seed: bytes, length: int, hash_func=hashlib.sha256) -> bytes:
    """MGF1 mask generation function."""
    result = b''
    counter = 0
    while len(result) < length:
        c = counter.to_bytes(4, 'big')
        result += hash_func(seed + c).digest()
        counter += 1
    return result[:length]


def _oaep_pad(message: bytes, key_size: int, hash_func=hashlib.sha256) -> bytes:
    """OAEP padding (PKCS#1 v2.1)."""
    h_len = hash_func().digest_size  # 32 for SHA-256
    k = key_size  # Key size in bytes (256 for 2048-bit key)
    
    # Label hash (empty label)
    l_hash = hash_func(b'').digest()
    
    # Padding
    m_len = len(message)
    ps_len = k - m_len - 2 * h_len - 2
    
    if ps_len < 0:
        raise ValueError("Message too long for key size")
    
    # DB = lHash || PS || 0x01 || M
    db = l_hash + (b'\x00' * ps_len) + b'\x01' + message
    
    # Random seed
    seed = random.randbytes(h_len)
    
    # Mask DB
    db_mask = _mgf1(seed, k - h_len - 1, hash_func)
    masked_db = bytes(a ^ b for a, b in zip(db, db_mask))
    
    # Mask seed
    seed_mask = _mgf1(masked_db, h_len, hash_func)
    masked_seed = bytes(a ^ b for a, b in zip(seed, seed_mask))
    
    # EM = 0x00 || maskedSeed || maskedDB
    return b'\x00' + masked_seed + masked_db


def _rsa_encrypt(padded: bytes, n: int, e: int) -> bytes:
    """Raw RSA encryption."""
    m = _int_from_bytes(padded)
    c = pow(m, e, n)
    return _int_to_bytes(c, 256)  # 2048-bit key = 256 bytes


def encrypt_password(password: str) -> str:
    """
    Encrypt password using Siege CSE (RSA-OAEP + custom header).
    
    Args:
        password: Plaintext password
        
    Returns:
        Siege CSE formatted encrypted password
    """
    # Parse RSA key
    n = _int_from_bytes(_base64url_decode(SIEGE_PUBLIC_KEY["n"]))
    e = _int_from_bytes(_base64url_decode(SIEGE_PUBLIC_KEY["e"]))
    
    # OAEP pad and encrypt
    padded = _oaep_pad(password.encode('utf-8'), 256)
    encrypted = _rsa_encrypt(padded, n, e)
    
    # Build Siege header
    # Format: 0x01 0x00 | "si:md5" | 0x00 | MD5_HEX | 0x01 0x00 | ENCRYPTED
    pwd_md5 = hashlib.md5(password.encode('utf-8')).hexdigest()
    
    header = (
        b'\x01\x00' +                    # Version
        b'si:md5' +                       # Scheme identifier
        b'\x00' +                         # Separator
        pwd_md5.encode('ascii') +         # MD5 hex (32 chars)
        b'\x01\x00'                       # Separator before ciphertext
    )
    
    payload = header + encrypted
    
    # Base64 encode with prefix
    return "AY" + base64.b64encode(payload).decode('ascii')


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

@dataclass
class AuthPayloads:
    """Generated authentication payloads"""
    fingerprint: str
    metadata1: str
    encrypted_pwd: str
    repassword: str


def generate_auth_payloads(password: str) -> AuthPayloads:
    """
    Generate all authentication payloads needed for login.
    
    Args:
        password: User's plaintext password
        
    Returns:
        AuthPayloads with metadata1 and encrypted_pwd
    """
    fp = generate_fingerprint()
    fp_json = json.dumps(fp, separators=(',', ':'))
    return AuthPayloads(
        fingerprint=fp_json,
        metadata1=generate_metadata1(fingerprint=fp),
        encrypted_pwd=encrypt_password(password),
        repassword=encrypt_password(password)
    )


# =============================================================================
# SPOOFED AUTH GENERATOR
# =============================================================================

def _extract_scripts(html_b64: str) -> Dict[str, Any]:
    """Extract dynamic URLs from base64 encoded HTML."""
    if not html_b64:
        return {"urls": [], "count": 0}
    
    html = base64.b64decode(html_b64).decode("utf-8", errors="ignore")
    
    urls = []
    for m in re.findall(
        r"\.load\.js\('(https://m\.media-amazon\.com/images/I/[^']+)'", html
    ):
        urls.append(m)
    
    if m := re.search(
        r'src="(https://static\.siege-amazon\.com/prod/profiles/AuthenticationPortalSigninNA\.js\?v=\d+)"',
        html,
    ):
        urls.append(m.group(1))
    
    urls = list(dict.fromkeys(urls))
    
    return {"urls": urls, "count": len(urls)}


def parse_user_agent(ua: str) -> dict:
    """Parse user agent string to extract browser and OS info."""
    result = {
        "browser": "unknown",
        "browser_version": 0,
        "os": "unknown",
        "os_version": 0,
        "device": "desktop"
    }
    
    ua = ua.lower()
    
    if "firefox" in ua:
        result["browser"] = "Firefox"
        try:
            result["browser_version"] = int(ua.split("firefox/")[1].split(".")[0])
        except:
            pass
        if "windows" in ua:
            result["os"] = "Windows"
            try:
                result["os_version"] = int(ua.split("windows nt ")[1].split(";")[0].split(".")[0])
            except:
                pass
        elif "linux" in ua:
            result["os"] = "Linux"
        elif "android" in ua:
            result["os"] = "Android"
            result["device"] = "mobile"
        elif "mac os x" in ua:
            result["os"] = "macOS"
            try:
                result["os_version"] = int(ua.split("mac os x ")[1].split(";")[0].replace(".",""))
            except:
                pass
    elif "chrome" in ua:
        result["browser"] = "Chrome"
        try:
            result["browser_version"] = int(ua.split("chrome/")[1].split(".")[0])
        except:
            pass
        if "windows" in ua:
            result["os"] = "Windows"
        elif "linux" in ua:
            result["os"] = "Linux"
        elif "android" in ua:
            result["os"] = "Android"
            result["device"] = "mobile"
        elif "crom" in ua:
            result["os"] = "macOS"
    elif "safari" in ua:
        result["browser"] = "Safari"
        try:
            result["browser_version"] = int(ua.split("version/")[1].split(".")[0])
        except:
            pass
        if "iphone" in ua or "ipad" in ua:
            result["os"] = "iOS"
            result["device"] = "mobile"
        elif "mac os x" in ua:
            result["os"] = "macOS"
    elif "edge" in ua:
        result["browser"] = "Edge"
        try:
            result["browser_version"] = int(ua.split("edge/")[1].split(".")[0])
        except:
            pass
        if "windows" in ua:
            result["os"] = "Windows"
    
    return result


def _generate_form_timing(name_len: int, email_len: int, password_len: int) -> dict:
    """Generate realistic form timing data based on input lengths (matching real fingerprints)."""
    timing = {
        "ap_customer_name": {
            "clicks": random.randint(1, 4),
            "touches": 0,
            "keyPresses": name_len if name_len > 0 else random.randint(10, 16),
            "cuts": 0,
            "copies": 0,
            "pastes": 0,
            "keyPressTimeIntervals": [random.randint(50, 400) for _ in range(random.randint(8, 14))],
            "mouseClickPositions": [f"{random.randint(150,350)},{random.randint(180,350)}" for _ in range(random.randint(3, 6))],
            "keyCycles": [random.randint(50, 180) for _ in range(random.randint(8, 14))],
            "mouseCycles": [random.randint(50, 180) for _ in range(random.randint(3, 6))],
            "touchCycles": [],
            "width": 312,
            "height": 32,
            "totalFocusTime": random.randint(2500, 4500),
            "autocomplete": False,
            "prefilled": False
        },
        "email": {
            "clicks": 0,
            "touches": 0,
            "keyPresses": 0,
            "cuts": 0,
            "copies": 0,
            "pastes": 0,
            "keyPressTimeIntervals": [],
            "mouseClickPositions": [],
            "keyCycles": [],
            "mouseCycles": [],
            "touchCycles": [],
            "width": 312,
            "height": 32,
            "totalFocusTime": random.randint(500, 1200),
            "autocomplete": False,
            "prefilled": random.choice([True, False])
        },
        "password": {
            "clicks": random.randint(1, 3),
            "touches": 0,
            "keyPresses": password_len if password_len > 0 else random.randint(6, 12),
            "cuts": 0,
            "copies": 0,
            "pastes": 0,
            "keyPressTimeIntervals": [random.randint(80, 350) for _ in range(random.randint(5, 10))],
            "mouseClickPositions": [f"{random.randint(150,350)},{random.randint(180,350)}" for _ in range(random.randint(3, 8))],
            "keyCycles": [random.randint(60, 200) for _ in range(random.randint(5, 10))],
            "mouseCycles": [random.randint(50, 200) for _ in range(random.randint(3, 8))],
            "touchCycles": [],
            "width": 312,
            "height": 32,
            "totalFocusTime": random.randint(2000, 4000),
            "autocomplete": False,
            "prefilled": False
        },
        "ap_password_check": {
            "clicks": random.randint(0, 2),
            "touches": 0,
            "keyPresses": random.randint(0, 2),
            "cuts": 0,
            "copies": 0,
            "pastes": 0,
            "keyPressTimeIntervals": [random.randint(100, 300) for _ in range(random.randint(1, 3))],
            "mouseClickPositions": [f"{random.randint(150,350)},{random.randint(180,350)}" for _ in range(random.randint(1, 3))],
            "keyCycles": [random.randint(80, 180) for _ in range(random.randint(1, 3))],
            "mouseCycles": [random.randint(50, 180) for _ in range(random.randint(1, 3))],
            "touchCycles": [],
            "width": 312,
            "height": 32,
            "totalFocusTime": random.randint(1000, 3000),
            "autocomplete": False,
            "prefilled": False
        }
    }
    return timing


def generate_spoofed_auth(
    method: str,
    user_agent: str,
    name: str,
    email: str,
    password: str,
    location: str = "",
    html_b64: str = "",
    ref: str = ""
) -> AuthPayloads:
    """
    Generate spoofed authentication data based on user agent.
    
    Args:
        method: Auth method (register, login, etc.)
        user_agent: Browser user agent string
        name: User's name
        email: User's email/phone
        password: User's password
        location: Current URL/location (optional)
        html_b64: Base64 encoded HTML (optional)
        ref: Referrer URL (optional)
        
    Returns:
        AuthPayloads with:
            - repassword: encrypted password
            - metadata1: FWCIM metadata
            - encrypted_pwd: encrypted password (same as repassword)
    """
    now = int(time.time() * 1000)
    start_time = now - random.randint(2000, 8000)
    
    ua_info = parse_user_agent(user_agent)
    
    extracted = _extract_scripts(html_b64)
    dynamic_urls = extracted["urls"]
    url_count = extracted["count"]
    
    if ua_info["device"] == "mobile":
        screen_width = random.choice([360, 375, 390, 411, 414])
        screen_height = random.choice([640, 667, 690, 712, 740, 800, 844])
    else:
        if ua_info["os"] == "Linux":
            screen_width = random.choice([1920, 1440, 2560, 1366, 1600, 1280])
            screen_height = random.choice([1080, 900, 1440, 768, 900, 1024])
        else:
            screen_width = random.choice([1280, 1366, 1440, 1536, 1920])
            screen_height = random.choice([720, 768, 800, 900, 1080])
    
    timezone_offset = random.choice([-9, -8, -7, -6, -5])
    
    form_data = _generate_form_timing(
        len(name) if name else 0,
        len(email) if email else 0,
        len(password) if password else 0
    )
    
    fp = {
        "metrics": {
            "el": 0,
            "script": 0,
            "h": 0,
            "batt": 0,
            "perf": 0,
            "auto": 0,
            "tz": 0,
            "fp2": 0,
            "lsubid": 0,
            "browser": 0,
            "capabilities": 0,
            "gpu": 0,
            "dnt": 0,
            "math": 0,
            "tts": 0,
            "input": 0,
            "canvas": 0,
            "captchainput": 0,
            "pow": 0
        },
        "start": start_time,
        "interaction": {
            "clicks": random.randint(3, 7),
            "touches": 0,
            "keyPresses": random.randint(25, 40),
            "cuts": 0,
            "copies": 0,
            "pastes": random.randint(0, 2),
            "keyPressTimeIntervals": [random.randint(50, 200) for _ in range(random.randint(5, 15))],
            "mouseClickPositions": (_mouse_data := _generate_mouse_human(random.randint(2, 6)))["mouseClickPositions"],
            "keyCycles": [random.randint(50, 180) for _ in range(random.randint(8, 20))],
            "mouseCycles": _mouse_data["mouseCycles"],
            "touchCycles": []
        },
        "scripts": {
            "dynamicUrls": dynamic_urls if dynamic_urls else [
                "https://images-na.ssl-images-amazon.com/images/I/215h87l68bL.js",
                "https://m.media-amazon.com/images/I/21ZMwVh4T0L._RC|21OJDARBhQL.js,218GJg15I8L.js,31lucpmF4CL.js,21juQdw6GzL.js,6155PkPoNgL.js_.js?AUIClients/AuthenticationPortalAssets",
                "https://m.media-amazon.com/images/I/01wGDSlxwdL.js?AUIClients/AuthenticationPortalInlineAssets",
                "https://m.media-amazon.com/images/I/418h4goWmdL.js?AUIClients/CVFAssets",
                "https://m.media-amazon.com/images/I/8150jbgvn9L.js?AUIClients/SiegeClientSideEncryptionAUI",
            ],
            "inlineHashes": [-314038750] + [random.randint(-2000000000, 2000000000) for _ in range(18)],
            "elapsed": random.choice([0, 1]),
            "dynamicUrlCount": url_count if url_count > 0 else 10,
            "inlineHashesCount": 19
        },
        "history": {
            "length": random.randint(6, 12)
        },
        "performance": {
            "timing": {
                "navigationStart": start_time - random.randint(100, 500),
                "unloadEventStart": start_time - random.randint(50, 200),
                "unloadEventEnd": start_time - random.randint(40, 150),
                "redirectStart": 0,
                "redirectEnd": 0,
                "fetchStart": start_time - random.randint(100, 400),
                "domainLookupStart": start_time - random.randint(100, 400),
                "domainLookupEnd": start_time - random.randint(80, 300),
                "connectStart": start_time - random.randint(80, 300),
                "connectEnd": start_time - random.randint(50, 200),
                "secureConnectionStart": start_time - random.randint(50, 200),
                "requestStart": start_time - random.randint(30, 150),
                "responseStart": start_time - random.randint(10, 80),
                "responseEnd": start_time - random.randint(5, 50),
                "domLoading": start_time - random.randint(20, 100),
                "domInteractive": start_time + random.randint(200, 600),
                "domContentLoadedEventStart": start_time + random.randint(250, 700),
                "domContentLoadedEventEnd": start_time + random.randint(280, 750),
                "domComplete": start_time + random.randint(500, 1200),
                "loadEventStart": start_time + random.randint(520, 1250),
                "loadEventEnd": start_time + random.randint(550, 1300)
            }
        },
        "automation": {
            "wd": {
                "properties": {
                    "document": [],
                    "window": [],
                    "navigator": []
                }
            },
            "phantom": {
                "properties": {
                    "window": []
                }
            }
        },
        "end": start_time + random.randint(300000, 650000),
        "timeZone": timezone_offset,
        "flashVersion": None,
        "plugins": _get_plugins_for_ua(user_agent, screen_width, screen_height),
        "dupedPlugins": _get_plugins_for_ua(user_agent, screen_width, screen_height),
        "screenInfo": f"{screen_width}-{screen_height}-{screen_height}-24-*-*-*",
        "lsUbid": f"X{random.randint(10,99)}-{random.randint(1000000,9999999)}-{random.randint(1000000,9999999)}:{start_time}",
        "referrer": ref,
        "userAgent": user_agent,
        "location": location,
        "webDriver": False,
        "capabilities": _get_capabilities_for_ua(user_agent),
        "gpu": _get_gpu_for_ua(user_agent) | {"extensions": _get_gpu_extensions(user_agent, _get_gpu_for_ua(user_agent))},
        "dnt": random.choice([0, 1]),
        "math": {
            "tan": "-1.4214488238747245",
            "sin": "0.8178819121159085",
            "cos": "-0.5753861119575491"
        },
        "form": form_data,
        "canvas": _generate_canvas_advanced(user_agent, email),
        "token": {
            "isCompatible": True,
            "pageHasCaptcha": 0
        },
        "auth": {
            "form": {
                "method": "post"
            }
        },
        "errors": [],
        "version": "4.0.0"
    }
    
    metadata1 = generate_metadata1(
        url=location if location else "https://www.amazon.com/ap/register",
        user_agent=user_agent,
        screen_width=screen_width,
        screen_height=screen_height,
        timezone_offset=timezone_offset,
        fingerprint=fp
    )
    
    encrypted_pwd = encrypt_password(password)
    fingerprint_json = json.dumps(fp, separators=(',', ':'))
    
    return AuthPayloads(
        fingerprint=fingerprint_json,
        metadata1=metadata1,
        encrypted_pwd=encrypted_pwd,
        repassword=encrypted_pwd,
    )


# =============================================================================
# CLI / TEST
# =============================================================================

if __name__ == "__main__":
    print("=== FWCIM Generator Test ===")
    metadata1 = generate_metadata1()
    print(f"metadata1: {metadata1[:80]}...")
    print(f"Length: {len(metadata1)} chars")
    
    print("\n=== Siege CSE Password Test ===")
    encrypted = encrypt_password("testpassword123")
    print(f"encryptedPwd: {encrypted[:80]}...")
    print(f"Length: {len(encrypted)} chars")
    
    print("\n=== Combined Test ===")
    payloads = generate_auth_payloads("mypassword")
    print(f"metadata1: {len(payloads.metadata1)} chars")
    print(f"encrypted_pwd: {len(payloads.encrypted_pwd)} chars")
    print(f"repassword: {len(payloads.repassword)} chars")
    
    print("\n=== Spoofed Auth Test ===")
    data_json = generate_spoofed_auth(
        method="register",
        user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
        name="javier garcias",
        email="5085565541",
        password="dfbc1992",
        ref="https://www.amazon.com/ap/signin",
    )
    print(f"fingerprint: {len(data_json.fingerprint)} chars")
    print(f"repassword: {data_json.repassword[:60]}...")
    print(f"metadata1: {data_json.metadata1[:60]}...")
    print(f"encrypted_pwd: {data_json.encrypted_pwd[:60]}...")
    print("\n✓ All generators working!")