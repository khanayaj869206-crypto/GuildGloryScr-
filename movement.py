# -*- coding: utf-8 -*-
"""
Clean Anti-AFK Movement Controller (Calibrated from Live OB55 PCAP Captures)
Sends 10 Hz CMD 2001 movement packets (sprint, jump, landing, idle)
synchronized to S2C spawn coordinates.
"""

import asyncio
import math
import random
import struct
import time
from typing import Any, Dict, Optional, Tuple

CRC7_TABLE = bytes([
    0,9,18,27,36,45,54,63,72,65,90,83,108,101,126,119,
    25,16,11,2,61,52,47,38,81,88,67,74,117,124,103,110,
    50,59,32,41,22,31,4,13,122,115,104,97,94,87,76,69,
    43,34,57,48,15,6,29,20,99,106,113,120,71,78,85,92,
    100,109,118,127,64,73,82,91,44,37,62,55,8,1,26,19,
    125,116,111,102,89,80,75,66,53,60,39,46,17,24,3,10,
    86,95,68,77,114,123,96,105,30,23,12,5,58,51,40,33,
    79,70,93,84,107,98,121,112,7,14,21,28,35,42,49,56,
    65,72,83,90,101,108,119,126,9,0,27,18,45,36,63,54,
    88,81,74,67,124,117,110,103,16,25,2,11,52,61,38,47,
    115,122,97,104,87,94,69,76,59,50,41,32,31,22,13,4,
    106,99,120,113,78,71,92,85,34,43,48,57,6,15,20,29,
    37,44,55,62,1,8,19,26,109,100,127,118,73,64,91,82,
    60,53,46,39,24,17,10,3,116,125,102,111,80,89,66,75,
    23,30,5,12,51,58,33,40,95,86,77,68,123,114,105,96,
    14,7,28,21,42,35,56,49,70,79,84,93,98,107,112,121,
])

FIELD_SIZES = {0: 1, 1: 2, 2: 2, 3: 1, 4: 2}


def crc7_buf(data: bytes) -> int:
    c = 0
    for b in data:
        c = CRC7_TABLE[((2 * (c & 0xFF)) ^ (b & 0xFF)) & 0xFF] & 0x7F
    return c & 0x7F


def uleb_encode(n: int) -> bytes:
    try:
        n = int(n)
    except Exception:
        n = 0
    if n < 0:
        n &= 0xFFFFFFFFFFFFFFFF
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


def zigzag_encode_signed(n: int) -> bytes:
    try:
        n = int(n)
    except Exception:
        n = 0
    zz = (n << 1) ^ (n >> 63)
    if zz < 0:
        zz = -zz
    zz &= 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        b = zz & 0x7F
        zz >>= 7
        if zz:
            b |= 0x80
        out.append(b)
        if not zz:
            break
    return bytes(out)


def f32_bits(val: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(val)))[0]


def layouts_from_mask(mask) -> Tuple[list, list]:
    if isinstance(mask, (bytes, bytearray)):
        mask = mask.decode("utf-8", errors="ignore")
    s = "".join(ch for ch in str(mask).strip() if ch in "01234")
    ru = [int(c) for c in s] if len(s) == 5 else [0, 1, 4, 3, 2]
    nr = [c for c in ru if c != 2]
    return ru, nr


def build_raw_packet(
    msg_key: int,
    layout: list,
    so: int,
    cmd: int,
    oid: Optional[int],
    flags: int,
    content: bytes,
    key: bytes,
) -> bytes:
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    body = content
    hdr = bytearray([msg_key, 0])
    for code in layout:
        val = {0: so, 1: cmd, 2: oid or 0, 3: flags, 4: len(body)}[code]
        if FIELD_SIZES[code] == 1:
            hdr.append((val & 0xFF) ^ k)
        else:
            v = ((val & 0xFFFF) ^ v80) & 0xFFFF
            hdr.append(v & 0xFF)
            hdr.append((v >> 8) & 0xFF)
    pkt = bytearray(hdr + body)
    pkt[1] = crc7_buf(bytes(pkt[2:])) & 0x7F
    return bytes(pkt)


