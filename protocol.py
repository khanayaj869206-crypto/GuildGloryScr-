# -*- coding: utf-8 -*-
"""
Clean Free Fire (OB55) Guild Glory Protocol Module
Contains only Login, Guild Join, Squad BR TCP Framing, and UDP Gameplay Session.
Zero Level-Up / Web / Solo Lone-Wolf bloat.
"""

import asyncio
import base64
import json
import random
import re
import secrets
import socket
import struct
import time
from urllib.parse import parse_qs, urlparse

import requests
import urllib3
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from movement import MovementController

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ═════════════════════════ CONSTANTS ═════════════════════════
AES_KEY = b"Yg&tc%DEuh6%Zc^8"
AES_IV = b"6oyZDr22E3ychjM%"

CLIENT_SECRET = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
APP_ID = "100067"
OAUTH_URL = "https://100067.connect.garena.com/oauth/guest/token/grant"
MAJOR_LOGIN_URL = "https://loginbp.ppmainecoonghj.com/MajorLogin"
GETLOGIN_HOST = "clientbp.freefireind.in"

CLIENT_VERSION = "2.133.21"
UDP_CLIENT_VERSION = "1.134.21"
SQUAD_CLIENT_VER = "1.134.21"
RELEASE_VERSION = "OB55"
REGION = "IND"

HANDSHAKE_STEP_DELAY = 10.0
PING_ENABLED = True
PING_INTERVAL = 3.0
PING_TICK_BASE = 0xFFFFFFFF

MSG_KEY_HELLO = 0x63
MSG_KEY_ACK_HELLO = 0x5B
MSG_KEY_ACK = 0x68
MSG_KEY_AUTH_447 = 0x5E
MSG_KEY_AUTH_448 = 0x5D
MSG_KEY_PING_POOL = (0x66, 0x6D, 0x69, 0x6C, 0x6B, 0x6E, 0x6F, 0x70)

CMD_NAMES = {
    1: "HELLO",
    2: "ACK",
    3: "PING",
    100: "MATCH_INIT",
    117: "PLAYER_STATE",
    121: "ZONE_TICK",
    176: "DATA",
    447: "AUTH#1",
    448: "AUTH#2",
    551: "ZONE_DMG",
    849: "DATA",
    2000: "ECHO_TICK",
    2001: "MOVE",
    2011: "SELF_TICK",
    2012: "PLAYER_LIST",
}

FIELD_SIZES = {0: 1, 1: 2, 2: 2, 3: 1, 4: 2}
FIELD_NAMES = {0: "sendOption", 1: "cmd", 2: "orderId", 3: "flags", 4: "length"}

UDP_CLIENT_IP = "127.0.0.1"
UDP_DEVICE_HASH = "8fc4a8858c245f80da34b0d88f10540b7f868bdb81a5f31b38241147c0af6b6d"

PRESET_73 = dict(
    mode_id=73,
    a0=0x22,
    a_p=1,
    a_last=4,
    head_last=1,
    pre=0,
    slot_byte=1,
    device_fingerprint="",
    hot_update_url="",
    device_hash="8aaf4b2980573afccd892b5f8f1551e8814ac1c9916a3fc94bbdb41d2a8a7839",
)

CLOUDFLARE_PRIMARY_DNS = "1.1.1.1"
CLOUDFLARE_SECONDARY_DNS = "1.0.0.1"
DNS_CACHE_TTL = 300.0
_DNS_CACHE = {}

# ═════════════════════════ LOGGING HELPERS ═════════════════════════
def ok(m):
    print(f"\033[92m[+]\033[0m {m}", flush=True)


def info(m):
    print(f"\033[96m[i]\033[0m {m}", flush=True)


def warn(m):
    print(f"\033[93m[!]\033[0m {m}", flush=True)


def err(m):
    print(f"\033[91m[-]\033[0m {m}", flush=True)


# ═════════════════════════ CRYPTO & CRC7 ═════════════════════════
_CRC7_TABLE = bytes([
    0,9,18,27,36,45,54,63,72,65,90,83,108,101,126,119,25,16,11,2,61,52,47,38,
    81,88,67,74,117,124,103,110,50,59,32,41,22,31,4,13,122,115,104,97,94,87,
    76,69,43,34,57,48,15,6,29,20,99,106,113,120,71,78,85,92,100,109,118,127,
    64,73,82,91,44,37,62,55,8,1,26,19,125,116,111,102,89,80,75,66,53,60,39,
    46,17,24,3,10,86,95,68,77,114,123,96,105,30,23,12,5,58,51,40,33,79,70,
    93,84,107,98,121,112,7,14,21,28,35,42,49,56,65,72,83,90,101,108,119,126,
    9,0,27,18,45,36,63,54,88,81,74,67,124,117,110,103,16,25,2,11,52,61,38,
    47,115,122,97,104,87,94,69,76,59,50,41,32,31,22,13,4,106,99,120,113,78,
    71,92,85,34,43,48,57,6,15,20,29,37,44,55,62,1,8,19,26,109,100,127,118,
    73,64,91,82,60,53,46,39,24,17,10,3,116,125,102,111,80,89,66,75,23,30,5,
    12,51,58,33,40,95,86,77,68,123,114,105,96,14,7,28,21,42,35,56,49,70,79,
    84,93,98,107,112,121,
])


def crc7(buf, c=0):
    c &= 0x7F
    for b in buf:
        c = _CRC7_TABLE[((2 * c) ^ b) & 0xFF] & 0x7F
    return c


_M = 0xFFFFFFFF
_DELTA = 0x9E3779B9
_ROUNDS = 16


def _tea_enc(v0, v1, k):
    s = 0
    for _ in range(_ROUNDS):
        s = (s + _DELTA) & _M
        v0 = (v0 + ((((v1 << 4) + k[0]) & _M) ^ ((v1 + s) & _M) ^ ((v1 >> 5) + k[1]))) & _M
        v1 = (v1 + ((((v0 << 4) + k[2]) & _M) ^ ((v0 + s) & _M) ^ ((v0 >> 5) + k[3]))) & _M
    return v0, v1


def _tea_dec(v0, v1, k):
    s = (_DELTA * _ROUNDS) & _M
    for _ in range(_ROUNDS):
        v1 = (v1 - ((((v0 << 4) + k[2]) & _M) ^ ((v0 + s) & _M) ^ ((v0 >> 5) + k[3]))) & _M
        v0 = (v0 - ((((v1 << 4) + k[0]) & _M) ^ ((v1 + s) & _M) ^ ((v1 >> 5) + k[1]))) & _M
        s = (s - _DELTA) & _M
    return v0, v1


def _udp_pad(content):
    pad_len = (8 - (len(content) + 10) % 8) % 8
    return bytes([pad_len]) + bytes(pad_len + 2) + content + bytes(7)


def _udp_unpad(padded):
    if len(padded) < 8 or any(padded[-1 - i] for i in range(7)):
        return None
    n = padded[0] & 7
    s, e = 3 + n, len(padded) - 7
    return padded[s:e] if s < e else b""


def udp_encrypt_body(content, key):
    padded = _udp_pad(content)
    k = struct.unpack("<4I", key)
    out = bytearray(len(padded))
    pc, pi = bytes(8), bytes(8)
    for i in range(0, len(padded), 8):
        d = bytes(a ^ b for a, b in zip(padded[i:i + 8], pc))
        x = struct.pack("<2I", *_tea_enc(*struct.unpack("<2I", d), k))
        c = bytes(a ^ b for a, b in zip(x, pi))
        out[i:i + 8] = c
        pc, pi = c, d
    return bytes(out)


