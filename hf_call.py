#!/usr/bin/env python3
"""hf_call.py - the Higgsfield CLI failure handling every fire script shares.

Measured on the four Petlab swaps (2026-09-25/26): dozens of "empty CLI response, no matching job"
(the CLI returned nothing while the job WAS created and completed later), HTTP 503s, two cut-off
downloads (ContentTooShortError, a paid job lost), one 503 during `account status` reported as
"the signed-in account is not petlab's". The rules this module holds:

  * an empty response, a 5xx, a dropped connection or a DNS blip is TRANSIENT: look the job up
    (created at/after the fire, same prompt, unclaimed) before ever re-firing, then retry the
    command with backoff; never let it read as "failed" without a lookup
  * a 5xx during the account check means UNREACHABLE, not signed out and not the wrong account
  * a download is verified against Content-Length and retried; a partial file never stays on disk
  * a content-filter refusal is its own class (reword, do not retry the same text)

Pure helpers plus two thin wrappers over subprocess/urllib. No credits are spent here except by
`submit()` re-running the command the caller built, and only after a lookup found no job.
"""
import json, os, re, subprocess, time, urllib.request

TRANSIENT = ("no response received", "request failed", "503", "502", "504", "timed out", "timeout",
             "econnreset", "getaddrinfo failed", "socket hang up", "temporarily unavailable",
             "service unavailable", "bad gateway", "network error")
AUTH = ("not signed in", "unauthorized", "401", "login", "auth")
NSFW = ("nsfw", "content policy", "safety")


def classify(text):
    """'nsfw' | 'transient' | 'auth' | None from a CLI's stderr/stdout."""
    t = (text or "").lower()
    if any(k in t for k in NSFW):
        return "nsfw"
    if any(k in t for k in TRANSIENT):
        return "transient"
    if any(k in t for k in AUTH):
        return "auth"
    return None


def parse_job(stdout):
    try:
        i = stdout.index("[")
        arr = json.loads(stdout[i:])
        return arr[0] if arr else None
    except Exception:
        try:
            i = stdout.index("{")
            return json.loads(stdout[i:])
        except Exception:
            return None


def find_job(hf, want_prompt, since, claimed=(), size=100, runner=None):
    """One lookup: the completed, unclaimed job with this FULL prompt created at/after `since`
    (ISO UTC). Returns (job, n_candidates). Two candidates = ambiguous, the caller must not guess.
    `size` is 100 because the default page is short and a busy account pages the job away."""
    runner = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                                    errors="replace", timeout=300).stdout)
    out = runner([hf, "generate", "list", "--size", str(size), "--json"]) or ""
    try:
        jobs = json.loads(out[out.index("["):])
    except Exception:
        return None, 0
    cands = [j for j in jobs
             if (j.get("params") or {}).get("prompt", "") == want_prompt and j.get("status") == "completed"
             and j.get("result_url") and j.get("id") not in claimed and str(j.get("created_at", "")) >= since]
    return (cands[0] if len(cands) == 1 else None), len(cands)


def submit(cmd, hf, want_prompt, since, claimed=(), retries=2, backoff=(30, 90), lookups=6, lookup_wait=45,
           runner=None, sleep=time.sleep, log=None):
    """Run a `generate create` command and land a job dict.

    Returns (job, outcome, text): outcome is 'ok' | 'nsfw' | 'auth' | 'ambiguous' | 'failed'.
    On an empty/transient response: look the job up (lookups x lookup_wait) BEFORE re-firing, then
    re-run with backoff, at most `retries` extra fires. A job with status != completed is 'failed'."""
    runner = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, encoding="utf-8",
                                                  errors="replace", timeout=3600))
    say = log or (lambda *a: None)
    fires = 0
    while True:
        r = runner(cmd)
        fires += 1
        stdout, stderr = getattr(r, "stdout", "") or "", getattr(r, "stderr", "") or ""
        job = parse_job(stdout)
        if job is not None:
            if job.get("status") == "completed" and job.get("result_url"):
                return job, "ok", ""
            return job, "failed", "status=%s %s" % (job.get("status"), stderr[-160:])
        err = (stderr or stdout)[-300:]
        kind = classify(err)
        if kind == "nsfw":
            return None, "nsfw", err
        if kind == "auth":
            return None, "auth", err
        # empty or transient: the job may exist. Look before firing again.
        for i in range(lookups):
            sleep(lookup_wait)
            found, n = find_job(hf, want_prompt, since, claimed, runner=(lambda c: runner(c).stdout) if runner else None)
            if found:
                say("recovered job %s after an empty response" % found.get("id"))
                return found, "ok", ""
            if n > 1:
                return None, "ambiguous", "%d completed jobs share this prompt since %s" % (n, since)
        if fires > retries:
            return None, "failed", "empty CLI response after %d fires, no matching job: %s" % (fires, err.replace("\n", " "))
        wait = backoff[min(fires - 1, len(backoff) - 1)]
        say("transient CLI failure (%s) - re-firing in %ss" % (err.strip()[:80], wait))
        sleep(wait)


def download(url, dest, tries=3, opener=None, sleep=time.sleep):
    """Fetch to `dest`, verify the byte count against Content-Length, retry a short or dropped
    transfer. A partial file is removed so a re-run never sees a broken 'existing take'."""
    opener = opener or urllib.request.urlopen
    last = ""
    for i in range(tries):
        try:
            with opener(url, timeout=600) as resp:
                want = int(resp.headers.get("Content-Length") or 0)
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                tmp = dest + ".part"
                with open(tmp, "wb") as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                got = os.path.getsize(tmp)
                if want and got != want:
                    os.remove(tmp)
                    last = "short read %d/%d bytes" % (got, want)
                else:
                    os.replace(tmp, dest)
                    return True, ""
        except Exception as e:
            last = "%s %s" % (type(e).__name__, str(e)[:120])
            try:
                os.remove(dest + ".part")
            except OSError:
                pass
        if i + 1 < tries:
            sleep(5 * (i + 1))
    return False, last


def account_status(rc, out):
    """('ok', email) | ('unreachable', text) | ('signed_out', text). A 5xx or dropped connection
    during `account status` is the API, not the login - never report it as the wrong account."""
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", out or "")
    if rc == 0 and email:
        return "ok", email.group(0).lower()
    if classify(out) == "transient":
        return "unreachable", (out or "").strip()[:200]
    return "signed_out", (out or "").strip()[:200]
