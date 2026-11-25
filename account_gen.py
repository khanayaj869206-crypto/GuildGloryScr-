#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clean Free Fire Guest Account Generator (OB55)
==============================================
Creates fresh guest accounts for any region (IND, BD, SG, BR, ME),
saves them safely to accounts.json, and returns them directly to
guild_glory.py so 4 bots can join the target Guild immediately.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import random
import socket
import string
import threading
import time
import uuid
import warnings

import requests
import urllib3
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# ═══════════════ GARENA OAUTH FAST IP RESOLVER ═══════════════
_ORIG_GETADDRINFO = socket.getaddrinfo
_GARENA_FAST_IPS = ["43.205.19.48", "34.124.234.158"]


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == "100067.connect.garena.com":
        ip = random.choice(_GARENA_FAST_IPS)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)


socket.getaddrinfo = _patched_getaddrinfo

# ═══════════════ CRYPTO & CLIENT CONSTANTS ═══════════════
HKEY = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
xK = b"Yg&tc%DEuh6%Zc^8"
xV = b"6oyZDr22E3ychjM%"
MSDK_UA = "GarenaMSDK/4.0.42(NX809J ;Android 9;en;US;app 1.134.14 2024010012;)"
LOGIN_HOST = "loginbp.ppmainecoonghj.com"
RELEASE_VERSION = "OB55"
CLIENT_VERSION = "1.134.14"

FIELD22_HEX = (
    "47475245010101008202000048d0c6912766f3f72cedb7201cf7a7098d74888b9eb4070aec58e1cd29f26559fb3b8d32cc302d17c00fe629c20b94bece7b75867df5d2c63ad694fe754b06ca5f200fcc316532ee2d705c69d64e873a976d73f1bada85dedb1271bfd90f13e6eb1f607b197ea5741550829c40b5b9ab59f305fd460c462ac2ba9a50561b453053303105b3329e6aef4bf468da4ae276023c26fa39932d0ec40d874a849550e72f67b2f1e0974f835df6a8b5f3cd886483abd0da7cbccc6cd4df72aa5b104e63ea2bfeaf7b08d5176d2a9b128b8630468f625392397e2e43f63969e842f6e2c001c6da1cab2660eacaf8fdbc20520e1316a538406c480f3eefae89171f542b6aa5a8da797673764b0b05ac26a31511759cbdad6826ab621d208e0cf5282a76eb8453f8f768fd8d52dd408d64c0063149605de6af9b34bdf22202ea8d132cd797bbfb6ac72455000c493b18085957df0144f73b36b1aec77f8f5ca3c838d1c2b7e065db59df08b0e8b87dc6769a12ed8ed729c5a3c58468afc6f0c52604a3448b3dbca6a587ccff08bbdcd22e767169949942742142f52f837bfe97f49c801cad0b2d3fd22101188bd5e1dafeeabc4259abe0c7f2b96c5ded89eac5843e7cb95177755b6cf63edd6abaccf03fadcb7084988b2c28d7f3574da8775f6a2de3a429513837ad8319fefa6eb9bfaa1a1a8b06f9d58778c953ab1fc38b4569879416e65625d15ce233cbfffb2cbd56aeb16e07e05da7a52bff20f1d4e11643a328b28105ab74256507f9fccab3bfd0575b4b6fb0fdbeb448c9a1e4e780a4dfdabb2b487cd83f42764816d9a6f3e313349b95ce6fb5ca6dbb60d2f5c3b5157da94c84e2b7ce0e47e5dcbf4d7c146089b104979a5627e4f70a0a8d51a9ce358761119e0eb5ec3d2d7fea4f87227f9dfeaeb3a4ccd2b4cd38caa754c827d45ff0d00d520db11a49fdb6f0b4010203"
)
KEYSTREAM = [
    0x30, 0x30, 0x30, 0x32, 0x30, 0x31, 0x37, 0x30,
    0x30, 0x30, 0x30, 0x30, 0x32, 0x30, 0x31, 0x37,
    0x30, 0x30, 0x30, 0x30, 0x30, 0x32, 0x30, 0x31,
    0x37, 0x30, 0x30, 0x30, 0x30, 0x30, 0x32, 0x30,
]