def udp_decrypt_body(body, key):
    k = struct.unpack("<4I", key)
    out = bytearray(len(body))
    pc, pi = bytes(8), bytes(8)
    for i in range(0, len(body), 8):
        x = bytes(a ^ b for a, b in zip(body[i:i + 8], pi))
        d = struct.pack("<2I", *_tea_dec(*struct.unpack("<2I", x), k))
        out[i:i + 8] = bytes(a ^ b for a, b in zip(d, pc))
        pc, pi = body[i:i + 8], d
    return bytes(out)


def aes_encrypt(data, key=AES_KEY, iv=AES_IV):
    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(data, AES.block_size))


def aes_decrypt(data, key=AES_KEY, iv=AES_IV):
    if not data or len(data) % 16:
        return None
    try:
        try:
            return unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(data), AES.block_size)
        except ValueError:
            return AES.new(key, AES.MODE_CBC, iv).decrypt(data)
    except Exception:
        return None


# ═════════════════════════ PROTOBUF HELPERS ═════════════════════════
def varint(n):
    if n < 0:
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            break
    return bytes(out)


def read_varint(buf, i):
    n = shift = 0
    while True:
        c = buf[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if not (c & 0x80):
            return n, i


def zigzag_str(s):
    if isinstance(s, str):
        s = s.encode()
    n = len(s) << 1
    out = bytearray()
    while n > 0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out) + s


def _wv(v):
    return varint(v)


def _fv(n, v):
    return _wv((n << 3) | 0) + _wv(int(v))


def _fs(n, s):
    b = s.encode() if isinstance(s, str) else bytes(s)
    return _wv((n << 3) | 2) + _wv(len(b)) + b


def serialize(fields):
    out = bytearray()
    for k, v in fields.items():
        if isinstance(v, bool):
            out += _fv(k, 1 if v else 0)
        elif isinstance(v, int):
            out += _fv(k, v)
        elif isinstance(v, str):
            out += _fs(k, v)
        elif isinstance(v, (bytes, bytearray)):
            out += _fs(k, bytes(v))
        elif isinstance(v, dict):
            out += _fs(k, serialize(v))
    return bytes(out)


def _rv(buf, off):
    r = s = 0
    while True:
        if off >= len(buf):
            raise ValueError("eof")
        b = buf[off]
        off += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            return r, off
        s += 7
        if s > 63:
            raise ValueError("long")


def _decode_len(raw, depth):
    if raw and all(32 <= b < 127 or b in (9, 10, 13) for b in raw):
        try:
            return raw.decode("utf-8")
        except Exception:
            pass
    if depth < 4 and len(raw) >= 2:
        try:
            sub, used = _parse(raw, depth + 1)
            if used == len(raw) and sub:
                return sub
        except Exception:
            pass
    try:
        return raw.decode("utf-8")
    except Exception:
        return raw


def _parse(buf, depth=0):
    out, off = {}, 0
    while off < len(buf):
        try:
            tag, off = _rv(buf, off)
        except Exception:
            break
        f, w = tag >> 3, tag & 7
        if f == 0:
            break
        try:
            if w == 0:
                v, off = _rv(buf, off)
            elif w == 1:
                v = buf[off:off + 8]
                off += 8
            elif w == 2:
                ln, off = _rv(buf, off)
                chunk = buf[off:off + ln]
                off += ln
                v = _decode_len(chunk, depth)
            elif w == 5:
                v = buf[off:off + 4]
                off += 4
            else:
                break
        except Exception:
            break
        if f in out:
            out[f] = out[f] + [v] if isinstance(out[f], list) else [out[f], v]
        else:
            out[f] = v
    return out, off


def parse_proto(data):
    try:
        return _parse(data, 0)[0]
    except Exception:
        return {}


def first(d, k, default=None):
    v = d.get(k, default)
    if isinstance(v, list):
        v = v[0] if v else default
    return v


def to_int(v, default=0):
    v = first({0: v}, 0)
    if v is None:
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, bytes):
        try:
            return int(v.decode("ascii", "ignore"))
        except Exception:
            return default
    try:
        return int(v)
    except Exception:
        return default


def to_str(v, default=""):
    v = first({0: v}, 0)
    if v is None:
        return default
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", "ignore")
        except Exception:
            return default
    return str(v)


def collect_uids(obj, out=None):
    """Collects all integer & numeric-string IDs inside a parsed protobuf tree."""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for v in obj.values():
            collect_uids(v, out)
    elif isinstance(obj, list):
        for x in obj:
            collect_uids(x, out)
    elif isinstance(obj, int):
        out.add(obj)
    elif isinstance(obj, str) and obj.isdigit():
        out.add(int(obj))
    elif isinstance(obj, (bytes, bytearray)):
        if len(obj) == 8:
            out.add(int.from_bytes(obj, "little"))
            out.add(int.from_bytes(obj, "big"))
    return out


# ═════════════════════════ DNS & HTTP HELPERS ═════════════════════════
def _dns_query_sync(hostname, server_ip):
    s = None
    try:
        tx = random.randint(1000, 65535)
        header = struct.pack(">HHHHHH", tx, 0x0100, 1, 0, 0, 0)
        qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in hostname.split(".")) + b"\x00"
        q = header + qname + struct.pack(">HH", 1, 1)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.2)
        s.sendto(q, (server_ip, 53))
        resp, _ = s.recvfrom(1024)
        if len(resp) >= 12 and struct.unpack(">H", resp[6:8])[0] > 0:
            off = 12 + len(qname) + 4
            for _ in range(struct.unpack(">H", resp[6:8])[0]):
                if off >= len(resp):
                    break
                if (resp[off] & 0xC0) == 0xC0:
                    off += 2
                else:
                    while off < len(resp) and resp[off] != 0:
                        off += 1 + resp[off]
                    off += 1
                if off + 10 > len(resp):
                    break
                rt, _, _, rdlen = struct.unpack(">HHIH", resp[off:off + 10])
                off += 10
                if rt == 1 and rdlen == 4 and off + 4 <= len(resp):
                    return socket.inet_ntoa(resp[off:off + 4])
                off += rdlen
    except Exception:
        pass
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass
    return None


def resolve_host_sync(hostname):
    if not hostname:
        return hostname
    parts = hostname.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return hostname
    now = time.time()
    if hostname in _DNS_CACHE and now < _DNS_CACHE[hostname][1]:
        return _DNS_CACHE[hostname][0]

    ip = _dns_query_sync(hostname, CLOUDFLARE_PRIMARY_DNS) or _dns_query_sync(hostname, CLOUDFLARE_SECONDARY_DNS)
    if not ip:
        try:
            ip = socket.gethostbyname(hostname)
        except Exception:
            ip = hostname
    if ip:
        _DNS_CACHE[hostname] = (ip, now + DNS_CACHE_TTL)
    return ip


async def resolve_host_cloudflare(hostname):
    if not hostname:
        return hostname
    parts = hostname.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return hostname
    now = time.time()
    if hostname in _DNS_CACHE and now < _DNS_CACHE[hostname][1]:
        return _DNS_CACHE[hostname][0]

    loop = asyncio.get_running_loop()
    ip = await loop.run_in_executor(None, _dns_query_sync, hostname, CLOUDFLARE_PRIMARY_DNS)
    if not ip:
        ip = await loop.run_in_executor(None, _dns_query_sync, hostname, CLOUDFLARE_SECONDARY_DNS)
    if not ip:
        try:
            inf = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
            if inf:
                ip = inf[0][4][0]
        except Exception:
            ip = hostname
    if ip:
        _DNS_CACHE[hostname] = (ip, now + DNS_CACHE_TTL)
    return ip or hostname


def post_resolved(url, data=None, headers=None, timeout=20, verify=False):
    p = urlparse(url)
    host = p.netloc
    ip = resolve_host_sync(host)
    real_url = url.replace(f"://{host}", f"://{ip}")
    hdrs = dict(headers or {})
    if "Host" not in hdrs:
        hdrs["Host"] = host
    return requests.post(real_url, data=data, headers=hdrs, timeout=timeout, verify=verify)


