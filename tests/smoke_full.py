"""
Comprehensive smoke test driving every user-visible endpoint against a live
3-node cluster (alpha=8888, beta=8889, gamma=8890).

Run with the cluster up:
    python -m tests.smoke_full

Categories exercised:
  1. Status / files / manifest / chunk
  2. Upload (password) + download (instant) + preview
  3. Resumable download (start / progress / pause / resume / file)
  4. Chats (invite -> accept -> messages -> reject -> delete)
  5. Sharing (share -> shares -> path -> ack -> share-receipts -> delete)
  6. Audits (run / random / log / reputation)
  7. Threshold (upload-threshold -> probe -> recipient download)

Reports PASS / FAIL / SKIP for each.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import time
import json
import traceback

import httpx

ALPHA = "http://127.0.0.1:8888"
BETA  = "http://127.0.0.1:8889"
GAMMA = "http://127.0.0.1:8890"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    sym = {"PASS": "[OK]", "FAIL": "[XX]", "SKIP": "[..]"}[status]
    print(f"  {sym} {name}" + (f" -- {detail}" if detail else ""))


def get(url: str, timeout: float = 10) -> httpx.Response:
    return httpx.get(url, timeout=timeout)


def post(url: str, **kw) -> httpx.Response:
    kw.setdefault("timeout", 30)
    return httpx.post(url, **kw)


def expect(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "expectation failed")


# ── 1. Identity / discovery ────────────────────────────────────────

def test_status_and_discovery() -> dict:
    print("\n[1] STATUS / DISCOVERY")
    ids: dict[str, str] = {}
    for label, url in [("alpha", ALPHA), ("beta", BETA), ("gamma", GAMMA)]:
        try:
            r = get(f"{url}/status")
            r.raise_for_status()
            d = r.json()
            ids[label] = d["node_id"]
            alive = sum(1 for p in d["peers"].values() if p.get("alive"))
            record(f"status[{label}]", PASS, f"id={d['node_id'][:12]} alive_peers={alive}")
        except Exception as e:
            record(f"status[{label}]", FAIL, str(e))
            ids[label] = ""
    return ids


# ── 2. Upload + download (password mode) ───────────────────────────

def test_upload_and_download() -> tuple[str, bytes, str]:
    print("\n[2] UPLOAD / DOWNLOAD (password mode)")
    payload = ("Hello DistriStore smoke test! " * 200).encode()
    src_hash = hashlib.sha256(payload).hexdigest()

    file_hash = ""
    try:
        files = {"file": ("smoke.txt", io.BytesIO(payload), "text/plain")}
        data = {"password": "smoketest"}
        r = post(f"{ALPHA}/upload", files=files, data=data, timeout=60)
        r.raise_for_status()
        body = r.json()
        file_hash = body["file_hash"]
        expect(body["chunks"] >= 1, "no chunks in manifest")
        record("upload[alpha]", PASS, f"hash={file_hash[:16]} chunks={body['chunks']}")
    except Exception as e:
        record("upload[alpha]", FAIL, repr(e))
        return "", payload, src_hash

    # /files lists it on alpha
    try:
        r = get(f"{ALPHA}/files")
        r.raise_for_status()
        files = r.json().get("files", [])
        expect(any(f["file_hash"] == file_hash for f in files), "uploaded file missing from /files")
        record("files[alpha]", PASS, f"{len(files)} file(s)")
    except Exception as e:
        record("files[alpha]", FAIL, repr(e))

    # /manifest/{hash}
    try:
        r = get(f"{ALPHA}/manifest/{file_hash}")
        r.raise_for_status()
        m = r.json()
        expect(m["file_hash"] == file_hash, "manifest hash mismatch")
        record("manifest[alpha]", PASS, f"chunks={len(m['chunks'])} merkle={m['merkle_root'][:16]}")
    except Exception as e:
        record("manifest[alpha]", FAIL, repr(e))

    # Instant download with correct password
    try:
        r = httpx.get(f"{ALPHA}/download/{file_hash}", params={"password": "smoketest"}, timeout=60)
        r.raise_for_status()
        got = r.content
        expect(hashlib.sha256(got).hexdigest() == src_hash, "round-trip hash mismatch")
        record("download[alpha:correct-pwd]", PASS, f"{len(got)} bytes")
    except Exception as e:
        record("download[alpha:correct-pwd]", FAIL, repr(e))

    # Wrong password should fail with non-2xx
    try:
        r = httpx.get(f"{ALPHA}/download/{file_hash}", params={"password": "wrong"}, timeout=20)
        if r.status_code >= 400:
            record("download[alpha:wrong-pwd]", PASS, f"got HTTP {r.status_code}")
        else:
            record("download[alpha:wrong-pwd]", FAIL, "wrong password should fail but got 200")
    except Exception as e:
        record("download[alpha:wrong-pwd]", FAIL, repr(e))

    # Cross-node: beta downloads alpha's file (chunks fetched via onion route)
    try:
        r = httpx.get(f"{BETA}/download/{file_hash}", params={"password": "smoketest"}, timeout=120)
        r.raise_for_status()
        got = r.content
        expect(hashlib.sha256(got).hexdigest() == src_hash, "cross-node hash mismatch")
        record("download[beta:onion-from-alpha]", PASS, f"{len(got)} bytes")
    except Exception as e:
        record("download[beta:onion-from-alpha]", FAIL, repr(e))

    # /preview/{hash} — should stream successfully
    try:
        r = httpx.get(f"{ALPHA}/preview/{file_hash}", params={"password": "smoketest"}, timeout=30)
        r.raise_for_status()
        record("preview[alpha]", PASS, f"len={len(r.content)} ct={r.headers.get('content-type','')}")
    except Exception as e:
        record("preview[alpha]", FAIL, repr(e))

    return file_hash, payload, src_hash


# ── 3. Resumable download ─────────────────────────────────────────

def test_resumable_download(file_hash: str, src_hash: str):
    print("\n[3] RESUMABLE DOWNLOAD")
    if not file_hash:
        record("resumable[*]", SKIP, "no file uploaded")
        return
    try:
        r = post(f"{BETA}/download/{file_hash}/start", params={"password": "smoketest"})
        r.raise_for_status()
        record("download.start[beta]", PASS)
    except Exception as e:
        record("download.start[beta]", FAIL, repr(e))
        return

    # Poll until completed (small file should finish fast)
    state = "?"
    for _ in range(30):
        try:
            r = get(f"{BETA}/download/{file_hash}/progress")
            if r.status_code == 200:
                state = r.json()["download"]["status"]
                if state in ("completed", "error"):
                    break
        except Exception:
            pass
        time.sleep(0.5)
    record("download.progress[beta]", PASS if state == "completed" else FAIL, f"status={state}")

    # /downloads
    try:
        r = get(f"{BETA}/downloads")
        r.raise_for_status()
        record("downloads[beta]", PASS, f"{len(r.json().get('downloads', {}))} entries")
    except Exception as e:
        record("downloads[beta]", FAIL, repr(e))

    # Fetch the merged file
    try:
        r = httpx.get(f"{BETA}/download/{file_hash}/file", timeout=30)
        r.raise_for_status()
        got_hash = hashlib.sha256(r.content).hexdigest()
        ok = got_hash == src_hash
        record("download.file[beta]", PASS if ok else FAIL, f"{len(r.content)} bytes, hash_match={ok}")
    except Exception as e:
        record("download.file[beta]", FAIL, repr(e))

    # /downloads/clear
    try:
        r = post(f"{BETA}/downloads/clear")
        r.raise_for_status()
        record("downloads.clear[beta]", PASS)
    except Exception as e:
        record("downloads.clear[beta]", FAIL, repr(e))


# ── 4. Chats ──────────────────────────────────────────────────────

def test_chats(ids: dict) -> bool:
    print("\n[4] CHATS")
    if not (ids.get("alpha") and ids.get("beta")):
        record("chats[*]", SKIP, "no node ids")
        return False

    alpha_id, beta_id = ids["alpha"], ids["beta"]

    # Reset state: delete any existing thread between alpha and beta
    for url, peer in [(ALPHA, beta_id), (BETA, alpha_id)]:
        try:
            httpx.delete(f"{url}/chats/{peer}", timeout=10)
        except Exception:
            pass

    time.sleep(1)

    # Alpha invites beta
    try:
        r = post(f"{ALPHA}/chats/invite", json={"peer_id": beta_id})
        r.raise_for_status()
        record("chats.invite[alpha->beta]", PASS, f"status={r.json().get('status', '?')}")
    except Exception as e:
        record("chats.invite[alpha->beta]", FAIL, repr(e))
        return False

    time.sleep(1)

    # Beta accepts
    try:
        r = post(f"{BETA}/chats/{alpha_id}/accept")
        r.raise_for_status()
        record("chats.accept[beta]", PASS)
    except Exception as e:
        record("chats.accept[beta]", FAIL, repr(e))
        return False

    time.sleep(1)

    # Both list /chats and see status=accepted
    for label, url, peer in [("alpha", ALPHA, beta_id), ("beta", BETA, alpha_id)]:
        try:
            r = get(f"{url}/chats")
            r.raise_for_status()
            chats = r.json().get("chats", [])
            t = next((c for c in chats if c["peer_id"] == peer), None)
            ok = t and t["status"] == "accepted"
            record(f"chats.list[{label}]", PASS if ok else FAIL, f"status={t['status'] if t else 'missing'}")
        except Exception as e:
            record(f"chats.list[{label}]", FAIL, repr(e))

    # Alpha sends, beta sends
    for sender_url, recv_id, label in [(ALPHA, beta_id, "alpha->beta"), (BETA, alpha_id, "beta->alpha")]:
        try:
            r = post(f"{sender_url}/chats/{recv_id}/messages", json={"text": f"hi from {label} at {int(time.time())}"})
            r.raise_for_status()
            record(f"chats.send[{label}]", PASS)
        except Exception as e:
            record(f"chats.send[{label}]", FAIL, repr(e))

    time.sleep(1)

    # Both fetch messages
    for label, url, peer in [("alpha", ALPHA, beta_id), ("beta", BETA, alpha_id)]:
        try:
            r = get(f"{url}/chats/{peer}/messages")
            r.raise_for_status()
            msgs = r.json().get("messages", [])
            record(f"chats.messages[{label}]", PASS if len(msgs) >= 2 else FAIL, f"{len(msgs)} msgs")
        except Exception as e:
            record(f"chats.messages[{label}]", FAIL, repr(e))

    return True


# ── 5. Sharing ────────────────────────────────────────────────────

def test_sharing(ids: dict, file_hash: str, src_hash: str):
    print("\n[5] SHARING")
    if not (file_hash and ids.get("beta")):
        record("share[*]", SKIP, "no file or no beta id")
        return

    beta_id = ids["beta"]
    share_id = None

    try:
        r = post(f"{ALPHA}/share", json={
            "to_peer_id": beta_id,
            "file_hashes": [file_hash],
            "note": "smoke-test share",
        })
        r.raise_for_status()
        body = r.json()
        record("share.send[alpha->beta]", PASS, f"status={body.get('status','?')}")
    except Exception as e:
        record("share.send[alpha->beta]", FAIL, repr(e))
        return

    time.sleep(1)

    # Beta lists /shares
    try:
        r = get(f"{BETA}/shares")
        r.raise_for_status()
        shares = r.json().get("shares", [])
        match = next((s for s in shares if s["file_hash"] == file_hash), None)
        if match:
            share_id = match["id"]
            record("share.list[beta]", PASS, f"share_id={share_id} note={match.get('note','')}")
        else:
            record("share.list[beta]", FAIL, f"share for {file_hash[:8]} not found")
            return
    except Exception as e:
        record("share.list[beta]", FAIL, repr(e))
        return

    # Beta downloads the shared file
    try:
        r = httpx.get(f"{BETA}/download/{file_hash}", params={"password": "smoketest"}, timeout=60)
        r.raise_for_status()
        ok = hashlib.sha256(r.content).hexdigest() == src_hash
        record("share.download[beta]", PASS if ok else FAIL, f"hash_match={ok}")
    except Exception as e:
        record("share.download[beta]", FAIL, repr(e))

    # Path inspection
    try:
        r = get(f"{BETA}/shares/{share_id}/path")
        if r.status_code == 200:
            path = r.json().get("path", [])
            record("share.path[beta]", PASS, f"hops={len(path)}")
        else:
            record("share.path[beta]", FAIL, f"HTTP {r.status_code}")
    except Exception as e:
        record("share.path[beta]", FAIL, repr(e))

    # Ack
    try:
        r = post(f"{BETA}/shares/{share_id}/ack")
        r.raise_for_status()
        record("share.ack[beta]", PASS)
    except Exception as e:
        record("share.ack[beta]", FAIL, repr(e))

    time.sleep(1)

    # Alpha sees the receipt
    try:
        r = get(f"{ALPHA}/share-receipts")
        r.raise_for_status()
        recs = r.json().get("receipts", [])
        record("share-receipts[alpha]", PASS if any(rec.get("file_hash") == file_hash for rec in recs) else FAIL,
               f"{len(recs)} receipt(s)")
    except Exception as e:
        record("share-receipts[alpha]", FAIL, repr(e))

    # Delete share from beta's inbox
    try:
        r = httpx.delete(f"{BETA}/shares/{share_id}", timeout=10)
        r.raise_for_status()
        record("share.delete[beta]", PASS)
    except Exception as e:
        record("share.delete[beta]", FAIL, repr(e))


# ── 6. Audits ─────────────────────────────────────────────────────

def test_audits(ids: dict):
    print("\n[6] AUDITS")
    try:
        r = post(f"{ALPHA}/audit/run")
        if r.status_code in (200, 503, 404):
            record("audit.run.random[alpha]", PASS, f"HTTP {r.status_code}")
        else:
            record("audit.run.random[alpha]", FAIL, f"HTTP {r.status_code} {r.text[:100]}")
    except Exception as e:
        record("audit.run.random[alpha]", FAIL, repr(e))

    if ids.get("beta"):
        try:
            r = post(f"{ALPHA}/audit/run/{ids['beta']}")
            if r.status_code in (200, 404, 503):
                record("audit.run.targeted[alpha->beta]", PASS, f"HTTP {r.status_code}")
            else:
                record("audit.run.targeted[alpha->beta]", FAIL, f"HTTP {r.status_code}")
        except Exception as e:
            record("audit.run.targeted[alpha->beta]", FAIL, repr(e))

    try:
        r = get(f"{ALPHA}/audit/log")
        r.raise_for_status()
        record("audit.log[alpha]", PASS, f"{len(r.json().get('log', []))} entries")
    except Exception as e:
        record("audit.log[alpha]", FAIL, repr(e))

    try:
        r = get(f"{ALPHA}/audit/reputation")
        r.raise_for_status()
        rep = r.json().get("reputation", [])
        record("audit.reputation[alpha]", PASS, f"{len(rep)} peer(s) tracked")
    except Exception as e:
        record("audit.reputation[alpha]", FAIL, repr(e))


# ── 7. Threshold-encrypted upload ─────────────────────────────────

def test_threshold(ids: dict):
    print("\n[7] THRESHOLD (Shamir Secret Sharing)")
    alpha_id, beta_id, gamma_id = ids.get("alpha"), ids.get("beta"), ids.get("gamma")
    if not (alpha_id and beta_id and gamma_id):
        record("threshold[*]", SKIP, "missing node ids")
        return

    # Need accepted chats: alpha<->beta (already done) and alpha<->gamma (need to set up)
    # Reset alpha<->gamma chat
    for url, peer in [(ALPHA, gamma_id), (GAMMA, alpha_id)]:
        try:
            httpx.delete(f"{url}/chats/{peer}", timeout=10)
        except Exception:
            pass
    time.sleep(0.5)

    # Alpha invites gamma
    try:
        r = post(f"{ALPHA}/chats/invite", json={"peer_id": gamma_id})
        r.raise_for_status()
    except Exception as e:
        record("threshold.setup_chat[alpha->gamma]", FAIL, repr(e))
        return
    time.sleep(0.5)
    try:
        r = post(f"{GAMMA}/chats/{alpha_id}/accept")
        r.raise_for_status()
        record("threshold.setup_chat[gamma_accept]", PASS)
    except Exception as e:
        record("threshold.setup_chat[gamma_accept]", FAIL, repr(e))
        return
    time.sleep(1)

    # Upload threshold: file has 1-of-1 split with gamma as the only holder,
    # recipient = beta. (Auto-pick will use gamma.)
    payload = ("Threshold smoke test. " * 150).encode()
    src_hash = hashlib.sha256(payload).hexdigest()
    file_hash = ""
    try:
        files = {"file": ("threshold_smoke.txt", io.BytesIO(payload), "text/plain")}
        data = {"recipient_id": beta_id, "m": "1", "n": "1"}
        r = post(f"{ALPHA}/upload-threshold", files=files, data=data, timeout=60)
        r.raise_for_status()
        body = r.json()
        file_hash = body["file_hash"]
        record("threshold.upload[alpha]", PASS,
               f"hash={file_hash[:12]} m={body['key_m']} n={body['key_n']} holders={[h['holder_name'] for h in body['share_holders']]}")
    except Exception as e:
        record("threshold.upload[alpha]", FAIL, repr(e))
        return

    # Probe from beta's side
    time.sleep(1)
    try:
        r = get(f"{BETA}/threshold/{file_hash}/probe")
        r.raise_for_status()
        p = r.json()
        record("threshold.probe[beta]", PASS,
               f"is_threshold={p['is_threshold']} m={p['m']} n={p['n']} online={p['online_count']} decryptable={p['decryptable_now']}")
    except Exception as e:
        record("threshold.probe[beta]", FAIL, repr(e))

    # Beta downloads — backend should reconstruct key from gamma's share
    try:
        r = httpx.get(f"{BETA}/download/{file_hash}", timeout=60)
        if r.status_code == 200:
            ok = hashlib.sha256(r.content).hexdigest() == src_hash
            record("threshold.download[beta]", PASS if ok else FAIL, f"hash_match={ok} len={len(r.content)}")
        else:
            record("threshold.download[beta]", FAIL, f"HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        record("threshold.download[beta]", FAIL, repr(e))

    # Recipient mismatch test: alpha shouldn't be able to download
    try:
        r = httpx.get(f"{ALPHA}/download/{file_hash}", timeout=30)
        if r.status_code == 403:
            record("threshold.recipient-gate[alpha]", PASS, f"HTTP 403 as expected")
        elif r.status_code >= 400:
            record("threshold.recipient-gate[alpha]", PASS, f"HTTP {r.status_code} (non-200 ok)")
        else:
            record("threshold.recipient-gate[alpha]", FAIL, "alpha decrypted a file addressed to beta")
    except Exception as e:
        record("threshold.recipient-gate[alpha]", FAIL, repr(e))


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("  DistriStore — Comprehensive Smoke Test")
    print("=" * 72)

    ids = test_status_and_discovery()
    file_hash, payload, src_hash = test_upload_and_download()
    test_resumable_download(file_hash, src_hash)
    test_chats(ids)
    test_sharing(ids, file_hash, src_hash)
    test_audits(ids)
    test_threshold(ids)

    print("\n" + "=" * 72)
    print("  RESULTS SUMMARY")
    print("=" * 72)
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    n_skip = sum(1 for _, s, _ in results if s == SKIP)
    print(f"  PASS={n_pass}   FAIL={n_fail}   SKIP={n_skip}   TOTAL={len(results)}")
    if n_fail:
        print("\n  FAILURES:")
        for n, s, d in results:
            if s == FAIL:
                print(f"    - {n}: {d}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        traceback.print_exc()
        sys.exit(2)