def build_movement_packet_2001(
    key_bytes: bytes,
    msg_key: int,
    x: float,
    y: float,
    z: float,
    angle: float,
    speed: float,
    vel_y: float,
    s1: int,
    s2: int,
    dt_ms: int,
    counter_ms: int,
    room_code: Any,
    player_match_id: int = 1,
    team_tag: int = 1,
) -> bytes:
    _, nr = layouts_from_mask(room_code) if room_code else (None, [0, 1, 4, 3])
    pid_val = int(player_match_id) & 0x7F if player_match_id else 1
    t_val = int(team_tag) if team_tag else 1
    body = bytearray(uleb_encode((t_val << 24) | pid_val))

    body += zigzag_encode_signed(int(round(x)))
    body += zigzag_encode_signed(int(round(y)))
    body += zigzag_encode_signed(int(round(z)))

    ca = math.cos(angle)
    sa = math.sin(angle)
    fx = max(-100, min(100, int(round(100.0 * ca))))
    fy = 0
    fz = max(-100, min(100, int(round(100.0 * sa))))
    body += zigzag_encode_signed(fx)
    body += zigzag_encode_signed(fy)
    body += zigzag_encode_signed(fz)

    if s1 == 0 and s2 == 0:
        mx, mz = 0, 0
    elif speed > 10.0:
        mx, mz = fx, fz
    else:
        mx, mz = 0, 0
    body += zigzag_encode_signed(mx)
    body += zigzag_encode_signed(mz)

    vx = int(round((speed * ca) / 10.0))
    vy = int(round(vel_y / 10.0))
    vz = int(round((speed * sa) / 10.0))
    body += zigzag_encode_signed(vx)
    body += zigzag_encode_signed(vy)
    body += zigzag_encode_signed(vz)

    body += uleb_encode(int(s1))
    body += uleb_encode(int(s2))

    pitch = -0.05
    cp = math.cos(pitch)
    sp = math.sin(pitch)
    body += uleb_encode(f32_bits(ca * cp))
    body += uleb_encode(f32_bits(sp))
    body += uleb_encode(f32_bits(sa * cp))

    body += b"\x00\x00"
    body += struct.pack("<I", max(16, int(dt_ms)) & 0xFFFFFFFF)
    body += uleb_encode(int(counter_ms) & 0xFFFFFFFF)

    return build_raw_packet(msg_key, nr, 0, 2001, None, 0, bytes(body), key_bytes)