# ═════════════════════════ LOGIN & GUILD JOIN ═════════════════════════
def oauth(uid, password):
    r = requests.post(
        OAUTH_URL,
        data={
            "uid": uid,
            "password": password,
            "response_type": "token",
            "client_type": "2",
            "client_secret": CLIENT_SECRET,
            "client_id": APP_ID,
        },
        headers={
            "User-Agent": "GarenaMSDK/4.0.19P9(SM-M526B ;Android 13;en;IN;)",
            "Content-Type": "application/x-www-form-urlencoded",
            "Connection": "close",
        },
        timeout=20,
        verify=False,
    )
    if r.status_code != 200:
        raise RuntimeError(f"OAuth HTTP {r.status_code}: {r.text[:160]}")
    j = r.json()
    oid, at = j.get("open_id"), j.get("access_token")
    if not oid or not at:
        raise RuntimeError(f"OAuth missing fields: {j}")
    return oid, at


def inspect_token(access_token):
    tok = (access_token or "").replace("\n", "").replace("\r", "").strip().strip('"').strip("'")
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()
    if "access_token=" in tok:
        parsed = urlparse(tok)
        qs = parse_qs(parsed.query or parsed.fragment or tok)
        if qs.get("access_token"):
            tok = qs["access_token"][0].strip()
    tok = tok.replace(" ", "")
    r = requests.get(
        f"https://100067.connect.garena.com/oauth/token/inspect?token={tok}",
        headers={
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "close",
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "100067.connect.garena.com",
            "User-Agent": "GarenaMSDK/4.0.19P4(G011A ;Android 9;en;US;)",
        },
        timeout=20,
        verify=False,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Token inspect HTTP {r.status_code}")
    j = r.json()
    if "error" in j or not j.get("open_id"):
        raise RuntimeError(f"Token invalid: {j}")
    return j["open_id"], tok, str(j.get("platform", 4))


def _build_login_proto(open_id, token_val, platform="4", region="IND", client_version="2.133.9", client_ip=None):
    ip_str = client_ip or ".".join(str(random.randint(1, 254)) for _ in range(4))
    return serialize({
        3: time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        4: "free fire",
        5: 1,
        7: str(client_version),
        8: "Android OS 16 / API-36 (BP2A.250605.015/V.R4T3.11f6bd7_e90d13_e8a4fa)",
        9: "Handheld",
        10: "IND airtel",
        11: "CarrierDataNetwork",
        12: 2460,
        13: 1080,
        14: "408",
        15: "ARM64 FP ASIMD AES | 2208 | 8",
        16: 5407,
        17: "Adreno (TM) 613",
        18: "OpenGL ES 3.2 V@0615.91 (GIT@a63fb93269, I16b8254a37, 1724729505) (Date:08/27/24)",
        19: "Google|0626e27a-ac2a-4bf3-8750-f0ebb42917d4",
        20: str(ip_str),
        21: "en",
        22: str(open_id),
        23: str(platform),
        24: "Handheld",
        25: "OnePlus CPH2649",
        26: str(region),
        29: str(token_val),
        30: 1,
        41: "IND airtel",
        42: "4G",
        57: "1ac4b80ecf0478a44203bf8fac6120f5",
        60: 110509, 61: 73232, 62: 4302, 64: 73360, 65: 110509, 66: 73360, 67: 110509,
        73: 1,
        74: "/data/app/~~SjMOD8hIWfhTKxaJ6gianw==/com.dts.freefiremax-oFFHtOFIiXi5JBZyoZnfiA==/lib/arm64",
        76: 2,
        77: "38f4751a330688ab124c2c804cec90a5|/data/app/~~SjMOD8hIWfhTKxaJ6gianw==/com.dts.freefiremax-oFFHtOFIiXi5JBZyoZnfiA==/base.apk",
        78: 2, 79: 2, 81: "64", 83: "2019118527", 85: 3,
        86: "OpenGLES3", 87: 4095,
        88: int(platform) if str(platform).isdigit() else 4,
        92: 9722, 93: "android_max",
        94: "KqsHT519uyKFe/8KekFkwQLbD4DO4nI3V18X0Wn2STnKVHCHfHJjlCDrjHcO5YdvgHwAnxri94V9KeSsOUemXJmRSA+AnKfbqvf/s7WiaYqB9sV0",
        95: 111207,
        96: '{"cur_rate":[90,49,60,30],"support_etc2":true}',
        97: 1, 98: 1, 99: str(platform), 100: str(platform), 102: "",
        104: 8330, 105: 1,
        106: "https://dl-tata.freefireind.in/live/ABHotUpdates/|https://core-tata.freefireind.in/live/ABHotUpdates/|ac2c27398b1d5d95a4b6b6e04fa94066",
        107: "1.1686d744a88f55b9",
    })


def _extract_major_any(raw):
    cands = []
    dec = aes_decrypt(raw)
    if dec:
        cands.append(dec)
    for off in (0, 64, 48, 32, 80, 16, 128, 8, 96, 60, 56, 40, 24, 20, 12, 4):
        if off < len(raw):
            cands.append(raw[off:])
            d2 = aes_decrypt(raw[off:])
            if d2:
                cands.append(d2)
    for off in range(0, min(128, len(raw))):
        cands.append(raw[off:])

    ban_reason = None
    for buf in cands:
        try:
            root = parse_proto(buf)
        except Exception:
            continue
        if not root:
            continue

        f13 = first(root, 13)
        if isinstance(f13, (bytes, bytearray)) and len(f13) > 2:
            try:
                sub = parse_proto(bytes(f13))
                reason_b = first(sub, 4)
                if isinstance(reason_b, (bytes, bytearray)):
                    txt = reason_b.decode("utf-8", "ignore").strip()
                    if txt:
                        ban_reason = txt
            except Exception:
                pass

        jwt_v = first(root, 8)
        if isinstance(jwt_v, bytes):
            jwt_v = jwt_v.decode("utf-8", "ignore")
        if not (isinstance(jwt_v, str) and jwt_v.count(".") == 2 and len(jwt_v) > 100):
            continue

        def _b(f):
            v = first(root, f)
            if isinstance(v, (bytes, bytearray)) and len(v) == 16:
                return bytes(v)
            if isinstance(v, str) and len(v) == 32:
                try:
                    return bytes.fromhex(v)
                except Exception:
                    pass
            return None

        key, iv = _b(22), _b(23)
        if not key or not iv:
            continue
        acc_id = to_int(first(root, 1))
        srv_t = to_int(first(root, 21), int(time.time()))
        srv_url = to_str(first(root, 10), "")
        return jwt_v, key, iv, acc_id, srv_t, srv_url

    if ban_reason:
        raise RuntimeError(f"ACCOUNT BANNED / SUSPENDED: {ban_reason}")
    return None


def smart_major_login(open_id, access_token, platform="4", region="IND", cv_hint="2.133.9", client_ip=None):
    urls = [MAJOR_LOGIN_URL, "https://loginbp.ggpolarbear.com/MajorLogin"]
    cvs = []
    for c in (cv_hint, "2.133.9", "2.133.21"):
        if c and c not in cvs:
            cvs.append(c)

    last_debug = ""
    for url in urls:
        host = urlparse(url).netloc
        for cv in cvs:
            body = aes_encrypt(_build_login_proto(open_id, access_token, platform, region, cv, client_ip))
            r = post_resolved(
                url,
                data=body,
                headers={
                    "Host": host,
                    "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
                    "Accept": "*/*",
                    "Accept-Encoding": "deflate, gzip",
                    "X-GA-SV": str(int(time.time())),
                    "Authorization": "Bearer ",
                    "X-GA": "v1 1",
                    "ReleaseVersion": RELEASE_VERSION,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Unity-Version": "2018.4.12f1",
                    "Content-Length": str(len(body)),
                },
                timeout=20,
                verify=False,
            )
            if r.status_code == 200 and r.content:
                res = _extract_major_any(r.content)
                if res:
                    return res
                txt = "".join(chr(b) if 32 <= b < 127 else "" for b in r.content)[:80]
                last_debug = f"{host} cv={cv} len={len(r.content)} txt=[{txt}]"
    raise RuntimeError(f"MajorLogin failed: {last_debug or 'no JWT/key/iv'}")


def _extract_getlogin_online(raw):
    cands = []
    dec = aes_decrypt(raw)
    if dec:
        cands.append(dec)
    for off in range(0, min(129, len(raw))):
        cands.append(raw[off:])
    for buf in cands:
        try:
            root = parse_proto(buf)
        except Exception:
            continue
        if not root:
            continue
        online = to_str(first(root, 14), "")
        clan_id = to_int(first(root, 20), 0)
        if online and ":" in online:
            return {
                "online": online,
                "region": to_str(first(root, 3), REGION).upper(),
                "clan_id": clan_id,
                "chat": to_str(first(root, 32), ""),
            }
    return None


def smart_get_login_data(
    jwt, open_id, access_token="", platform="4", region="IND", server_url="", cv_hint="2.133.9", client_ip=None
):
    base_urls = []
    for u in (
        server_url,
        f"https://{GETLOGIN_HOST}",
        "https://client.ind.freefiremobile.com",
        "https://clientbp.ppmainecoonghj.com",
    ):
        if u and u.rstrip("/") not in base_urls:
            base_urls.append(u.rstrip("/"))
    tok_list = []
    for t in (access_token, jwt):
        if t and t not in tok_list:
            tok_list.append(t)

    for base in base_urls:
        host = urlparse(base).netloc
        url = f"{base}/GetLoginData"
        for tok29 in tok_list:
            body = aes_encrypt(_build_login_proto(open_id, tok29, platform, region, cv_hint, client_ip))
            try:
                r = post_resolved(
                    url,
                    data=body,
                    headers={
                        "Host": host,
                        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
                        "Accept": "*/*",
                        "Accept-Encoding": "deflate, gzip",
                        "X-GA-SV": str(int(time.time())),
                        "Authorization": f"Bearer {jwt}",
                        "X-GA": "v1 1",
                        "ReleaseVersion": RELEASE_VERSION,
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-Unity-Version": "2018.4.12f1",
                        "Content-Length": str(len(body)),
                    },
                    timeout=15,
                    verify=False,
                )
                if r.status_code == 200 and r.content:
                    gd = _extract_getlogin_online(r.content)
                    if gd:
                        return gd
            except Exception:
                pass
    return None


def _jwt_payload(jwt_str):
    try:
        parts = str(jwt_str).split(".")
        if len(parts) != 3:
            return {}
        p = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def login_bot_sync(acc, existing_sess=None, force_fresh=False):
    name = acc.get("name") or acc.get("nickname") or acc.get("uid") or acc.get("login_uid") or "Bot"

    if not force_fresh and existing_sess:
        jp = _jwt_payload(existing_sess.get("jwt", ""))
        exp = int(jp.get("exp") or 0)
        if exp == 0 or (exp - time.time()) > 120:
            sess = dict(existing_sess)
            sess["kts"] = int(time.time())
            sess["fresh"] = False
            return sess

    uid = acc.get("uid") or acc.get("login_uid")
    pw = acc.get("password") or acc.get("pass")
    if not uid or not pw:
        raise RuntimeError(f"[{name}] accounts.json me uid/password missing")

    oid, at = oauth(uid, pw)
    _, at, plat = inspect_token(at)
    target_reg = str(acc.get("region") or acc.get("server") or REGION).upper()
    jwt_v, key, iv, acc_id, kts, srv_url = smart_major_login(oid, at, plat, target_reg, "2.133.9", None)

    jp2 = _jwt_payload(jwt_v)
    reg = (jp2.get("lock_region") or jp2.get("noti_region") or target_reg).upper()
    cv_str = str(jp2.get("client_version") or "2.133.9")
    gd = smart_get_login_data(jwt_v, oid, at, plat, reg, srv_url, cv_str, None)
    if not gd or not gd.get("online"):
        raise RuntimeError(f"[{name}] online_ip_port nahi mila")

    return {
        "name": name,
        "jwt": jwt_v,
        "key": key,
        "iv": iv,
        "acc_id": int(acc_id),
        "kts": int(kts),
        "online": gd["online"],
        "region": reg,
        "cv": cv_str,
        "open_id": oid,
        "access_token": at,
        "server_url": srv_url,
        "fresh": True,
        "clan_id": gd.get("clan_id", 0),
    }


def _clan_host(region):
    r = str(region).upper()
    if r == "IND":
        return "clientbp.freefireind.in"
    if r in ("BR", "US", "NA", "SAC"):
        return "client.us.freefiremobile.com"
    return "clientbp.ggpolarbear.com"


def join_guild(jwt_token, guild_id, region=REGION):
    host = _clan_host(region)
    url = f"https://{host}/RequestJoinClan"
    enc = aes_encrypt(serialize({1: int(guild_id)}))
    r = post_resolved(
        url,
        data=enc,
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "X-Unity-Version": "2018.4.11f1",
            "X-GA": "v1 1",
            "ReleaseVersion": RELEASE_VERSION,
            "Content-Type": "application/octet-stream",
            "Host": host,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
        },
        timeout=15,
        verify=False,
    )
    return r.status_code == 200, r.status_code, host


