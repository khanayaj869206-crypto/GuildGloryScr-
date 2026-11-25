#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GUILD GLORY BOT — 4-Bot Squad BR Automation (Termux Ready)
==========================================================
Flow per match:
1. 4 bots login & join target Guild (optional)
2. Bot #1 (Leader) creates 4-player BR squad & generates Team Code
3. Bots #2, #3, #4 (Members) join automatically via Team Code (verified by server)
4. Once all 4 bots are confirmed in squad, Leader starts BR matchmaking
5. Each of the 4 bots waits for its OWN 0300 match token & launches its OWN UDP + Movement session
6. Repeats continuously for the exact number of matches set in Termux, then disconnects all 4 bots offline cleanly.
"""

import argparse
import asyncio
import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from protocol import (
    br_create_squad_pkt,
    br_join_team_pkt,
    br_start_squad_pkt,
    collect_uids,
    connect_with_auto_refresh,
    decode_tcp_payload,
    err,
    extract_match_info,
    first,
    heartbeat_loop,
    info,
    is_placeholder_host,
    join_guild,
    leave_team_pkt,
    login_bot_sync,
    ok,
    op31_pkt,
    parse_addr,
    safe_send,
    to_int,
    to_str,
    udp_session,
    warn,
)

DEFAULT_ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.json")
SQUAD_SIZE = 4


def _banner():
    try:
        os.system("cls" if os.name == "nt" else "clear")
    except Exception:
        pass
    print("\033[96m" + "=" * 58)
    print("   GUILD GLORY BOT — 4-BOT SQUAD BR (OB55)")
    print("   1 Leader + 3 Auto-Join Members | Continuous Match Loop")
    print("=" * 58 + "\033[0m", flush=True)


def load_all_accounts(path: str):
    if not os.path.exists(path):
        raise RuntimeError(f"Accounts file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or len(data) < 2:
        raise RuntimeError("accounts.json me kam se kam 2 (best 4) bots hone chahiye!")
    return data


async def login_and_join_guild(all_accounts, guild_id: int, target_count: int = SQUAD_SIZE, region_override: str = "", stop_evt: asyncio.Event = None):
    ready = []
    for idx, raw_acc in enumerate(all_accounts, start=1):
        if stop_evt and stop_evt.is_set():
            break
        if len(ready) >= target_count:
            break
        acc = dict(raw_acc)
        if region_override:
            acc["region"] = region_override.upper()
        name = acc.get("name") or acc.get("nickname") or acc.get("uid") or f"Bot_{idx}"
        try:
            sess = await asyncio.to_thread(login_bot_sync, acc, None, False)
            sess["name"] = f"Bot{len(ready)+1}({str(sess['acc_id'])[-4:]})" if str(name).isdigit() else str(name)
            if guild_id:
                if sess.get("clan_id") and int(sess["clan_id"]) == int(guild_id):
                    ok(f"[{sess['name']}] Guild {guild_id} already active")
                else:
                    ok_g, st, host = await asyncio.to_thread(join_guild, sess["jwt"], guild_id, sess["region"])
                    if ok_g:
                        ok(f"[{sess['name']}] Guild {guild_id} joined ({host})")
                    else:
                        warn(f"[{sess['name']}] Guild join HTTP {st} ({host})")
            ready.append((acc, sess))
        except Exception as e:
            err(f"[{name}] Login failed ({e}) — trying next account if available")
    return ready


async def leader_bot(acc, session, holder, stop_evt):
    name = session["name"]
    writer = None
    hb_stop = asyncio.Event()
    hb_task = None
    try:
        session, reader, writer, fr = await connect_with_auto_refresh(acc, session)
        holder["sessions"][int(session["acc_id"])] = session
        hb_task = asyncio.create_task(heartbeat_loop(writer, reader, session, hb_stop))

        # Clean previous squad state before creating new group
        await safe_send(writer, leave_team_pkt(session["acc_id"], session["region"], session["key"], session["iv"]))
        await asyncio.sleep(0.8)

        create_pkt = br_create_squad_pkt(session["region"], session["key"], session["iv"], squad_players=SQUAD_SIZE)
        info(f"[{name}] Creating {SQUAD_SIZE}-player squad group...")
        if not await safe_send(writer, create_pkt):
            raise RuntimeError("Create squad packet send fail")

        op31_sent = False
        start_sent = False
        start_sent_at = 0.0
        start_retries = 0
        match_accepted = False
        team_at = 0.0
        last_create_at = time.time()
        deadline = time.time() + 180.0

        while time.time() < deadline and not stop_evt.is_set():
            if reader.at_eof():
                raise RuntimeError("Server EOF on leader")

            if holder["team_code"] and not team_at:
                team_at = time.time()

            # Retry create if server didn't reply with group/team code yet
            if not holder["team_code"] and (time.time() - last_create_at) >= 5.0:
                last_create_at = time.time()
                await safe_send(writer, create_pkt)

            confirmed_slaves = holder["joined_uids"] | holder["member_confirmed_uids"]
            all_joined = len(confirmed_slaves) >= len(holder["slave_uids"])
            wait_fallback = bool(team_at and (time.time() - team_at) >= 22.0 and len(confirmed_slaves) >= 1)

            if holder["team_code"] and not start_sent and (all_joined or wait_fallback):
                if not all_joined:
                    warn(f"[{name}] Fallback start with {len(confirmed_slaves)}/{len(holder['slave_uids'])} members joined")
                await asyncio.sleep(1.2)
                start_pkt = br_start_squad_pkt(session["acc_id"], session["region"], session["key"], session["iv"])
                if await safe_send(writer, start_pkt):
                    start_sent = True
                    start_sent_at = time.time()
                    ok(f"[{name}] Squad BR Match Start Sent! ({len(confirmed_slaves)+1}/{holder['total_bots']} bots in squad)")

            if team_at and not start_sent and (time.time() - team_at) >= 28.0 and len(confirmed_slaves) == 0:
                raise RuntimeError("0 members joined squad within 28s — aborting match start to retry squad")

            if start_sent and not match_accepted and start_retries < 3 and (time.time() - start_sent_at) >= 5.0:
                start_pkt = br_start_squad_pkt(session["acc_id"], session["region"], session["key"], session["iv"])
                if await safe_send(writer, start_pkt):
                    start_retries += 1
                    start_sent_at = time.time()
                    info(f"[{name}] Re-sending Squad BR Start (#{start_retries})")

            frame = await fr.next_frame(timeout=0.8)
            if not frame:
                continue

            op, payload = frame
            hex_op = op.hex()
            if hex_op in ("0b00", "0c00", "1e00"):
                raise RuntimeError(f"Server disconnected leader ({hex_op})")

            d = decode_tcp_payload(payload, session["key"], session["iv"])
            cmd = to_int(first(d, 4))
            data = first(d, 5)

            if op.startswith(b"\x05"):
                if cmd == 1 and isinstance(data, dict) and not holder["group_id"]:
                    gid = to_int(first(data, 1))
                    if gid:
                        holder["group_id"] = gid
                        ok(f"[{name}] Group Created (group_id={gid})")
                        if not op31_sent:
                            op31_sent = True
                            await safe_send(writer, op31_pkt(gid, session["region"], session["key"], session["iv"]))
                elif cmd == 32 and not holder["team_code"]:
                    tc = to_str(first(data, 1) if isinstance(data, dict) else None) or to_str(first(d, 1))
                    if tc:
                        holder["team_code"] = tc
                        holder["team_ready"].set()
                        ok(f"[{name}] Team Code Ready: {tc}")
                elif cmd in (4, 6, 12) or (holder["team_code"] and cmd != 1):
                    uids = collect_uids(d)
                    for uid in uids & holder["slave_uids"]:
                        if uid not in holder["joined_uids"]:
                            holder["joined_uids"].add(uid)
                            total_now = len(holder["joined_uids"] | holder["member_confirmed_uids"])
                            ok(f"[{name}] Member UID {uid} joined squad ({total_now}/{len(holder['slave_uids'])})")
                    if cmd == 10:
                        match_accepted = True
                        ok(f"[{name}] Matchmaking active (server accepted squad queue)")
                if cmd == 10 and not match_accepted:
                    match_accepted = True
                    ok(f"[{name}] Matchmaking active (server accepted squad queue)")

            elif op.startswith(b"\x11") and cmd == 5 and isinstance(data, dict) and not holder["team_code"]:
                tc = to_str(first(data, 11))
                if tc and tc.isdigit() and len(tc) >= 5:
                    holder["team_code"] = tc
                    holder["team_ready"].set()
                    ok(f"[{name}] Team Code Ready: {tc}")

            if holder["group_id"] and not op31_sent:
                op31_sent = True
                await safe_send(writer, op31_pkt(holder["group_id"], session["region"], session["key"], session["iv"]))

            # CRITICAL: Only register leader's OWN 0300 info (never overwrite members!)
            minfo = extract_match_info(op, payload, cmd, session["acc_id"], session["key"], session["iv"])
            if minfo:
                my_id = int(session["acc_id"])
                holder["match_infos"][my_id] = (session, minfo)
                if not holder["first_found_at"]:
                    holder["first_found_at"] = time.time()
                ok(f"[{name}] 0300 Match Ready -> {minfo['server']} ({len(holder['match_infos'])}/{holder['total_bots']})")
                if len(holder["match_infos"]) >= holder["total_bots"]:
                    holder["all_found"].set()
                break

        while not stop_evt.is_set() and not reader.at_eof():
            await asyncio.sleep(1.0)
    except Exception as e:
        err(f"[{name}] Leader error: {e}")
        holder["team_ready"].set()
    finally:
        hb_stop.set()
        if hb_task:
            hb_task.cancel()
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def member_bot(acc, session, member_idx, holder, stop_evt):
    name = session["name"]
    writer = None
    hb_stop = asyncio.Event()
    hb_task = None
    try:
        session, reader, writer, fr = await connect_with_auto_refresh(acc, session)
        my_id = int(session["acc_id"])
        holder["sessions"][my_id] = session
        hb_task = asyncio.create_task(heartbeat_loop(writer, reader, session, hb_stop))

        # Leave any leftover group before waiting for leader's team code
        await safe_send(writer, leave_team_pkt(my_id, session["region"], session["key"], session["iv"]))

        try:
            await asyncio.wait_for(holder["team_ready"].wait(), timeout=45.0)
        except asyncio.TimeoutError:
            err(f"[{name}] Team Code wait timeout")
            return

        tc = holder["team_code"]
        if not tc:
            err(f"[{name}] Team Code missing")
            return

        # Stagger join slightly so all 3 members don't collide at the exact same millisecond
        await asyncio.sleep(0.45 * member_idx)

        join_pkt = br_join_team_pkt(tc, session["region"], session["key"], session["iv"])
        info(f"[{name}] Joining Team Code {tc}...")
        await safe_send(writer, join_pkt)
        last_join_send = time.time()
        join_attempts = 1
        joined = False
        deadline = time.time() + 170.0

        while time.time() < deadline and not stop_evt.is_set():
            if reader.at_eof():
                break

            # Only retry join if server hasn't confirmed squad membership yet
            if not joined and my_id not in holder["joined_uids"] and join_attempts < 5 and (time.time() - last_join_send) >= 3.5:
                join_attempts += 1
                last_join_send = time.time()
                info(f"[{name}] Retrying join Team Code {tc} (#{join_attempts})")
                await safe_send(writer, join_pkt)

            frame = await fr.next_frame(timeout=0.8)
            if not frame:
                if my_id in holder["joined_uids"] and not joined:
                    joined = True
                continue

            op, payload = frame
            if op.hex() in ("0b00", "0c00", "1e00"):
                warn(f"[{name}] Server disconnected ({op.hex()})")
                break

            d = decode_tcp_payload(payload, session["key"], session["iv"])
            cmd = to_int(first(d, 4))

            if op.startswith(b"\x05") and not joined:
                uids_in_pkt = collect_uids(d)
                has_leader_or_gid = (
                    (holder["leader_uid"] in uids_in_pkt)
                    or (holder["group_id"] and holder["group_id"] in uids_in_pkt)
                    or (cmd in (4, 6) and len(payload) >= 60)
                )
                if has_leader_or_gid:
                    joined = True
                    holder["member_confirmed_uids"].add(my_id)
                    ok(f"[{name}] Confirmed inside squad {tc}!")

            if op.startswith(b"\x05") and cmd == 10:
                info(f"[{name}] Entered squad matchmaking queue")

            # CRITICAL: Register this member's OWN 0300 info (unique JWT & UDP key)
            minfo = extract_match_info(op, payload, cmd, my_id, session["key"], session["iv"])
            if minfo:
                holder["match_infos"][my_id] = (session, minfo)
                if not holder["first_found_at"]:
                    holder["first_found_at"] = time.time()
                ok(f"[{name}] 0300 Match Ready -> {minfo['server']} ({len(holder['match_infos'])}/{holder['total_bots']})")
                if len(holder["match_infos"]) >= holder["total_bots"]:
                    holder["all_found"].set()
                break

        while not stop_evt.is_set() and not reader.at_eof():
            await asyncio.sleep(1.0)
    except Exception as e:
        err(f"[{name}] Member error: {e}")
    finally:
        hb_stop.set()
        if hb_task:
            hb_task.cancel()
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def play_one_squad_match(
    ready_bots,
    match_no: int,
    total_matches: int,
    match_duration: float,
    idle_timeout: float,
    verbose: bool,
    external_stop_evt: asyncio.Event = None,
):
    tag = f"MATCH #{match_no}" + (f"/{total_matches}" if total_matches > 0 else " (∞)")
    print(f"\n\033[95m{'─' * 58}\n  Starting {tag} | {len(ready_bots)} Bots\n{'─' * 58}\033[0m", flush=True)

    leader_acc, leader_sess = ready_bots[0]
    slaves = ready_bots[1:]

    holder = {
        "group_id": None,
        "team_code": None,
        "team_ready": asyncio.Event(),
        "leader_uid": int(leader_sess["acc_id"]),
        "slave_uids": {int(s["acc_id"]) for _, s in slaves},
        "joined_uids": set(),
        "member_confirmed_uids": set(),
        "total_bots": len(ready_bots),
        "sessions": {},
        "match_infos": {},
        "first_found_at": 0.0,
        "all_found": asyncio.Event(),
    }
    stop_evt = asyncio.Event()

    async def _watch_external():
        if not external_stop_evt:
            return
        while not stop_evt.is_set():
            if external_stop_evt.is_set():
                stop_evt.set()
                break
            await asyncio.sleep(0.4)

    watch_task = asyncio.create_task(_watch_external()) if external_stop_evt else None
    tasks = [asyncio.create_task(leader_bot(leader_acc, leader_sess, holder, stop_evt))]
    await asyncio.sleep(0.8)
    for idx, (acc, sess) in enumerate(slaves, start=1):
        tasks.append(asyncio.create_task(member_bot(acc, sess, idx, holder, stop_evt)))

    try:
        deadline = time.time() + 180.0
        while time.time() < deadline:
            if external_stop_evt and external_stop_evt.is_set():
                return False
            if holder["all_found"].is_set():
                break
            if holder["first_found_at"] and (time.time() - holder["first_found_at"]) >= 12.0:
                warn(f"Grace timeout: {len(holder['match_infos'])}/{len(ready_bots)} bots received 0300")
                break
            if all(t.done() for t in tasks):
                break
            await asyncio.sleep(0.3)

        if not holder["match_infos"]:
            err(f"{tag}: Match nahi mila — agla attempt retry karega")
            return False

        ok(
            f"{tag} · Team Code {holder['team_code']} | Launching UDP Gameplay for "
            f"{len(holder['match_infos'])}/{len(ready_bots)} bots simultaneously!"
        )

        # CRITICAL FIX: Explicit arguments bind each bot's own sess, minfo, host, port (no loop closure bug!)
        async def _run_bot_udp(bot_sess, bot_minfo, udp_host, udp_port, slot_num):
            return await udp_session(
                host=udp_host,
                port=udp_port,
                key_hex=bot_minfo["udp_key"],
                token=bot_minfo["token"],
                player_id=int(bot_sess["acc_id"]),
                match_id=int(bot_minfo["match_id"]),
                mask=bot_minfo["mask"],
                extra448=bot_minfo.get("extra448"),
                slot_id=slot_num,
                duration=match_duration,
                idle_timeout=idle_timeout,
                match_label=bot_sess["name"],
                verbose=verbose,
            )

        udp_tasks = []
        for slot_num, (bot_sess, bot_minfo) in enumerate(holder["match_infos"].values(), start=1):
            host, port = parse_addr(bot_minfo["server"])
            if not host or is_placeholder_host(host):
                err(f"[{bot_sess['name']}] Invalid UDP server: {bot_minfo['server']}")
                continue
            if not port:
                port = 10018
            udp_tasks.append(asyncio.create_task(_run_bot_udp(bot_sess, bot_minfo, host, port, slot_num)))

        if udp_tasks:
            udp_gather = asyncio.gather(*udp_tasks, return_exceptions=True)
            if external_stop_evt:
                while not udp_gather.done():
                    if external_stop_evt.is_set():
                        for ut in udp_tasks:
                            ut.cancel()
                        break
                    await asyncio.sleep(0.5)
            results = await udp_gather
            for res in results:
                if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                    err(f"UDP task exception: {type(res).__name__}: {res}")

        if external_stop_evt and external_stop_evt.is_set():
            return False

        # Update sessions with any auto-refreshed tokens so next match reuses valid JWTs
        updated = []
        for acc, old_sess in ready_bots:
            uid_int = int(old_sess["acc_id"])
            updated.append((acc, holder["sessions"].get(uid_int, old_sess)))
        ready_bots[:] = updated

        ok(f"{tag} Completed cleanly!")
        return True
    finally:
        stop_evt.set()
        if watch_task:
            watch_task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_guild_glory_job(
    accounts_subset=None,
    clan_id: int = 0,
    region: str = "IND",
    target_glory: int = 2000,
    glory_per_bot: int = 22,
    stop_evt: asyncio.Event = None,
    on_progress=None,
    match_duration: float = 0.0,
    idle_timeout: float = 35.0,
    verbose: bool = False,
    auto_create_accounts: bool = True,
    accounts_json_path: str = DEFAULT_ACCOUNTS_FILE,
    name_prefix: str = "Glory",
    initial_matches: int = 0,
    initial_glory: int = 0,
):
    """
    Programmatic runner used by Termux server.py.
    1 Credit -> Generate 4 fresh accounts -> Join Guild -> 22 * 4 = 88 Glory/Game -> 23 Games = 2,000 Glory -> Auto-stop offline.
    Supports seamless resume after server restart from saved bot UIDs, matches_played, and glory_earned.
    """
    import math
    from account_gen import generate_accounts

    def _emit(payload):
        if on_progress:
            try:
                on_progress(payload)
            except Exception:
                pass

    completed = max(0, int(initial_matches or 0))
    glory_earned = max(0, int(initial_glory or 0))

    if glory_earned >= target_glory:
        _emit({
            "status": "completed",
            "bots_active": 0,
            "matches_played": completed,
            "glory_earned": glory_earned,
        })
        return {"completed_matches": completed, "glory_earned": glory_earned, "status": "completed"}

    chosen_accounts = list(accounts_subset or [])
    existing_uids = []
    for a in chosen_accounts:
        u = str(a.get("uid") or a.get("login_uid") or "")
        if u and u not in existing_uids:
            existing_uids.append(u)

    if auto_create_accounts and len(chosen_accounts) < SQUAD_SIZE:
        need_count = SQUAD_SIZE - len(chosen_accounts)
        _emit({
            "status": "creating_accounts",
            "accounts_created": len(chosen_accounts),
            "bot_uids": json.dumps(existing_uids),
            "bots_active": 0,
            "glory_earned": glory_earned,
            "matches_played": completed,
        })

        def _on_acc_progress(done, _total, acc):
            uid_s = str(acc.get("uid") or "")
            if uid_s and uid_s not in existing_uids:
                existing_uids.append(uid_s)
            _emit({
                "status": "creating_accounts",
                "accounts_created": min(SQUAD_SIZE, len(chosen_accounts) + done),
                "bot_uids": json.dumps(existing_uids),
            })

        new_accounts = await generate_accounts(
            count=need_count,
            region=region,
            name_prefix=name_prefix,
            accounts_json_path=accounts_json_path,
            stop_evt=stop_evt,
            on_progress=_on_acc_progress,
        )
        chosen_accounts.extend(new_accounts)
        if stop_evt and stop_evt.is_set():
            _emit({"status": "stopped", "bots_active": 0, "glory_earned": glory_earned, "matches_played": completed})
            return {"completed_matches": completed, "glory_earned": glory_earned, "status": "stopped"}

        if len(chosen_accounts) < 2:
            _emit({
                "status": "failed",
                "error": f"Account creation yielded {len(chosen_accounts)}/{SQUAD_SIZE} accounts — retry later",
                "accounts_created": len(chosen_accounts),
                "bots_active": 0,
            })
            return {"completed_matches": completed, "glory_earned": glory_earned, "status": "failed"}

    _emit({
        "status": "joining_guild",
        "accounts_created": len(chosen_accounts),
        "bot_uids": json.dumps([str(a.get("uid") or a.get("login_uid") or "") for a in chosen_accounts]),
        "bots_active": 0,
    })

    ready_bots = await login_and_join_guild(
        chosen_accounts,
        int(clan_id),
        target_count=SQUAD_SIZE,
        region_override=region,
        stop_evt=stop_evt,
    )
    if len(ready_bots) < 2:
        _emit({
            "status": "failed",
            "error": f"Only {len(ready_bots)} bots logged in — need at least 2 (target 4)",
            "bots_active": len(ready_bots),
        })
        return {"completed_matches": completed, "glory_earned": glory_earned, "status": "failed"}

    active_count = len(ready_bots)
    glory_per_match = active_count * glory_per_bot
    target_matches = max(1, math.ceil(target_glory / glory_per_match))

    _emit({
        "status": "running",
        "accounts_created": len(chosen_accounts),
        "bots_active": active_count,
        "glory_per_match": glory_per_match,
        "target_matches": target_matches,
        "matches_played": completed,
        "glory_earned": glory_earned,
    })

    attempt = 0
    max_attempts = max(1, target_matches - completed) + 10
    while glory_earned < target_glory and completed < target_matches and attempt < max_attempts:
        if stop_evt and stop_evt.is_set():
            _emit({"status": "stopped", "bots_active": 0, "glory_earned": glory_earned, "matches_played": completed})
            return {"completed_matches": completed, "glory_earned": glory_earned, "status": "stopped"}

        attempt += 1
        played = await play_one_squad_match(
            ready_bots,
            match_no=completed + 1,
            total_matches=target_matches,
            match_duration=match_duration,
            idle_timeout=idle_timeout,
            verbose=verbose,
            external_stop_evt=stop_evt,
        )
        if played:
            completed += 1
            glory_earned = min(target_glory, completed * glory_per_match)
            _emit({
                "status": "running" if (glory_earned < target_glory and completed < target_matches) else "completed",
                "bots_active": active_count if (glory_earned < target_glory and completed < target_matches) else 0,
                "glory_per_match": glory_per_match,
                "target_matches": target_matches,
                "matches_played": completed,
                "glory_earned": glory_earned,
            })

        if glory_earned >= target_glory or completed >= target_matches:
            break
        if stop_evt and stop_evt.is_set():
            break
        await asyncio.sleep(4.0)

    final_status = "stopped" if (stop_evt and stop_evt.is_set()) else "completed"
    _emit({
        "status": final_status,
        "bots_active": 0,
        "glory_per_match": glory_per_match,
        "target_matches": target_matches,
        "matches_played": completed,
        "glory_earned": glory_earned,
    })
    return {"completed_matches": completed, "glory_earned": glory_earned, "status": final_status}


async def amain(args):
    _banner()
    guild_id = args.guild
    if guild_id is None:
        raw_g = input("\033[96m[+] Guild ID daalo (skip karne ke liye Enter): \033[0m").strip()
        guild_id = int(raw_g) if raw_g.isdigit() else 0

    total_matches = args.matches
    if total_matches is None:
        raw_m = input("\033[96m[+] Kitne matches khelne hain? (e.g. 23, ya 0 = lagatar): \033[0m").strip()
        total_matches = int(raw_m) if raw_m.isdigit() else 1

    if args.create:
        from account_gen import generate_accounts
        info(f"Creating {SQUAD_SIZE} fresh accounts in region {args.region}...")
        all_accounts = await generate_accounts(
            count=SQUAD_SIZE,
            region=args.region,
            name_prefix=args.prefix,
            accounts_json_path=args.accounts,
        )
        ok(f"Created {len(all_accounts)}/{SQUAD_SIZE} fresh accounts!")
    else:
        all_accounts = load_all_accounts(args.accounts)
        info(f"Loaded {len(all_accounts)} accounts from {os.path.basename(args.accounts)} | Target Bots = {SQUAD_SIZE}")

    info(f"Guild ID = {guild_id or 'Skipped'} | Matches = {'Infinite (Ctrl+C to stop)' if total_matches == 0 else total_matches}")

    ready_bots = await login_and_join_guild(
        all_accounts,
        guild_id,
        target_count=SQUAD_SIZE,
        region_override=args.region if args.create else "",
    )
    if len(ready_bots) < 2:
        err(f"Sirf {len(ready_bots)} bots login hue — squad ke liye kam se kam 2 (best 4) chahiye!")
        return
    if len(ready_bots) < SQUAD_SIZE:
        warn(f"Sirf {len(ready_bots)}/{SQUAD_SIZE} bots login hue — inhi ke saath squad start kar raha hu.")

    match_no = 0
    completed = 0
    while total_matches == 0 or match_no < total_matches:
        match_no += 1
        played = await play_one_squad_match(
            ready_bots,
            match_no=match_no,
            total_matches=total_matches,
            match_duration=args.duration,
            idle_timeout=args.idle_timeout,
            verbose=args.verbose,
        )
        if played:
            completed += 1
        if total_matches == 0 or match_no < total_matches:
            info("Next match 4 seconds me start hoga...")
            await asyncio.sleep(4.0)

    print(f"\n\033[92m[✓] Target Complete: {completed}/{match_no} matches played! All {len(ready_bots)} bots are now OFFLINE.\033[0m\n", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Guild Glory 4-Bot Squad BR Automation (Termux)")
    ap.add_argument("--guild", type=int, default=None, help="Guild ID to join before starting matches (0 to skip)")
    ap.add_argument("--matches", type=int, default=None, help="Number of matches to play (0 = infinite loop)")
    ap.add_argument("--create", action="store_true", help="Create 4 fresh accounts automatically before playing")
    ap.add_argument("--region", default="IND", help="Region for auto-created accounts (IND, BD, SG, BR, ME)")
    ap.add_argument("--prefix", default="Glory", help="Nickname prefix for auto-created accounts")
    ap.add_argument("--duration", type=float, default=0.0, help="Force per-match seconds (0 = auto-detect match end)")
    ap.add_argument("--idle-timeout", type=float, default=35.0, help="Seconds of UDP silence to treat match as finished")
    ap.add_argument("--accounts", default=DEFAULT_ACCOUNTS_FILE, help="Path to accounts.json")
    ap.add_argument("--verbose", action="store_true", help="Print every raw UDP packet header")
    args = ap.parse_args()

    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\n\033[93m[!] Stopped by user — all bots disconnected offline.\033[0m\n")


if __name__ == "__main__":
    main()