class MovementController:
    def __init__(
        self,
        account_id: Any,
        udp_sock,
        udp_server: Tuple[str, int],
        udp_key_bytes: bytes,
        match_code: Any,
        slot_id: int = 1,
        tick_hz: float = 10.0,
        label: str = "",
    ):
        self.account_id = int(account_id) if str(account_id).isdigit() else 0
        self.udp_sock = udp_sock
        self.udp_server = udp_server
        self.udp_key = udp_key_bytes
        self.match_code = match_code
        self.slot_id = max(1, min(4, int(slot_id)))
        self.interval = 1.0 / max(1.0, float(tick_hz))
        self.label = label or str(self.account_id)

        self.player_match_id = 1
        self.team_tag = 1
        self.spawn_synced = False
        self.ground_y = 570.0
        self.x = 0.0
        self.y = 570.0
        self.z = 0.0

        self.angle = (self.slot_id - 1) * (math.pi / 2.0)
        self.cur_speed = 5000.0
        self.vel_y = 0.0
        self.state = "sprint"
        self.pkt_s1 = 0
        self.pkt_s2 = 2

        self._jump_start_t = 0.0
        self._last_jump_t = time.time()
        self._last_decision_t = time.time()
        self._last_idle_t = time.time()
        self._idle_until = 0.0
        self._last_log_t = 0.0
        self._start_time = time.time()

        self._msg_keys = [0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D]
        self._key_idx = 0
        self._stop_evt = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    def set_spawn(self, x: float, y: float, z: float, player_match_id: int = 1, team_tag: int = 1):
        self.player_match_id = int(player_match_id) or 1
        self.team_tag = int(team_tag) or 1
        self.ground_y = float(y)
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.spawn_synced = True

    def handle_server_frame(self, frame: Dict[str, Any]):
        cmd = frame.get("cmd")
        content = frame.get("content")
        if not content:
            return

        if cmd == 2001 and len(content) >= 12 and content[:2] == b"\x00\x02":
            pid = content[2]
            if 1 <= pid <= 64:
                self.player_match_id = pid
            cur = 3

            def _dec_v(b, off):
                res = s = 0
                cur_ = off
                while cur_ < len(b):
                    bt = b[cur_]
                    res |= (bt & 0x7F) << s
                    cur_ += 1
                    s += 7
                    if not (bt & 0x80):
                        break
                return res, cur_

            def _dec_zz(n):
                return (n >> 1) ^ -(n & 1)

            try:
                vx, cur = _dec_v(content, cur)
                vy, cur = _dec_v(content, cur)
                vz, cur = _dec_v(content, cur)
                zx, zy, zz = _dec_zz(vx), _dec_zz(vy), _dec_zz(vz)

                if not self.spawn_synced:
                    self.x, self.y, self.z = float(zx), float(zy), float(zz)
                    self.ground_y = float(zy)
                    self.spawn_synced = True
                else:
                    if zy < 250000 or zy >= 800000:
                        self.ground_y = float(zy)
                        if self.state != "jump":
                            self.y = float(zy)
                        if zy < 250000 and math.hypot(self.x - zx, self.z - zz) > 6000.0:
                            self.x, self.z = float(zx), float(zz)
            except Exception:
                pass

    def _step(self, dt: float):
        now = time.time()
        if self.state not in ("jump", "land") and now < self._idle_until:
            self.state = "idle"
            self.cur_speed = 0.0
            self.vel_y = 0.0
            self.y = float(self.ground_y)
            self.pkt_s1, self.pkt_s2 = 0, 0
            return

        if (now - self._last_decision_t) >= 3.5:
            self._last_decision_t = now
            self.angle = (self.angle + random.uniform(-1.0, 1.0)) % (2.0 * math.pi)

        if self.state == "sprint" and (now - self._last_idle_t) >= 14.0:
            self._last_idle_t = now
            self._idle_until = now + 0.6
            self.state = "idle"
            self.cur_speed = 0.0
            self.vel_y = 0.0
            self.pkt_s1, self.pkt_s2 = 0, 0
            return

        if self.state == "sprint" and (now - self._last_jump_t) >= 5.0:
            self._last_jump_t = now
            self._jump_start_t = now
            self.state = "jump"

        if self.state == "jump":
            j_dur = 0.70
            j_t = now - self._jump_start_t
            j_prog = j_t / j_dur
            if 0.0 <= j_prog < 1.0:
                self.y = float(self.ground_y) + 699.0 * 4.0 * j_prog * (1.0 - j_prog)
                self.vel_y = 3395.0 - 9700.0 * j_t
                self.cur_speed = 5000.0
                self.pkt_s1, self.pkt_s2 = 1, 11
            else:
                self.y = float(self.ground_y)
                self.vel_y = -3800.0
                self.cur_speed = 5000.0
                self.state = "land"
                self.pkt_s1, self.pkt_s2 = 1, 0
        elif self.state == "land":
            self.y = float(self.ground_y)
            self.vel_y = 0.0
            self.cur_speed = 5000.0
            self.state = "sprint"
            self.pkt_s1, self.pkt_s2 = 0, 2
        else:
            self.y = float(self.ground_y)
            self.vel_y = 0.0
            self.cur_speed = 5000.0
            self.state = "sprint"
            self.pkt_s1, self.pkt_s2 = 0, 2

        self.x += math.cos(self.angle) * self.cur_speed * dt
        self.z += math.sin(self.angle) * self.cur_speed * dt

        if now - self._last_log_t >= 20.0:
            self._last_log_t = now
            print(
                f"  [MOVE:{self.label}] pos=({self.x:.0f}, {self.y:.0f}, {self.z:.0f}) "
                f"state={self.state}",
                flush=True,
            )

    async def _send_packet(self, dt_ms: int):
        if not self.udp_sock or not self.udp_key:
            return
        loop = asyncio.get_running_loop()
        mk = self._msg_keys[self._key_idx % len(self._msg_keys)]
        self._key_idx += 1
        time_ms = int((time.time() - self._start_time) * 1000) & 0xFFFFFFFF

        pkt = build_movement_packet_2001(
            key_bytes=self.udp_key,
            msg_key=mk,
            x=self.x,
            y=self.y,
            z=self.z,
            angle=self.angle,
            speed=self.cur_speed,
            vel_y=self.vel_y,
            s1=self.pkt_s1,
            s2=self.pkt_s2,
            dt_ms=dt_ms,
            counter_ms=time_ms,
            room_code=self.match_code,
            player_match_id=self.player_match_id,
            team_tag=self.team_tag,
        )
        try:
            await loop.sock_sendall(self.udp_sock, pkt)
        except Exception:
            try:
                if self.udp_server:
                    await loop.sock_sendto(self.udp_sock, pkt, self.udp_server)
            except Exception:
                pass

    async def _loop(self):
        self._start_time = time.time()
        wait_start = time.time()
        while not self.spawn_synced and not self._stop_evt.is_set():
            if time.time() - wait_start >= 2.5:
                break
            await asyncio.sleep(0.1)

        last_t = time.time()
        while not self._stop_evt.is_set():
            now = time.time()
            dt = now - last_t
            last_t = now
            if dt <= 0.0 or dt > 0.25:
                dt = self.interval

            dt_ms = max(20, min(150, int(round(dt * 1000.0))))
            self._step(dt)
            await self._send_packet(dt_ms)

            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    def start(self):
        if self._task and not self._task.done():
            return
        self._stop_evt.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop_evt.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except Exception:
                self._task.cancel()