# ═════════════════════════ SQUAD BR PACKETS ═════════════════════════
_C_S1 = "0200044B122D3B02010000000000000000000000000000E3172CED00"
_C_14_1 = b"\x32" + bytes([len(_C_S1)]) + _C_S1.encode() + b"\x30\x30" * 3
_C_14_3 = bytes.fromhex(
    "7e585c521207074f5d030c5c545104570455070f0a555a085201095701025b560601545606010f07100802487d5c4043481e07"
    "1f081a1709034c0757787500566b45627e7a6548407565577a700e7708627a047e7e041005447b5d655070547e437c607f7656"
    "5675704e5877525b5a5b515662410a105f5c5a4c1e637f1c7147430b4f6c557778605b67416b50635850500056575e005a7606"
    "057005067e0c15060045455e56447a665a6672694b5046524b057b79427c715560577b575f0a120e4b470e631b536262186b0f"
    "73406359475343795c481d7918564b47790c13054d1d714b5d75451740515f7e586d414e40595a03000e654e0741507a091b08"
    "4c6668777b1c4c676517505d6e7a4c4e7d7c791f5269057f42440207041005004d7e5c5977037d4a631f560255026b7a647e6b"
    "0007015503746353500e1a0048701a0153626506774f18065e0558610457421856007313771b60010b140745554d7f50477d68"
    "770662600646696876026242541c4a49037f74750a1204074f487402135059457f517b415266604a434506064e79004c5a7b4f"
    "700d14044a58454a5a7c5e5f5b764354425e1267735041537d6041180a6d694305"
)
_C_14_7 = bytes.fromhex("1d02474b420204030506171d677c716374687d5f557051565753")
_C_14_11 = b"\x03" + (
    "bbSQ69i8H3F5AdV2KyoVlWj2cgPtsEL3Pi8QcWkc/NJiGxtxXsmeZDDwFPKPbvd56sULGeaD3qha9S6hKIkIb3oXVAAFErlI76uU2p2AqNdJyaucxz1ba5ecHPCBohQR6E5Y1UzpsgpxutURpI8pbqY0tCpeL1bOT+F5UaysgFmTeoIalHxxEYPQx7OVJHElCV5el0vpIs98JSA3nB8FWin6ckz5Fd0sG4n2g0B7OUOvcreqilCT737xI24wPxLdidSr1AJ2MK0/m3ugV1iqzPXhttKyvfmMNVY8DoXBBUcYt3vpK7mgbnietrOqJIbJC3O4ygX6IWl/hkp+ngsjfeOZSornpzFnxM/kkqZgZjVOweXe61FRU6JsGSeTeY63kqA/mbCPfdwodSYJE2hXfVJofxRG1644gFPYRkCPUjJazd2l9WAh9kd86kYbjphbxwz55ciWvZ3sDJR11W96ezyxJPhJPD8FQoQVmno6BP4e7QI1xe+h35bkS1epZq6K6xUP5YtHFvqbhARMNQKUYYzXQWRFMNL1JgAeMJNO0+eCPL57+RXzRKtlgM/rVsIWhNw8Oo9SdP+7wsGrMK+HIOJaF1apAXNRQ9mCqxI="
).encode()