_HTTP = requests.Session()
_HTTP.verify = False
_adapter = requests.adapters.HTTPAdapter(pool_connections=128, pool_maxsize=128, max_retries=1)
_HTTP.mount("https://", _adapter)
_HTTP.mount("http://", _adapter)
_HTTP.headers.update({"Accept-Encoding": "gzip, deflate", "Connection": "Keep-Alive"})

_PROXY_POOL = []
_PROXY_INDEX = 0
_PROXY_LOCK = threading.Lock()
_LAST_PROXY_FETCH = 0.0
_FILE_LOCK = threading.Lock()


def _fetch_proxies():
    global _PROXY_POOL, _LAST_PROXY_FETCH
    if time.time() - _LAST_PROXY_FETCH < 300 and _PROXY_POOL:
        return
    sources = [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=2500&country=all&ssl=yes&anonymity=elite",
        "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    ]
    fetched = []
    for src in sources:
        try:
            r = requests.get(src, timeout=2.5)
            fetched.extend([p.strip() for p in r.text.strip().split("\n") if ":" in p])
            if len(fetched) >= 60:
                break
        except Exception:
            continue
    if fetched:
        with _PROXY_LOCK:
            _PROXY_POOL = fetched
            _LAST_PROXY_FETCH = time.time()
            random.shuffle(_PROXY_POOL)


def _next_proxy():
    global _PROXY_INDEX
    with _PROXY_LOCK:
        if not _PROXY_POOL:
            _fetch_proxies()
        if not _PROXY_POOL:
            return None
        _PROXY_INDEX = (_PROXY_INDEX + 1) % len(_PROXY_POOL)
        return _PROXY_POOL[_PROXY_INDEX]


def _gen_sig(payload: str) -> str:
    return hmac.new(HKEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _aes_encrypt(data: bytes) -> bytes:
    return AES.new(xK, AES.MODE_CBC, xV).encrypt(pad(data, 16))


def _vi(n: int) -> bytes:
    d = []
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        d.append(b)
        if not n:
            break
    return bytes(d)


def _proto(fields: dict) -> bytes:
    out = bytearray()
    for f, v in fields.items():
        if isinstance(v, int):
            out += _vi((f << 3) | 0) + _vi(v)
        elif isinstance(v, (str, bytes)):
            b = v.encode() if isinstance(v, str) else v
            out += _vi((f << 3) | 2) + _vi(len(b)) + b
        elif isinstance(v, dict):
            nested = _proto(v)
            out += _vi((f << 3) | 2) + _vi(len(nested)) + nested
    return bytes(out)


def _first_varint(data: bytes, want: int):
    off = 0
    while off < len(data):
        key = shift = 0
        while True:
            b = data[off]
            off += 1
            key |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v = shift = 0
            while True:
                b = data[off]
                off += 1
                v |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            if fn == want:
                return v
        elif wt == 2:
            ln = shift = 0
            while True:
                b = data[off]
                off += 1
                ln |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            off += ln
        elif wt == 1:
            off += 8
        elif wt == 5:
            off += 4
        else:
            break
    return None


def _gen_nickname(prefix: str) -> str:
    clean = "".join(ch for ch in (prefix or "Glory") if ch.isalnum())[:8] or "Glory"
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"{clean}{suffix}"


def _gen_password() -> str:
    body = "".join(random.choices(string.ascii_letters + string.digits, k=26))
    return f"GLORY_{body}"


def _server_time():
    try:
        r = requests.head("https://www.google.com", timeout=3)
        d = r.headers.get("Date")
        if d:
            dt = datetime.strptime(d, "%a, %d %b %Y %H:%M:%S GMT")
            return int(dt.replace(tzinfo=timezone.utc).timestamp()), dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return int(time.time()), time.strftime("%Y-%m-%d %H:%M:%S")


def _guest_register(max_retries: int = 8):
    for attempt in range(max_retries):
        try:
            password = _gen_password()
            body = json.dumps({"app_id": 100067, "client_type": 2, "password": password, "source": 2}, separators=(",", ":"))
            headers = {
                "User-Agent": MSDK_UA,
                "Authorization": f"Signature {_gen_sig(body)}",
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "Host": "100067.connect.garena.com",
                "Connection": "Keep-Alive",
            }
            prox = None
            if attempt > 1:
                p = _next_proxy()
                if p:
                    prox = {"http": f"http://{p}", "https": f"http://{p}"}
            if prox:
                r = requests.post(
                    "https://100067.connect.garena.com/api/v2/oauth/guest:register",
                    headers=headers,
                    data=body,
                    timeout=2.5,
                    proxies=prox,
                    verify=False,
                )
            else:
                r = _HTTP.post(
                    "https://100067.connect.garena.com/api/v2/oauth/guest:register",
                    headers=headers,
                    data=body,
                    timeout=2.5,
                    verify=False,
                )
            if r.status_code == 200:
                j = r.json()
                if "data" in j and "uid" in j["data"]:
                    return str(j["data"]["uid"]), password
            elif r.status_code == 429:
                _fetch_proxies()
                time.sleep(0.25)
            else:
                time.sleep(0.15)
        except Exception:
            time.sleep(0.15)
    return None, None


def _guest_token(uid: str, password: str, max_retries: int = 6):
    for attempt in range(max_retries):
        try:
            body = json.dumps(
                {
                    "client_id": 100067,
                    "client_secret": HKEY,
                    "client_type": 2,
                    "password": password,
                    "response_type": "token",
                    "uid": int(uid),
                },
                separators=(",", ":"),
            )
            headers = {
                "User-Agent": MSDK_UA,
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "Host": "100067.connect.garena.com",
                "Connection": "Keep-Alive",
            }
            prox = None
            if attempt > 1:
                p = _next_proxy()
                if p:
                    prox = {"http": f"http://{p}", "https": f"http://{p}"}
            if prox:
                r = requests.post(
                    "https://100067.connect.garena.com/api/v2/oauth/guest/token:grant",
                    headers=headers,
                    data=body,
                    timeout=2.5,
                    proxies=prox,
                    verify=False,
                )
            else:
                r = _HTTP.post(
                    "https://100067.connect.garena.com/api/v2/oauth/guest/token:grant",
                    headers=headers,
                    data=body,
                    timeout=2.5,
                    verify=False,
                )
            if r.status_code == 200:
                j = r.json()
                if "data" in j and "access_token" in j["data"]:
                    d = j["data"]
                    return {"access_token": d["access_token"], "open_id": d.get("open_id", "")}
            elif r.status_code in (400, 429):
                _fetch_proxies()
                time.sleep(0.2)
            time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            time.sleep(0.15)
    return None


def _major_register(open_id: str, access_token: str, nickname: str, sync_ts: int, lang: str = "en", max_retries: int = 6):
    encoded = "".join(chr(ord(c) ^ KEYSTREAM[i % len(KEYSTREAM)]) for i, c in enumerate(open_id))
    fields = {
        1: nickname,
        2: access_token,
        3: open_id,
        5: 102000007,
        6: 4,
        7: 1,
        8: 4,
        13: 1,
        14: encoded.encode("latin1"),
        15: lang,
        16: 1,
        20: CLIENT_VERSION,
        21: 1,
        22: bytes.fromhex(FIELD22_HEX),
    }
    payload = _aes_encrypt(_proto(fields))
    headers = {
        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "Accept": "*/*",
        "X-GA-SV": str(sync_ts),
        "Authorization": "Bearer",
        "X-GA": "v1 1",
        "ReleaseVersion": RELEASE_VERSION,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Unity-Version": "2018.4.12f1",
        "Host": LOGIN_HOST,
        "Connection": "Keep-Alive",
    }
    for attempt in range(max_retries):
        try:
            prox = None
            if attempt > 1:
                p = _next_proxy()
                if p:
                    prox = {"http": f"http://{p}", "https": f"http://{p}"}
            if prox:
                r = requests.post(
                    f"https://{LOGIN_HOST}/MajorRegister",
                    data=payload,
                    headers=headers,
                    timeout=2.5,
                    proxies=prox,
                    verify=False,
                )
            else:
                r = _HTTP.post(
                    f"https://{LOGIN_HOST}/MajorRegister",
                    data=payload,
                    headers=headers,
                    timeout=2.5,
                    verify=False,
                )
            if r.status_code == 200:
                aid = _first_varint(r.content, 3)
                if aid:
                    return str(aid)
            elif r.status_code in (400, 429):
                _fetch_proxies()
                time.sleep(0.2)
            time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            time.sleep(0.15)
    return None


def _build_region_login_payload(access_token: str, open_id: str, server_time_str: str, region: str) -> bytes:
    reg = str(region or "IND").upper()
    P = {}
    if reg == "IND":
        P[3] = server_time_str; P[4] = "free fire"; P[5] = 1; P[7] = CLIENT_VERSION
        P[8] = "Android OS 14 / API-34 (UP1A.231005.007/E156BXXS6AYD4)"
        P[9] = "Handheld"; P[10] = "airtel"; P[11] = "CarrierDataNetwork"
        P[12] = 2340; P[13] = 1080; P[14] = "295"
        P[15] = "ARM64 FP ASIMD AES | 2200 | 8"; P[16] = 5586
        P[17] = "Mali-G57 MC2"; P[18] = "OpenGL ES 3.2 v1.r38p1"
        P[19] = f"Google|{uuid.uuid4()}"
        P[20] = "27.61.46.111"; P[21] = "en"; P[22] = open_id; P[23] = "4"
        P[24] = "Handheld"; P[25] = "samsung SM-A155F"; P[26] = "IND"
        P[29] = access_token; P[30] = 1; P[41] = "airtel"; P[42] = "4G"
        P[57] = "7428b253defc164018c604a1ebbfebdf"
        P[60] = 108009; P[61] = 3859; P[62] = 5023; P[64] = 3987
        P[65] = 108009; P[66] = 3987; P[67] = 108009; P[70] = 4; P[73] = 3
        P[74] = "/data/app/~~OlJmIW-Yzl-44x2WnJP4Nw==/com.garena.game.ffi-_eDk_pQQMiFk1YUeqwJBvQ==/lib/arm64"
        P[76] = 3
        P[77] = "dd9d4c174e0472d34b7fd34bd5924bc9|/data/app/~~OlJmIW-Yzl-44x2WnJP4Nw==/com.garena.game.ffi-_eDk_pQQMiFk1YUeqwJBvQ==/base.apk"
        P[78] = 3; P[79] = 2; P[81] = "64"; P[83] = "2019121269"
        P[85] = 3; P[86] = "OpenGLES2"; P[87] = 8191; P[88] = 4
        P[90] = "Coimbatore"; P[91] = "TN"; P[92] = 9570; P[93] = "android"
        P[94] = "KqsHT54jON6bxITmlrRyOrXWBqKqZ4BoqPzT/mhtriAfRt2dt7gC8pnAJfj11alcEUA5sD8BshSYQhxXT9RFmU6YzcI1FKK4MwxJcF4+kvelPD1H"
        P[95] = 111207; P[96] = '{"cur_rate":null,"support_etc2":false}'
        P[97] = 1; P[99] = "4"; P[100] = "4"; P[104] = 11921; P[105] = 1
        P[106] = "https://dl-tata.freefireind.in/live/ABHotUpdates/|https://core-tata.freefireind.in/live/ABHotUpdates/|dff941d41e6e3a9e76f05ed71fdc8d87"
        P[107] = "1.6168622e0868ddbd"
    else:
        P[3] = server_time_str; P[4] = "free fire"; P[5] = 1; P[7] = CLIENT_VERSION
        P[8] = "Android OS 14 / API-34 (UP1A.231005.007/V816.0.15.0.UNQMIXM)"
        P[9] = "Handheld"; P[10] = "Grameenphone"; P[11] = "CarrierDataNetwork"
        P[12] = 1666; P[13] = 750; P[14] = "352"
        P[15] = "ARM64 FP ASIMD AES | 2400 | 8"; P[16] = 5563
        P[17] = "Mali-G57 MC2"; P[18] = "OpenGL ES 3.2 v1.r32p1-01eac0.0a0b917d9265b4c01be9925306306139"
        P[19] = f"Google|{uuid.uuid4()}"
        P[20] = "59.152.5.188"; P[21] = "en"; P[22] = open_id; P[23] = "8"
        P[24] = "Handheld"; P[25] = "Xiaomi 2312DRAABI"; P[26] = reg
        P[29] = access_token; P[30] = 1; P[41] = "Grameenphone"; P[42] = "Cellular"
        P[57] = "7428b253defc164018c604a1ebbfebdf"
        P[60] = 109685; P[61] = 58294; P[62] = 797; P[63] = 18000
        P[64] = 58818; P[65] = 109685; P[66] = 58818; P[67] = 109685; P[73] = 2
        P[74] = "/data/app/~~cqQgpdMykLQEM3odipXatw==/com.dts.freefireth-G6e5yOt1dtuXG5Ambhj18g==/lib/arm64"
        P[76] = 1
        P[77] = "b8e0cd5e295eee42f5860d3c86e483dd|/data/app/~~cqQgpdMykLQEM3odipXatw==/com.dts.freefireth-G6e5yOt1dtuXG5Ambhj18g==/base.apk"
        P[78] = 3; P[79] = 2; P[81] = "64"; P[83] = "2019121229"
        P[85] = 3; P[86] = "OpenGLES2"; P[87] = 4095; P[88] = 8
        P[89] = b"FwQVTgUPX1UaUllDDwcWCRBpWAUOUgsvA1snWlBaO1kFYg=="
        P[90] = "Dhaka"; P[91] = "C"; P[92] = 8043; P[93] = "android"
        P[94] = "KqsHT4XbSZhqerDNUhjbS3DxG/4+I62zQU2tZFJN91kBUICAlZ5yujshLKvjlk/KiShfKfis8EeB1PMhFHIodWPncZC4fCHYF3TcTwNj95aWY/S3"
        P[95] = 111207; P[96] = '{"cur_rate":[60,90,120],"support_etc2":false}'
        P[97] = 1; P[98] = 1; P[99] = "4"; P[100] = "4"; P[102] = "GQ]        e"
        P[103] = 1; P[104] = 45174; P[105] = 1
        P[106] = "https://dl-bs.ggpolarbear.com/live/ABHotUpdates/|https://core-bs.ggpolarbear.com/live/ABHotUpdates/|a4332cb1c1a84e51dd77441e4856ed5a"
        P[107] = "1.9393e7b8e53e8aeb"
    return _aes_encrypt(_proto(P))


def _major_login_init(payload: bytes, sync_ts: int, max_retries: int = 6):
    url = f"https://{LOGIN_HOST}/MajorLogin"
    headers = {
        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "Accept": "*/*",
        "Accept-Encoding": "deflate, gzip",
        "X-GA-SV": str(sync_ts),
        "Authorization": "Bearer ",
        "X-GA": "v1 1",
        "ReleaseVersion": RELEASE_VERSION,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Unity-Version": "2018.4.12f1",
    }
    for attempt in range(max_retries):
        try:
            prox = None
            if attempt > 1:
                p = _next_proxy()
                if p:
                    prox = {"http": f"http://{p}", "https": f"http://{p}"}
            r = requests.post(url, headers=headers, data=payload, timeout=2.5, proxies=prox, verify=False)
            if r.status_code in (200, 201) and r.content and len(r.content) > 64:
                return r.content
            elif r.status_code in (400, 429):
                _fetch_proxies()
                time.sleep(0.2)
            time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            time.sleep(0.15)
    return None


def _extract_major_login(ml_resp: bytes):
    if not ml_resp or len(ml_resp) <= 64:
        return None
    body = ml_resp[64:] if ml_resp[0] != 0x0A else ml_resp
    f = {}
    pos, n = 0, len(body)
    while pos < n:
        try:
            key = shift = 0
            while True:
                b = body[pos]
                pos += 1
                key |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
        except Exception:
            break
        fn, wt = key >> 3, key & 0x07
        try:
            if wt == 0:
                val = shift = 0
                while True:
                    b = body[pos]
                    pos += 1
                    val |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
            elif wt == 2:
                ln = shift = 0
                while True:
                    b = body[pos]
                    pos += 1
                    ln |= (b & 0x7F) << shift
                    if not (b & 0x80):
                        break
                    shift += 7
                val = body[pos : pos + ln]
                pos += ln
            elif wt == 5:
                val = body[pos : pos + 4]
                pos += 4
            elif wt == 1:
                val = body[pos : pos + 8]
                pos += 8
            else:
                break
        except Exception:
            break
        f.setdefault(fn, []).append(val)

    jwt_b = f.get(8, [None])[0]
    key_b = f.get(22, [None])[0]
    iv_b = f.get(23, [None])[0]
    if not jwt_b or not isinstance(key_b, bytes) or not isinstance(iv_b, bytes):
        return None
    return {"jwt": jwt_b.decode("utf-8", "ignore"), "key": key_b, "iv": iv_b}


def _append_accounts_json(accounts_json_path: str, acc_entry: dict):
    if not accounts_json_path:
        return
    with _FILE_LOCK:
        existing = []
        if os.path.exists(accounts_json_path):
            try:
                with open(accounts_json_path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                    if raw:
                        existing = json.loads(raw)
            except Exception:
                existing = []
        if not any(str(a.get("uid")) == str(acc_entry["uid"]) for a in existing):
            existing.append(
                {
                    "uid": acc_entry["uid"],
                    "password": acc_entry["password"],
                    "region": acc_entry["region"],
                    "name": acc_entry["name"],
                }
            )
            tmp = accounts_json_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            os.replace(tmp, accounts_json_path)


def create_one_account_sync(region: str = "IND", name_prefix: str = "Glory"):
    sync_ts, sync_time_str = _server_time()
    uid, password = _guest_register()
    if not uid or not password:
        return None
    tok = _guest_token(uid, password)
    if not tok:
        return None
    open_id = tok["open_id"]
    access_token = tok["access_token"]
    nickname = _gen_nickname(name_prefix)

    account_id = _major_register(open_id, access_token, nickname, sync_ts, "en")
    if not account_id:
        return None

    login_payload = _build_region_login_payload(access_token, open_id, sync_time_str, region)
    ml = _major_login_init(login_payload, sync_ts)
    if not ml:
        return None
    ad = _extract_major_login(ml)
    if not ad:
        return None

    return {
        "uid": uid,
        "password": password,
        "account_id": account_id,
        "name": nickname,
        "region": str(region or "IND").upper(),
    }


async def generate_accounts(
    count: int = 4,
    region: str = "IND",
    name_prefix: str = "Glory",
    accounts_json_path: str = "",
    stop_evt: asyncio.Event = None,
    on_progress=None,
    concurrency: int = 6,
):
    """
    Generates `count` (default 4) fresh Free Fire accounts concurrently,
    persists them cleanly to accounts.json, and returns them for guild_glory.py.
    """
    created = []
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(concurrency)

    async def _worker():
        while len(created) < count:
            if stop_evt and stop_evt.is_set():
                break
            async with sem:
                if len(created) >= count or (stop_evt and stop_evt.is_set()):
                    break
                acc = await asyncio.to_thread(create_one_account_sync, region, name_prefix)
                if not acc:
                    await asyncio.sleep(0.25)
                    continue
                async with lock:
                    if len(created) >= count:
                        break
                    created.append(acc)
                    if accounts_json_path:
                        await asyncio.to_thread(_append_accounts_json, accounts_json_path, acc)
                    if on_progress:
                        try:
                            on_progress(len(created), count, acc)
                        except Exception:
                            pass

    workers = [asyncio.create_task(_worker()) for _ in range(min(concurrency, max(2, count * 2)))]
    deadline = time.time() + 120.0
    try:
        while len(created) < count and time.time() < deadline:
            if stop_evt and stop_evt.is_set():
                break
            if all(w.done() for w in workers):
                break
            await asyncio.sleep(0.2)
    finally:
        for w in workers:
            if not w.done():
                w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    return created[:count]


async def _cli_main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(base_dir, "accounts.json")
    reg = (input("Region (IND / BD / SG / BR / ME) [IND]: ").strip() or "IND").upper()
    prefix = input("Account name prefix [Glory]: ").strip() or "Glory"
    raw_n = input("How many accounts to create [4]: ").strip()
    n = int(raw_n) if raw_n.isdigit() and int(raw_n) > 0 else 4

    print(f"[*] Creating {n} accounts in {reg}...", flush=True)

    def _on_prog(done, total, acc):
        print(f"[+] [{done}/{total}] {acc['name']} | UID={acc['uid']} | GameID={acc['account_id']}", flush=True)

    res = await generate_accounts(count=n, region=reg, name_prefix=prefix, accounts_json_path=out_path, on_progress=_on_prog)
    print(f"[✓] Created {len(res)}/{n} accounts -> {out_path}")


if __name__ == "__main__":
    try:
        asyncio.run(_cli_main())
    except KeyboardInterrupt:
        print("\n[!] Stopped.")