def _vi(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _one(field, v):
    if isinstance(v, dict):
        body = _pb(v)
        return _vi((field << 3) | 2) + _vi(len(body)) + body
    if isinstance(v, int):
        return _vi(field << 3) + _vi(v)
    if isinstance(v, (str, bytes)):
        b = v.encode() if isinstance(v, str) else v
        return _vi((field << 3) | 2) + _vi(len(b)) + b
    raise TypeError(type(v))


def _pb(fields):
    out = bytearray()
    for f, v in fields.items():
        if isinstance(v, list):
            for it in v:
                out += _one(f, it)
        else:
            out += _one(f, v)
    return bytes(out)


def _rbyte(region):
    r = str(region or REGION).upper()
    if r == "BD":
        return 0x19
    if r == "IND":
        return 0x14
    return 0x15


def _frame(ptype, region, proto, key, iv):
    enc = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(proto, 16))
    return bytes([ptype, _rbyte(region)]) + len(enc).to_bytes(4, "big") + enc


def _idc_num(reg, pings=(75, 123)):
    return [{1: "IDC1", 2: pings[0], 3: reg}, {1: "IDC2", 2: pings[1], 3: reg}]


def br_create_squad_pkt(region, key, iv, squad_players=4):
    reg = str(region or REGION).upper()
    f = {
        1: 1,
        2: {
            2: b"\x01",
            3: 15,
            4: squad_players - 1,
            5: "en",
            8: _idc_num(reg),
            9: 1,
            10: bytes.fromhex("01090a0b12191a202729"),
            11: 1,
            13: 1,
            14: {
                1: _C_14_1, 2: 40, 3: _C_14_3, 4: "w\\Z]", 6: 12,
                7: _C_14_7, 8: SQUAD_CLIENT_VER, 9: 6, 10: 2, 11: _C_14_11,
            },
            19: 329,
            21: bytes.fromhex("374f521955"),
            24: [
                {1: 3, 2: 360}, {1: 4, 2: 391}, {1: 5, 2: 156}, {1: 29, 2: 204},
                {1: 22, 2: 118}, {1: 14, 2: 139}, {1: 21},
            ],
            30: (
                "https://dl-ind-production.freefiremobile.com/"
                "2832022628717E276054_7104117490_107_1767713981_0"
            ),
        },
    }
    return _frame(0x05, reg, _pb(f), key, iv)


def br_join_team_pkt(code, region, key, iv):
    reg = str(region or REGION).upper()
    field9 = {
        1: _C_14_1, 2: 40, 3: _C_14_3, 4: "w\\Z]", 6: 12,
        7: _C_14_7, 8: SQUAD_CLIENT_VER, 9: 6, 10: 2, 11: _C_14_11,
    }
    f = {
        1: 4,
        2: {
            4: bytes.fromhex("01090a0b121619201d2729"),
            5: str(code),
            6: 6,
            8: 1,
            9: field9,
            11: _idc_num(reg, (99, 136)),
            13: "en",
            16: bytes.fromhex("374f521955"),
            20: [
                {1: 3, 2: 360}, {1: 4, 2: 391}, {1: 5, 2: 156}, {1: 29, 2: 204},
                {1: 22, 2: 118}, {1: 14, 2: 139}, {1: 21},
            ],
            27: {1: 2, 2: 4},
        },
    }
    return _frame(0x05, reg, _pb(f), key, iv)


def op31_pkt(group_id, region, key, iv):
    reg = str(region or REGION).upper()
    return _frame(0x05, reg, _pb({1: 31, 2: {1: int(group_id)}}), key, iv)


def leave_team_pkt(uid, region, key, iv):
    reg = str(region or REGION).upper()
    return _frame(0x05, reg, _pb({1: 7, 2: {1: int(uid)}}), key, iv)


def br_start_squad_pkt(uid, region, key, iv):
    reg = str(region or REGION).upper()
    return _frame(0x05, reg, _pb({1: 9, 2: {1: int(uid), 7: _idc_num(reg)}}), key, iv)


def keep_alive_pkt(region):
    return bytes([0x02, _rbyte(region)])


def build_tcp_ping(session_key, session_iv):
    body = aes_encrypt(b"\x08\x20", session_key, session_iv)
    return bytes.fromhex("0e14") + len(body).to_bytes(4, "big") + body


def decode_tcp_payload(payload, key, iv):
    d = parse_proto(payload)
    if d and (first(d, 4) is not None or first(d, 5) is not None or first(d, 1) is not None):
        return d
    dec = aes_decrypt(payload, key, iv)
    if dec:
        d2 = parse_proto(dec)
        if d2:
            return d2
    return {}


# ═════════════════════════ 0300 PARSER ═════════════════════════
_IP_RE = re.compile(rb"((?:\d{1,3}\.){3}\d{1,3}:\d{1,5})")
_HEX32_RE = re.compile(rb"([0-9a-fA-F]{32})")
_JWT_RE = re.compile(rb"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
_HEX32 = set("0123456789abcdefABCDEF")


def _is_hex32(s):
    return isinstance(s, str) and len(s) == 32 and all(c in _HEX32 for c in s)


def _is_mask(s):
    return isinstance(s, str) and len(s) == 5 and s.isdigit() and sorted(s) == sorted("01234")


def _walk_find(obj, pred):
    if isinstance(obj, dict):
        for v in obj.values():
            r = _walk_find(v, pred)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for x in obj:
            r = _walk_find(x, pred)
            if r is not None:
                return r
    else:
        if pred(obj):
            return obj
    return None


def _scan_ips(payload):
    out = []
    for m in _IP_RE.finditer(payload):
        s = m.group(1).decode("ascii", "ignore")
        try:
            h, p = s.rsplit(":", 1)
            if all(0 <= int(x) <= 255 for x in h.split(".")) and 0 < int(p) < 65536 and s not in out:
                out.append(s)
        except Exception:
            pass
    return out


def extract_448_fields(payload):
    out = {}

    def walk(d, parent):
        if not isinstance(d, dict):
            return None
        if _is_hex32(to_str(first(d, 3))):
            return d, parent
        for v in d.values():
            for item in (v if isinstance(v, list) else [v]):
                r_ = walk(item, d)
                if r_:
                    return r_
        return None

    for off in range(0, min(65, len(payload))):
        try:
            outer = parse_proto(payload[off:])
        except Exception:
            continue
        if not outer:
            continue
        found = walk(outer, None)
        if not found:
            continue
        sub, parent = found
        for key_, f_ in (("a0", 6), ("mode_id", 7), ("slot_byte", 8)):
            v_ = to_int(first(sub, f_), -1)
            if v_ >= 0:
                out[key_] = v_
        u_ = to_str(first(sub, 39))
        if u_.startswith("http"):
            out["hot_update_url"] = u_
        x_ = to_int(first(parent or outer, 2), -1)
        if x_ >= 0:
            out["room_x"] = x_
        if len(out) >= 4:
            return out
    return out


def parse_0300(payload: bytes, fallback_account_id: int = 0):
    account_id = match_id = 0
    server = udp_key = token = mask = ""
    extra448 = {}

    def _take(d):
        nonlocal match_id, server, udp_key, token, mask
        if not match_id:
            match_id = to_int(first(d, 1))
        s = to_str(first(d, 2))
        if s and ":" in s and not server:
            server = s
        k = to_str(first(d, 3))
        if _is_hex32(k) and not udp_key:
            udp_key = k
        t = to_str(first(d, 4))
        if isinstance(t, str) and t.count(".") == 2 and len(t) > 50 and not token:
            token = t
        m = to_str(first(d, 42))
        if _is_mask(m) and not mask:
            mask = m

    for off in range(0, min(65, len(payload))):
        try:
            outer = parse_proto(payload[off:])
        except Exception:
            continue
        if not outer:
            continue
        oid = to_int(first(outer, 1))
        if oid and not account_id:
            account_id = oid
        sub = first(outer, 5)
        if isinstance(sub, dict):
            _take(sub)
            if not extra448:
                for key_, f_ in (("a0", 6), ("mode_id", 7), ("slot_byte", 8)):
                    v_ = to_int(first(sub, f_), -1)
                    if v_ >= 0:
                        extra448[key_] = v_
                u_ = to_str(first(sub, 39))
                if u_.startswith("http"):
                    extra448["hot_update_url"] = u_
                x_ = to_int(first(outer, 2), -1)
                if x_ >= 0:
                    extra448["room_x"] = x_

        if not (server and udp_key and token):
            if not udp_key:
                udp_key = _walk_find(outer, lambda v: _is_hex32(v) if isinstance(v, str) else False) or ""
            if not mask:
                mask = _walk_find(outer, lambda v: _is_mask(v) if isinstance(v, str) else False) or ""
            if not server:
                server = _walk_find(
                    outer,
                    lambda v: isinstance(v, str) and ":" in v and "." in v.split(":")[0] and v.split(":")[-1].isdigit(),
                ) or ""
            if not token:
                token = _walk_find(
                    outer,
                    lambda v: isinstance(v, str) and v.count(".") == 2 and len(v) > 50 and v.startswith("eyJ"),
                ) or ""
        if server and udp_key and token:
            break

    if not (server and udp_key and token):
        cands = _scan_ips(payload)
        mk = _HEX32_RE.search(payload)
        mj = _JWT_RE.search(payload)
        if cands and mk and mj:
            server = server or cands[0]
            udp_key = udp_key or mk.group(1).decode()
            token = token or mj.group(0).decode()

    if not (server and udp_key and token):
        return None
    if not _is_mask(mask):
        mask = ""

    if not account_id:
        try:
            p = token.split(".")[1]
            p += "=" * (-len(p) % 4)
            c = json.loads(base64.urlsafe_b64decode(p))
            for k in ("account_id", "uid", "sub"):
                if c.get(k):
                    account_id = int(c[k])
                    break
        except Exception:
            pass

    account_id = account_id or fallback_account_id
    match_id = match_id or account_id
    return {
        "account_id": account_id,
        "match_id": match_id,
        "token": token,
        "server": server,
        "udp_key": udp_key,
        "mask": mask,
        "extra448": extra448 or extract_448_fields(payload),
    }


def extract_match_info(op, payload, cmd, acc_id, key, iv):
    is_0300 = op.startswith(b"\x03") and len(payload) >= 300
    is_11_match = op.startswith(b"\x11") and cmd == 3 and len(payload) >= 200
    if not (is_0300 or is_11_match):
        return None
    m = parse_0300(payload, fallback_account_id=int(acc_id))
    if not m:
        dec = aes_decrypt(payload, key, iv)
        if dec:
            m = parse_0300(dec, fallback_account_id=int(acc_id))
    if m and m.get("server") and m.get("udp_key") and m.get("token") and m.get("mask"):
        return m
    return None


# ═════════════════════════ TCP CONNECTION ═════════════════════════
class SafeFrameReader:
    def __init__(self, reader):
        self.reader = reader
        self.buf = bytearray()

    async def next_frame(self, timeout=1.5):
        dl = time.time() + timeout
        while True:
            while len(self.buf) >= 5:
                if self.buf[1] == 0x00:
                    ln = int.from_bytes(self.buf[2:5], "big")
                    if 0 <= ln <= 262144:
                        if len(self.buf) < 5 + ln:
                            break
                        op = bytes(self.buf[:2])
                        payload = bytes(self.buf[5:5 + ln])
                        del self.buf[:5 + ln]
                        return op, payload
                idx = self.buf.find(b"\x00", 2)
                if idx != -1:
                    del self.buf[:idx - 1]
                else:
                    del self.buf[:-1]
            rem = dl - time.time()
            if rem <= 0:
                return None
            try:
                chunk = await asyncio.wait_for(self.reader.read(65536), timeout=rem)
            except asyncio.TimeoutError:
                return None
            if not chunk:
                return None
            self.buf.extend(chunk)


async def safe_send(writer, data):
    if not writer or writer.is_closing():
        return False
    try:
        writer.write(data)
        await writer.drain()
        return True
    except (ConnectionResetError, BrokenPipeError, OSError):
        return False


async def _tcp_connect_and_auth(session):
    host, port = session["online"].rsplit(":", 1)
    ip = await resolve_host_cloudflare(host)
    auth_enc = aes_encrypt(session["jwt"].encode(), session["key"], session["iv"])
    rb = _rbyte(session["region"])

    for head_byte in (0x71, 0x83, 0x72):
        try:
            reader, writer = await asyncio.open_connection(ip, int(port))
        except Exception:
            continue
        fr = SafeFrameReader(reader)
        auth_pkt = (
            bytes([head_byte, rb])
            + int(session["acc_id"]).to_bytes(8, "big")
            + int(session["kts"]).to_bytes(4, "big")
            + b"\x00\x00\x00\x00"
            + len(auth_enc).to_bytes(4, "big")
            + auth_enc
        )
        if not await safe_send(writer, auth_pkt):
            try:
                writer.close()
            except Exception:
                pass
            continue
        await safe_send(writer, keep_alive_pkt(session["region"]))

        ack = await fr.next_frame(timeout=4.0)
        if not ack or ack[0].hex() in ("0b00", "0c00", "1e00"):
            try:
                writer.close()
            except Exception:
                pass
            continue

        drain_end = time.time() + 0.6
        kicked = False
        while time.time() < drain_end:
            f = await fr.next_frame(timeout=0.15)
            if not f:
                break
            if f[0].hex() in ("0b00", "0c00", "1e00"):
                kicked = True
                break
        if kicked:
            try:
                writer.close()
            except Exception:
                pass
            continue

        ok(f"TCP Connected · {session['name']} (UID {session['acc_id']})")
        return reader, writer, fr

    return None, None, None


async def connect_with_auto_refresh(acc, session):
    reader, writer, fr = await _tcp_connect_and_auth(session)
    if writer:
        return session, reader, writer, fr
    fresh_sess = await asyncio.to_thread(login_bot_sync, acc, None, True)
    reader, writer, fr = await _tcp_connect_and_auth(fresh_sess)
    if writer:
        return fresh_sess, reader, writer, fr
    raise RuntimeError(f"[{session['name']}] TCP auth fail")


async def heartbeat_loop(writer, reader, session, stop_evt):
    ka = keep_alive_pkt(session["region"])
    ping = build_tcp_ping(session["key"], session["iv"])
    while not stop_evt.is_set():
        if writer.is_closing() or reader.at_eof():
            break
        if not await safe_send(writer, ka):
            break
        await safe_send(writer, ping)
        try:
            await asyncio.wait_for(stop_evt.wait(), timeout=5.0)
            break
        except asyncio.TimeoutError:
            pass


# ═════════════════════════ UDP PACKETS & SESSION ═════════════════════════
def non_reliable(mask):
    return "".join(c for c in mask if c != "2")


def build_pkt(cmd, content, key, mask, *, reliable=True, order_id=0, send_option=1, flags=1, msg_key=None, extra=b""):
    k0 = key[0]
    if extra:
        flags |= 4
    layout = [int(c) for c in (mask if reliable else non_reliable(mask))]
    body = udp_encrypt_body(content, key) if flags & 1 else content
    values = {0: send_option, 1: cmd, 2: order_id, 3: flags, 4: len(body)}
    head = b""
    for c in layout:
        v, n = values[c], FIELD_SIZES[c]
        head += bytes([(v ^ k0) & 0xFF]) if n == 1 else struct.pack("<H", (v ^ ((k0 << 8) | k0)) & 0xFFFF)
    rest = head + bytes(b ^ k0 for b in extra) + body
    mk = msg_key if msg_key is not None else 90 + random.randrange(20)
    return bytes([mk, crc7(rest)]) + rest


def decode_header(pkt, key, mask):
    if len(pkt) < 8:
        return None
    k0 = key[0]
    v80 = ((k0 << 8) | k0) & 0xFFFF
    crc_ok = (pkt[1] & 0x7F) == crc7(pkt[2:])
    for m in (mask, non_reliable(mask)):
        off, f, valid = 2, {}, True
        for ch in m:
            c = int(ch)
            n = FIELD_SIZES[c]
            if off + n > len(pkt):
                valid = False
                break
            if n == 1:
                f[FIELD_NAMES[c]] = pkt[off] ^ k0
            else:
                f[FIELD_NAMES[c]] = ((pkt[off] | (pkt[off + 1] << 8)) ^ v80) & 0xFFFF
            off += n
        if not valid or f["flags"] > 7 or f["sendOption"] > 7:
            continue
        extra = 3 if f["flags"] & 4 else 0
        if f["length"] != len(pkt) - off - extra:
            continue
        return {"mask": m, "header_len": off, "extra": extra, "crc_ok": crc_ok, **f}
    return None


def pkt_hello(player_id, suffix, key, mask):
    data = f"{player_id}_{suffix}".encode()
    data = (data + b"\x00" * (16 - len(data))) if len(data) < 16 else data[:16]
    body = b"\x10\x00\x00\x00" + data + b"\x00" * 9
    return build_pkt(1, body, key, mask, reliable=True, order_id=0, send_option=1, flags=1, msg_key=MSG_KEY_HELLO)


def pkt_ack(acked, key, mask, style="regular"):
    body = struct.pack("<H", acked & 0xFFFF)
    mk = MSG_KEY_ACK_HELLO if style == "short" else MSG_KEY_ACK
    return build_pkt(2, body, key, mask, reliable=False, order_id=0, send_option=0, flags=1, msg_key=mk)


def token_tail_len_for(token):
    return (2 * len(token)) // 13


def pkt_auth447(token, key, mask, order_id=0):
    return build_pkt(
        447,
        zigzag_str(token[:len(token) - token_tail_len_for(token)]),
        key,
        mask,
        reliable=True,
        order_id=order_id,
        send_option=2,
        flags=1,
        msg_key=MSG_KEY_AUTH_447,
    )


def _m448_a(a0=0x1A, x=3, p=2, last=None):
    if last is None:
        last = (1 << x) & 0xFF
    return (
        bytes([a0 & 0xFF])
        + bytes(6)
        + bytes([0x01, p & 0xFF, x, 0x01, 0x01, 0x04, 0x49, 0x4E, 0x00, x, 0x03, 0x00, 0x01, 0x00, 0x00, last & 0xFF])
    )


M448_BLOCK16 = bytes.fromhex("0000010000000000010000000000")
M448_TAIL = b"\x02\x00"
_LOGIN_TICKS = (time.time_ns() // 100) + 621355968000000000 - 400_000_000


def _zz_int(n):
    return varint(((n << 1) ^ (n >> 63)) & ((1 << 64) - 1))


def pkt_auth448(
    token,
    key,
    mask,
    player_id,
    match_id,
    client_version="1.134.21",
    order_id=1,
    mode_id=73,
    msg_key=MSG_KEY_AUTH_448,
    device_id="2019121269",
    device_hash=None,
    client_ip=None,
    device_fingerprint="",
    hot_update_url="",
    slot_byte=1,
    pre=0,
    a0=0x22,
    room_x=3,
    login_ticks=None,
    a_p=1,
    a_last=4,
    head_last=1,
):
    if not str(client_version).startswith("1."):
        client_version = UDP_CLIENT_VERSION
    device_hash = device_hash or UDP_DEVICE_HASH
    client_ip = client_ip or UDP_CLIENT_IP
    login_ticks = _LOGIN_TICKS if login_ticks is None else login_ticks
    tail = token[-token_tail_len_for(token):]

    body = (
        varint(int(player_id))
        + varint(int(match_id))
        + bytes([0x01, mode_id & 0xFF])
        + varint(int(match_id))
        + _m448_a(a0, room_x, a_p, a_last)
        + zigzag_str(client_version)
        + zigzag_str(device_id)
        + bytes([0, 0, 0, pre & 0xFF, 0, 0, 0, head_last & 0xFF])
        + zigzag_str(device_fingerprint)
        + b"\x00" * 8
        + zigzag_str(hot_update_url)
        + M448_BLOCK16
        + bytes([slot_byte & 0xFF, 0x00])
        + _zz_int(int(login_ticks))
        + M448_TAIL
        + zigzag_str(client_ip)
        + zigzag_str(device_hash.lower().zfill(64))
        + zigzag_str(tail)
    )
    return build_pkt(448, body, key, mask, reliable=True, order_id=order_id, send_option=2, flags=1, msg_key=msg_key)


def pkt_ping(tick, key, mask, msg_key=None, extra=b""):
    if msg_key is None:
        msg_key = MSG_KEY_PING_POOL[random.randrange(len(MSG_KEY_PING_POOL))]
    body = varint(tick & 0xFFFFFFFF) + b"\x00\x00\x00"
    return build_pkt(3, body, key, mask, reliable=False, order_id=0, send_option=0, flags=0, msg_key=msg_key, extra=extra)


def _zz(v):
    return (v >> 1) ^ -(v & 1)


def parse_addr(s):
    if not s:
        return None, None
    s = str(s).strip()
    if ":" in s:
        h, p = s.rsplit(":", 1)
        try:
            return h, int(p)
        except ValueError:
            return h, None
    return s, None


def is_placeholder_host(h):
    if not h or h in ("localhost", "0.0.0.0", "::1", ""):
        return True
    for pref in ("127.", "10.", "192.168.", "169.254."):
        if h.startswith(pref):
            return True
    if h.startswith("172."):
        try:
            if 16 <= int(h.split(".")[1]) <= 31:
                return True
        except Exception:
            pass
    return False


def _parse_server_pos(h, pkt, key):
    if h["cmd"] != 2001:
        return None
    off = h["header_len"] + h.get("extra", 0)
    body = pkt[off:off + h["length"]]
    if h["flags"] & 1:
        if key is None:
            return None
        body = _udp_unpad(udp_decrypt_body(body, key)) or b""
    if len(body) < 9 or body[0] != 0 or not 1 <= body[1] <= 8 or not 1 <= body[2] <= 32:
        return None
    try:
        x, i = read_varint(body, 3)
        y, i = read_varint(body, i)
        z, i = read_varint(body, i)
    except IndexError:
        return None
    return {"x": _zz(x), "y": _zz(y), "z": _zz(z), "slot": body[2], "team": body[1]}


async def udp_session(
    host,
    port,
    key_hex,
    token,
    player_id,
    match_id,
    mask,
    *,
    extra448=None,
    slot_id=1,
    on_join=None,
    duration=0.0,
    idle_timeout=35.0,
    match_label="",
    verbose=False,
):
    try:
        key = bytes.fromhex(key_hex)
    except Exception:
        err(f"[{match_label}] bad udp key")
        return False
    if len(key) != 16 or token.count(".") != 2:
        err(f"[{match_label}] invalid udp key or JWT")
        return False

    resolved = await resolve_host_cloudflare(host)
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    except OSError:
        pass
    sock.setblocking(False)
    try:
        sock.bind(("0.0.0.0", 0))
    except OSError:
        pass
    try:
        sock.connect((resolved, port))
    except OSError as e:
        err(f"[{match_label}] UDP connect failed: {e}")
        sock.close()
        return False

    st = {
        "spawn": {},
        "spawn_known": asyncio.Event(),
        "recv_q": asyncio.Queue(maxsize=4000),
        "stop_recv": asyncio.Event(),
        "stop_bg": asyncio.Event(),
        "ack_next": 0,
        "ack_sent": 0,
        "recv_orders": set(),
        "t0": time.time(),
        "joined": asyncio.Event(),
        "n100": 0,
        "last_rx": time.time(),
        "t_join": 0.0,
    }

    suffix = str(10000 + secrets.randbelow(90000))
    hello = pkt_hello(str(player_id), suffix, key, mask)
    a447 = pkt_auth447(token, key, mask, order_id=0)
    kw = dict(PRESET_73)
    kw.update({k: v for k, v in (extra448 or {}).items() if k != "hot_update_url"})
    a448 = pkt_auth448(token, key, mask, player_id, match_id, UDP_CLIENT_VERSION, order_id=1, **kw)

    movement = None

    async def send(pkt):
        try:
            await loop.sock_sendall(sock, pkt)
            return True
        except OSError:
            return False

    def note_order(h):
        oid = h.get("orderId")
        if oid is None or h["cmd"] == 2:
            return
        st["recv_orders"].add(oid)
        while st["ack_next"] in st["recv_orders"]:
            st["ack_next"] += 1

    def _mark_joined():
        nonlocal movement
        if st["joined"].is_set():
            return
        st["joined"].set()
        st["t_join"] = time.time()
        ok(f"IN-GAME JOINED · {match_label} (UID {player_id})")
        if movement is None:
            movement = MovementController(
                account_id=player_id,
                udp_sock=sock,
                udp_server=(resolved, port),
                udp_key_bytes=key,
                match_code=mask,
                slot_id=slot_id,
                tick_hz=10.0,
                label=match_label,
            )
            if st["spawn_known"].is_set() and st.get("spawn"):
                sp = st["spawn"]
                movement.set_spawn(sp["x"], sp["y"], sp["z"], sp.get("slot", 1), sp.get("team", 1))
            movement.start()
        if on_join:
            try:
                on_join()
            except Exception:
                pass

    async def handle(pkt, label=""):
        h = decode_header(pkt, key, mask)
        if not h:
            return None
        cmd, oid = h["cmd"], h.get("orderId")
        st["last_rx"] = time.time()
        if verbose:
            name_c = CMD_NAMES.get(cmd, f"cmd_{cmd}")
            print(
                f"  [{match_label}] < [{time.time()-st['t0']:5.1f}s] cmd={cmd}({name_c}) len={h['length']} {label}",
                flush=True,
            )

        dup = oid is not None and cmd != 2 and oid in st["recv_orders"]
        note_order(h)
        if oid is not None and cmd != 2 and (h.get("sendOption") == 2 or (h["flags"] & 1)):
            if st["ack_next"] > st["ack_sent"] or dup:
                st["ack_sent"] = st["ack_next"]
                await send(pkt_ack(st["ack_next"], key, mask, "regular"))

        if cmd == 100:
            st["n100"] += 1
            if st["n100"] == 3:
                warn(f"[{match_label}] server repeating cmd 100 (448 check)")
        elif cmd in (99, 176, 849):
            _mark_joined()
        elif cmd == 1:
            await send(pkt_ack(1, key, mask, "short"))
        elif cmd == 2001:
            sp = _parse_server_pos(h, pkt, key)
            if sp and not st["spawn_known"].is_set():
                st["spawn"] = dict(sp)
                st["spawn_known"].set()
                _mark_joined()
            if movement:
                off_ = h["header_len"] + h.get("extra", 0)
                b_raw = pkt[off_:off_ + h["length"]]
                dec_b = _udp_unpad(udp_decrypt_body(b_raw, key)) if (h["flags"] & 1) else b_raw
                if dec_b:
                    movement.handle_server_frame({"cmd": 2001, "content": dec_b})
        return cmd

    async def recv_loop():
        while not st["stop_recv"].is_set():
            try:
                pkt, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 65536), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except OSError:
                break
            try:
                st["recv_q"].put_nowait(pkt)
            except asyncio.QueueFull:
                pass

    async def consume():
        while not st["stop_bg"].is_set():
            try:
                pkt = await asyncio.wait_for(st["recv_q"].get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            try:
                await handle(pkt, label="(bg)")
            except Exception:
                pass

    async def handshake(pkt, expect, name, timeout=HANDSHAKE_STEP_DELAY):
        start = time.time()
        await send(pkt)
        while True:
            rem = timeout - (time.time() - start)
            if rem <= 0:
                break
            try:
                got = await asyncio.wait_for(st["recv_q"].get(), timeout=min(0.3, rem))
            except asyncio.TimeoutError:
                continue
            cmd = await handle(got, label=f"({name})")
            if cmd in expect:
                return True
        warn(f"[{match_label}] UDP handshake step {name} no reply")
        return False

    recv_task = asyncio.create_task(recv_loop())
    consume_task = ping_task = None

    try:
        if not await handshake(hello, {1, 2, 11}, "HELLO"):
            return False
        if not await handshake(a447, {2}, "AUTH#1"):
            return False
        await handshake(a448, {2, 99, 100, 176, 849, 2001}, "AUTH#2")

        consume_task = asyncio.create_task(consume())

        try:
            await asyncio.wait_for(st["joined"].wait(), timeout=18.0)
        except asyncio.TimeoutError:
            warn(f"[{match_label}] no join event in 18s — keeping UDP alive")
            _mark_joined()

        async def ping_loop():
            ping_t0 = time.time()
            while not st["stop_bg"].is_set():
                el_ms = int((time.time() - ping_t0) * 1000)
                tick = (PING_TICK_BASE - 2 * el_ms) & 0xFFFFFFFF
                if not await send(pkt_ping(tick, key, mask)):
                    break
                await asyncio.sleep(PING_INTERVAL)

        if PING_ENABLED:
            ping_task = asyncio.create_task(ping_loop())

        while not st["stop_bg"].is_set():
            await asyncio.sleep(0.5)
            if not st["joined"].is_set():
                continue
            if duration > 0:
                if time.time() - st["t_join"] >= duration:
                    info(f"[{match_label}] match duration {duration:.0f}s complete")
                    break
            else:
                if time.time() - st["last_rx"] > idle_timeout:
                    info(f"[{match_label}] match ended (idle {idle_timeout:.0f}s)")
                    break

        return st["joined"].is_set()
    finally:
        st["stop_bg"].set()
        st["stop_recv"].set()
        if movement:
            try:
                await movement.stop()
            except Exception:
                pass
        for t in (recv_task, consume_task, ping_task):
            if t:
                try:
                    t.cancel()
                except Exception:
                    pass
        try:
            sock.close()
        except Exception:
            pass
