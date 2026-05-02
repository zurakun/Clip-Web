#!/usr/bin/env python3
"""
CLIPPER TOOL - Web Version
Flask backend + Auth, Register, Profile, Admin Panel
"""

import os, sys, json, time, re, math, subprocess, shutil, threading, uuid
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from flask import (Flask, render_template, request, jsonify,
                   send_file, Response, session, redirect, url_for)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "clipper-tool-secret-2024")

# ── AssemblyAI Config ─────────────────────────────────────────────────────────
# Fallback transcription jika yt-dlp tidak dapat subtitle
ASSEMBLYAI_KEY = os.environ.get("ASSEMBLYAI_KEY", "db3377cd75b64ae9aaa0bee5554304b5")

BASE    = Path("ClipperTool")
CLIPS   = BASE / "clips"
REPORTS = BASE / "reports"
TEMP    = BASE / ".tmp"
for _d in [CLIPS, REPORTS, TEMP]: _d.mkdir(parents=True, exist_ok=True)

jobs = {}

def job_log(jid, msg, level="info"):
    if jid in jobs:
        jobs[jid]["logs"].append({"time": time.time(), "msg": msg, "level": level})

def job_progress(jid, pct, stage=""):
    if jid in jobs:
        jobs[jid]["progress"] = pct
        jobs[jid]["stage"] = stage

def _pip(pkg):
    subprocess.run([sys.executable,"-m","pip","install",pkg,
                    "--break-system-packages","-q"], check=True)

for _d in ["yt_dlp"]:
    try: __import__(_d)
    except ImportError: _pip(_d.replace("_","-"))

# ── Auth ──────────────────────────────────────────────────────────────────────
from auth import (
    login_user, register_user, get_current_user, is_admin,
    increment_usage, update_profile, change_own_password,
    login_required, admin_required,
    get_all_users, create_user, delete_user,
    toggle_user_active, change_password, set_user_role,
    assign_license, revoke_license,
)

# ── Metadata ──────────────────────────────────────────────────────────────────
def fetch_info(url):
    r = subprocess.run(
        ["yt-dlp","--dump-json","--no-playlist","--no-warnings",
         "--socket-timeout","30", url],
        capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300] or "yt-dlp returned no info")
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"): return json.loads(line)
    raise RuntimeError("No JSON metadata found")

# ── FYP Score (Podcast) ───────────────────────────────────────────────────────
_MW = {"engagement":0.32,"view_ratio":0.25,"duration":0.18,"watch_time":0.15,"trending":0.10}

def meta_score(m):
    dur = float(m.get("duration") or 0)
    vw  = int(m.get("view_count") or 0)
    lk  = int(m.get("like_count") or 0)
    cm  = int(m.get("comment_count") or 0)
    sub = int(m.get("channel_follower_count") or 1)
    ts  = float(m.get("timestamp") or 0)
    tags= m.get("tags") or []
    txt = ((m.get("title") or "")+" "+(m.get("description") or "")[:400]+" "+" ".join(tags)).lower()

    eng = (lk+cm*2)/max(vw,1)*100
    es  = 100 if eng>=10 else 85 if eng>=5 else 65 if eng>=2 else 45 if eng>=0.5 else 20
    rt  = vw/max(sub,1)
    rs  = 100 if rt>=10 else 85 if rt>=5 else 65 if rt>=1 else 40 if rt>=0.1 else 20
    ds  = (100 if 15<=dur<=90 else 82 if dur<=180 else
           max(0,dur/15*65) if dur<15 else max(0,82-(dur-180)/420*55) if dur<=600 else 8)
    ws  = max(0,min(100,100-max(0,dur-60)/4))
    if ts:
        age = (time.time()-ts)/86400
        trs = 100 if age<=1 else 85 if age<=7 else 65 if age<=30 else 40 if age<=90 else 20
    else: trs = 50

    bd    = {"engagement":round(es),"view_ratio":round(rs),"duration":round(ds),"watch_time":round(ws),"trending":round(trs)}
    total = sum(bd[k]*_MW[k] for k in _MW)
    hooks = ["viral","fyp","foryou","omg","epic","shocking","must watch","pov","storytime","exposed","reaction","trending","crazy"]
    bonus = min(10, sum(2 for k in hooks if k in txt))
    total = min(100, total+bonus)

    if total>=80:   lb,lc="VIRAL POTENTIAL","#ef4444"
    elif total>=65: lb,lc="HIGH FYP CHANCE","#8b5cf6"
    elif total>=48: lb,lc="WORTH CLIPPING","#22c55e"
    elif total>=30: lb,lc="LOW POTENTIAL","#f59e0b"
    else:           lb,lc="SKIP","#94a3b8"

    return {"total":round(total,1),"breakdown":bd,"label":lb,"color":lc,"bonus":bonus,
            "raw":{"duration":dur,"views":vw,"likes":lk,"comments":cm,"subs":sub}}

# ── Gaming Score ──────────────────────────────────────────────────────────────
def gaming_score(m):
    dur = float(m.get("duration") or 0)
    vw  = int(m.get("view_count") or 0)
    lk  = int(m.get("like_count") or 0)
    cm  = int(m.get("comment_count") or 0)
    sub = int(m.get("channel_follower_count") or 1)
    txt = ((m.get("title") or "")+" "+(m.get("description") or "")[:300]).lower()

    eng = (lk+cm*2)/max(vw,1)*100
    es  = 100 if eng>=5 else 75 if eng>=2 else 50 if eng>=0.5 else 25
    rt  = vw/max(sub,1)
    rs  = 100 if rt>=5 else 75 if rt>=1 else 40 if rt>=0.1 else 20

    game_kw = ["clutch","kill","win","epic","insane","best","highlight","montage",
               "gameplay","no scope","ace","carry","ranked","victory","boss",
               "speedrun","tryhard","comeback","rage"]
    bonus     = min(15, sum(3 for k in game_kw if k in txt))
    total     = min(100, round(es*0.4 + rs*0.6 + bonus, 1))

    if total>=75:   lb,lc="BANGER CLIP","#ef4444"
    elif total>=55: lb,lc="HIGHLIGHT","#8b5cf6"
    elif total>=35: lb,lc="DECENT CLIP","#22c55e"
    else:           lb,lc="LOW HYPE","#f59e0b"

    return {"total":total,"label":lb,"color":lc,"bonus":bonus,
            "breakdown":{"engagement":round(es),"view_ratio":round(rs),"kw_bonus":bonus},
            "raw":{"duration":dur,"views":vw,"likes":lk,"comments":cm,"subs":sub}}

# ── Transcript ────────────────────────────────────────────────────────────────
_HOOK_PODCAST = [
    "tapi tunggu","wait","hold on","but first","sebelum itu","ternyata","yang bikin",
    "kamu harus","jangan lewatkan","rahasia","bocoran","faktanya","rupanya","siapa sangka",
    "gak nyangka","gak percaya","gila","gokil","parah","kocak","wow","amazing","incredible",
    "insane","unbelievable","omg","tiba-tiba","seketika","akhirnya","suddenly","finally",
    "turns out","revealed","shocking","exposed","reaction","cobain","harus coba","wajib",
    "terbaik","nomor satu","pov","storytime","wait for it","plot twist","nah fr",
]

_HOOK_GAMING = [
    "clutch","insane","crazy","no way","omg","what","how","impossible","ez","gg",
    "rip","dead","kill","ace","win","lost","noob","pro","hack","cheat","bug","glitch",
    "rage","quit","carry","solo","rush","push","camp","snipe","headshot",
    "combo","spree","streak","boss",
]

def fetch_transcript(url, tmp_dir):
    out_tmpl = str(tmp_dir / "sub.%(ext)s")
    subprocess.run(["yt-dlp","--write-auto-subs","--write-subs","--sub-format","json3/vtt/best",
                    "--skip-download","--no-playlist","--no-warnings","-o",out_tmpl,url],
                   capture_output=True, timeout=60)
    for f in tmp_dir.glob("sub*.json3"):
        try: return _parse_json3(f)
        except: pass
    for f in tmp_dir.glob("sub*.vtt"):
        try: return _parse_vtt(f)
        except: pass
    return []

def _ts_parse(s):
    s = s.strip().replace(",",".")
    p = s.split(":")
    try:
        if len(p)==3: return int(p[0])*3600+int(p[1])*60+float(p[2])
        if len(p)==2: return int(p[0])*60+float(p[1])
        return float(p[0])
    except: return 0.0

def _parse_vtt(f):
    segs,buf,s,e = [],[],0,0
    for line in f.read_text(encoding="utf-8",errors="ignore").splitlines():
        line = line.strip()
        if "-->" in line:
            if buf: segs.append({"start":s,"end":e,"text":" ".join(buf)})
            p = line.split("-->"); s,e = _ts_parse(p[0]),_ts_parse(p[1].split()[0]); buf = []
        elif line and not line.startswith("WEBVTT") and not line.isdigit():
            c = re.sub(r"<[^>]+>","",line)
            if c: buf.append(c)
    if buf: segs.append({"start":s,"end":e,"text":" ".join(buf)})
    return segs

def _parse_json3(f):
    data = json.loads(f.read_text(encoding="utf-8",errors="ignore"))
    segs = []
    for ev in data.get("events",[]):
        sms = ev.get("tStartMs",0); dms = ev.get("dDurationMs",0)
        txt = "".join(sg.get("utf8","") for sg in ev.get("segs",[])).strip()
        if txt and txt!="\n": segs.append({"start":sms/1000,"end":(sms+dms)/1000,"text":txt})
    return segs

def score_transcript(segs, duration, clip_dur, mode="podcast"):
    if not segs: return []
    hooks   = _HOOK_GAMING if mode == "gaming" else _HOOK_PODCAST
    buckets = defaultdict(list)
    for sg in segs: buckets[int(sg["start"])].append(sg["text"].lower())
    step = max(1, clip_dur // 4)
    results = []
    for s in range(0, max(0, int(duration - clip_dur)) + 1, step):
        e   = s + clip_dur
        txt = " ".join(" ".join(buckets[t]) for t in range(s, min(e, int(duration))) if t in buckets)
        if not txt: continue
        matched = [w for w in hooks if w in txt]
        wc      = max(1, len(txt.split()))
        density = len(matched) / math.sqrt(wc) * 100
        pos_b   = max(0, 15*(1 - s/max(duration,1)))
        score   = min(100, round(density + pos_b, 1))
        if matched or density > 0:
            results.append({"start":s,"end":e,"score":score,"matched":matched[:5],
                             "preview":txt[:80].replace("\n"," "),"method":"transcript"})
    results.sort(key=lambda x: -x["score"])
    return results

# ── AssemblyAI Transcription (fallback) ───────────────────────────────────────
def assemblyai_transcribe(audio_url, clip_dur, jid=""):
    """
    Transkripsi audio via AssemblyAI REST API.
    Digunakan sebagai fallback jika yt-dlp tidak dapat subtitle.

    Returns:
        raw_segs  : list[{start, end, text}] — kompatibel dengan score_transcript()
        hl_segs   : list[{start, end, score, matched, preview, method}] — highlight siap pakai
    """
    import urllib.request

    base_headers = {
        "authorization": ASSEMBLYAI_KEY,
        "content-type":  "application/json",
    }

    # ── 1. Submit job ─────────────────────────────────────────────────────────
    if jid: job_log(jid, "AssemblyAI: submit transkripsi...", "info")

    payload = json.dumps({
        "audio_url":         audio_url,
        "auto_highlights":   True,   # highlight otomatis dari konten
        "language_detection": True,  # deteksi bahasa otomatis (ID/EN/dll)
    }).encode()

    req = urllib.request.Request(
        "https://api.assemblyai.com/v2/transcript",
        data=payload, headers=base_headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            submit_result = json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(f"AssemblyAI submit gagal: {e}")

    transcript_id = submit_result.get("id")
    if not transcript_id:
        raise RuntimeError(f"AssemblyAI tidak mengembalikan ID: {submit_result}")

    if jid: job_log(jid, f"AssemblyAI: job ID={transcript_id}, polling...", "info")

    # ── 2. Poll sampai selesai (max ~3 menit) ─────────────────────────────────
    poll_url = f"https://api.assemblyai.com/v2/transcript/{transcript_id}"
    poll_req = urllib.request.Request(poll_url, headers={"authorization": ASSEMBLYAI_KEY})

    result = {}
    for attempt in range(90):           # 90 × 3 detik = 4.5 menit max
        time.sleep(3)
        try:
            with urllib.request.urlopen(poll_req, timeout=30) as resp:
                result = json.loads(resp.read())
        except Exception:
            continue

        status = result.get("status", "")
        if jid and attempt % 5 == 0:
            job_log(jid, f"AssemblyAI: status={status} ({attempt*3}s)...")

        if status == "completed":
            break
        if status == "error":
            raise RuntimeError(f"AssemblyAI error: {result.get('error','unknown')}")
    else:
        raise RuntimeError("AssemblyAI timeout — melebihi batas waktu")

    # ── 3. Konversi words → raw_segs (kelompok ~5 detik) ─────────────────────
    words    = result.get("words") or []
    raw_segs = []

    if words:
        buf_start = words[0]["start"] / 1000
        buf_words = []

        for i, w in enumerate(words):
            buf_words.append(w.get("text", ""))
            w_end      = w["end"]   / 1000
            is_last    = (i == len(words) - 1)
            seg_dur    = w_end - buf_start
            has_punct  = w.get("text", "").rstrip().endswith((".", "!", "?"))

            if is_last or (seg_dur >= 5 and has_punct) or seg_dur >= 10:
                raw_segs.append({
                    "start": round(buf_start, 2),
                    "end":   round(w_end, 2),
                    "text":  " ".join(buf_words),
                })
                buf_words = []
                if i + 1 < len(words):
                    buf_start = words[i + 1]["start"] / 1000

    # ── 4. Konversi auto_highlights → hl_segs (scored, siap masuk pipeline) ──
    hl_segs    = []
    hl_result  = (result.get("auto_highlights_result") or {}).get("results") or []

    for hl in hl_result:
        rank       = float(hl.get("rank",  0))     # 0.0 – 1.0
        count      = int(  hl.get("count", 1))
        text       = hl.get("text", "")
        timestamps = hl.get("timestamps") or []

        for ts in timestamps:
            start_s = ts["start"] / 1000
            end_s   = min(start_s + clip_dur, ts["end"] / 1000 + clip_dur)
            score   = round(min(100, rank * 100 * 1.5 + count * 3), 1)
            hl_segs.append({
                "start":   int(start_s),
                "end":     int(end_s),
                "score":   score,
                "matched": [text],
                "preview": text,
                "method":  "assemblyai-highlight",
            })

    hl_segs.sort(key=lambda x: -x["score"])

    lang = result.get("language_code", "?")
    if jid:
        job_log(jid, f"AssemblyAI selesai: bahasa={lang}, "
                     f"{len(raw_segs)} segmen teks, {len(hl_segs)} highlight", "ok")

    return raw_segs, hl_segs

# ── Audio Probe ───────────────────────────────────────────────────────────────
from concurrent.futures import ThreadPoolExecutor, as_completed

# Konfigurasi kecepatan
MAX_WORKERS   = 5   # parallel probes (aman untuk YouTube)
PROBE_DUR_POD = 4   # detik per probe untuk podcast (turun dari 8)
PROBE_DUR_GAM = 3   # detik per probe untuk gaming (turun dari 5)
MAX_PROBES_POD = 8  # jumlah titik podcast (turun dari 12)
MAX_PROBES_GAM = 12 # jumlah titik gaming (turun dari 30)

def probe_segment_loudness(url, start, probe_dur=4):
    """Probe loudness satu titik — lightweight & cepat."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(int(start)),
        "-i", url,
        "-t", str(probe_dur),
        "-vn",
        "-af", "volumedetect",
        "-f", "null", "-"
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", r.stderr)
        if m: return float(m.group(1))
    except Exception:
        pass
    return 0.0

def get_direct_url(url):
    r = subprocess.run(
        ["yt-dlp", "-g", "--no-playlist", "--no-warnings",
         "-f", "bestaudio[ext=m4a]/bestaudio/best", url],
        capture_output=True, text=True, timeout=30)
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("http")]
    return lines[0] if lines else ""

def _probe_one(args):
    """Worker untuk ThreadPoolExecutor."""
    direct_url, pos, probe_dur = args
    vol = probe_segment_loudness(direct_url, pos, probe_dur)
    return (pos, vol)

def _score_volumes(raw, clip_dur, duration, mode):
    """Ubah list (pos, vol) menjadi scored segments."""
    vols = [v for _, v in raw if v != 0.0]
    if not vols: return []
    mn, mx  = min(vols), max(vols)
    avg_vol = sum(vols) / len(vols)
    span    = max(mx - mn, 1)
    scored  = []
    for pos, vol in raw:
        if vol == 0.0: continue
        base = (vol - mn) / span * 100
        if mode == "gaming":
            # Bonus spike: lonjakan di atas rata-rata
            spike = max(0, (vol - avg_vol) / span * 40)
            score = min(100, round(base + spike, 1))
            method = "gaming-spike"
        else:
            # Podcast: boost 30% untuk spike ringan saja
            spike = max(0, (vol - avg_vol) / span * 15) if vol > avg_vol * 1.3 else 0
            score = min(100, round(base + spike, 1))
            method = "audio"
        scored.append({
            "start":      pos,
            "end":        min(pos + clip_dur, int(duration)),
            "score":      score,
            "loudness_db": vol,
            "method":     method,
        })
    scored.sort(key=lambda x: -x["score"])
    return scored

def audio_probe_parallel(direct_url, duration, clip_dur, n_probes, probe_dur, mode, jid="", prog_start=25, prog_end=55):
    """
    Probe audio PARALEL — jauh lebih cepat dari sequential.
    n_probes titik diprobe sekaligus dengan MAX_WORKERS thread.
    """
    if not direct_url or duration <= 0: return []

    usable    = max(1, int(duration - clip_dur))
    step      = max(1, usable // max(n_probes - 1, 1))
    positions = list(range(0, usable + 1, step))[:n_probes]

    if jid: job_log(jid, f"Parallel probe: {len(positions)} titik × {probe_dur}s ({MAX_WORKERS} thread)...")

    raw      = [None] * len(positions)
    done_ref = [0]

    def update_progress(idx, pos, vol):
        raw[idx] = (pos, vol)
        done_ref[0] += 1
        pct = prog_start + int(done_ref[0] / len(positions) * (prog_end - prog_start))
        job_progress(jid, pct, "audio_probe")

    args_list = [(direct_url, pos, probe_dur) for pos in positions]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        future_map = {ex.submit(_probe_one, a): i for i, a in enumerate(args_list)}
        for fut in as_completed(future_map):
            idx      = future_map[fut]
            pos, vol = fut.result()
            update_progress(idx, pos, vol)
            if jid:
                job_log(jid, f"  [{done_ref[0]}/{len(positions)}] {str(timedelta(seconds=pos))} → {vol:.1f}dB")

    raw_clean = [(pos, vol) for pos, vol in raw if pos is not None]
    raw_clean.sort(key=lambda x: x[0])
    return _score_volumes(raw_clean, clip_dur, duration, mode)

# Alias compat
def audio_probe_candidates(direct_url, duration, clip_dur, n_probes=8, jid="", mode="podcast"):
    probe_dur = PROBE_DUR_GAM if mode == "gaming" else PROBE_DUR_POD
    return audio_probe_parallel(direct_url, duration, clip_dur, n_probes, probe_dur, mode, jid)

def detect_gaming_highlights(direct_url, duration, clip_dur, jid=""):
    """Gaming: parallel dense scan, probe singkat 3 detik."""
    if not direct_url or duration <= 0: return []
    # Hitung jumlah titik — setiap ~30 detik, max 12
    n = min(MAX_PROBES_GAM, max(6, int(duration / 30)))
    if jid: job_log(jid, f"Gaming parallel scan: {n} titik ({MAX_WORKERS} thread)...")
    return audio_probe_parallel(direct_url, duration, clip_dur,
                                 n_probes=n, probe_dur=PROBE_DUR_GAM,
                                 mode="gaming", jid=jid,
                                 prog_start=25, prog_end=55)

def select_top_segments(audio_segs, trans_segs, duration, clip_dur, num_clips, mode="podcast"):
    step    = max(1, clip_dur // 4)
    W_AUDIO = 0.35 if mode == "gaming" else 0.45
    W_TRANS = 0.65 if mode == "gaming" else 0.55
    grid    = {}
    for sg in audio_segs:
        snap = (sg["start"] // step) * step
        if snap not in grid: grid[snap] = {"audio":0,"trans":0,"matched":[],"preview":"","n":0}
        grid[snap]["audio"] = sg["score"]; grid[snap]["n"] += 1
    for sg in trans_segs:
        snap = (sg["start"] // step) * step
        if snap not in grid: grid[snap] = {"audio":0,"trans":0,"matched":[],"preview":"","n":0}
        grid[snap]["trans"] = sg["score"]; grid[snap]["matched"] = sg.get("matched",[])
        grid[snap]["preview"] = sg.get("preview",""); grid[snap]["n"] += 1

    candidates = []
    for snap, d in grid.items():
        final = min(100, round(d["audio"]*W_AUDIO + d["trans"]*W_TRANS + (d["n"]-1)*4, 1))
        end   = min(snap + clip_dur, int(duration))
        candidates.append({"start":snap,"end":end,"final_score":final,
                            "audio_score":d["audio"],"trans_score":d["trans"],
                            "matched":d["matched"],"preview":d["preview"],
                            "method":"combined" if d["n"]>1 else ("audio" if d["audio"] else "transcript")})
    candidates.sort(key=lambda x: -x["final_score"])
    selected = []
    for cand in candidates:
        if not any(not (cand["end"] <= s["start"] or cand["start"] >= s["end"]) for s in selected):
            selected.append(cand)
            # num_clips == 0 → auto mode: ambil semua highlight tanpa batas
            if num_clips > 0 and len(selected) >= num_clips: break
    return selected

def detect_subject_cx(src, n_samples=6):
    THUMB_W, THUMB_H = 160, 90
    pr = subprocess.run(["ffprobe","-v","quiet","-print_format","json","-show_entries","format=duration",str(src)],
                        capture_output=True, text=True, timeout=10)
    try: vid_dur = float(json.loads(pr.stdout)["format"]["duration"])
    except: vid_dur = 30.0
    step       = max(1.0, vid_dur / (n_samples + 1))
    timestamps = [step*(i+1) for i in range(n_samples) if step*(i+1) < vid_dur]
    if not timestamps: timestamps = [vid_dur/2]
    col_sums = [0] * THUMB_W
    for ts in timestamps:
        cmd = ["ffmpeg","-y","-ss",f"{ts:.2f}","-i",str(src),"-frames:v","1",
               "-vf",f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=disable",
               "-pix_fmt","gray","-f","rawvideo","pipe:1"]
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        if r.returncode!=0 or len(r.stdout)<THUMB_W*THUMB_H: continue
        pixels = r.stdout
        for row in range(THUMB_H):
            base = row*THUMB_W
            for col in range(THUMB_W): col_sums[col] += pixels[base+col]
    total = sum(col_sums)
    if total==0: return 0.5
    cx = sum(col*col_sums[col] for col in range(THUMB_W))/(total*THUMB_W)
    return max(0.1, min(0.9, cx))

def _smart_vf_916(w, h, cx_frac):
    if h >= w:
        return "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    crop_w = min(int(h*9/16), w)
    x_left = max(0, min(w-crop_w, int(cx_frac*w - crop_w/2)))
    return f"crop={crop_w}:{h}:{x_left}:0,scale=1080:1920,setsar=1"

def download_segment(url, start, end, out_stem, quality, jid=""):
    fmt_map = {
        "1080":"bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "720":"bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
        "480":"bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best",
    }
    tmpl = str(out_stem) + "_dl.%(ext)s"
    cmd  = ["yt-dlp","-f",fmt_map.get(quality,fmt_map["720"]),
            "--download-sections",f"*{start}-{end}","--merge-output-format","mp4",
            "--no-playlist","--no-warnings","--newline","--force-keyframes-at-cuts","-o",tmpl,url]
    if jid: job_log(jid, f"Downloading {str(timedelta(seconds=start))} -> {str(timedelta(seconds=end))}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for line in proc.stdout:
        if jid:
            m = re.search(r"(\d+\.?\d*)\s*%", line)
            if m: job_progress(jid, 60+int(float(m.group(1))*0.2), "downloading")
    proc.wait()
    if proc.returncode!=0: raise RuntimeError("yt-dlp segment download failed")
    files = sorted(out_stem.parent.glob(out_stem.name+"_dl.*"))
    if not files: raise RuntimeError("Downloaded file not found")
    return files[0]

def convert_clip(src, dst, clip_dur, use_916, jid=""):
    """
    16:9 → stream copy (cepat, tanpa re-encode).
    9:16 → re-encode dengan preset ultrafast + CRF 28 (lebih cepat dari fast+23).
    """
    if not use_916:
        # ── 16:9: stream copy, hampir instan ─────────────────────────────────
        if jid: job_log(jid, "16:9 stream copy (no re-encode)...")
        job_progress(jid, 88, "muxing")
        cmd = ["ffmpeg","-y","-i",str(src),"-t",str(clip_dur),
               "-c:v","copy","-c:a","copy","-movflags","+faststart",str(dst)]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        proc.wait()
        if proc.returncode == 0:
            job_progress(jid, 95, "done")
            return
        # Fallback jika copy gagal (codec incompatible)
        if jid: job_log(jid, "Stream copy gagal, fallback re-encode...", "warn")

    # ── 9:16 atau fallback: re-encode ultrafast ───────────────────────────────
    pr = subprocess.run(["ffprobe","-v","quiet","-print_format","json","-show_streams",str(src)],
                        capture_output=True, text=True)
    w, h = 1280, 720
    try:
        for s in json.loads(pr.stdout).get("streams",[]):
            if s.get("codec_type")=="video": w,h=int(s.get("width",1280)),int(s.get("height",720)); break
    except: pass

    if use_916:
        if jid: job_log(jid, "Detecting subject position...")
        cx = detect_subject_cx(src)
        vf = _smart_vf_916(w, h, cx)
    else:
        vf = "scale=trunc(iw/2)*2:trunc(ih/2)*2"   # hanya fix dimensi ganjil

    job_progress(jid, 88, "converting")
    if jid: job_log(jid, f"Re-encode {'9:16' if use_916 else '16:9'} ultrafast...")

    cmd = ["ffmpeg","-y","-i",str(src),"-t",str(clip_dur),
           "-vf", vf,
           "-c:v","libx264","-preset","ultrafast","-crf","28",
           "-c:a","aac","-b:a","96k","-movflags","+faststart",str(dst)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    proc.wait()
    if proc.returncode != 0:
        if use_916:
            # Fallback crop sederhana tanpa subject detection
            vf2 = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            cmd2 = ["ffmpeg","-y","-i",str(src),"-t",str(clip_dur),"-vf",vf2,
                    "-c:v","libx264","-preset","ultrafast","-crf","28",
                    "-c:a","aac","-b:a","96k","-movflags","+faststart",str(dst)]
            proc2 = subprocess.Popen(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            proc2.wait()
            if proc2.returncode != 0: raise RuntimeError("FFmpeg conversion failed")
        else:
            raise RuntimeError("FFmpeg conversion failed")
    job_progress(jid, 95, "done")

def _process_one_clip(args):
    """Worker: download + convert satu segmen. Dijalankan paralel."""
    url, seg, clip_dur, quality, use_916, jid, idx, total = args
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_stem = CLIPS / f"seg_{seg['start']}_{ts}"
    ar_sfx   = "9x16" if use_916 else "16x9"
    final    = CLIPS / f"clip_{seg['start']}s_{clip_dur}s_{ts}_{ar_sfx}.mp4"
    job_log(jid, f"[{idx}/{total}] Mulai download {str(timedelta(seconds=seg['start']))} → {str(timedelta(seconds=seg['end']))}")
    raw = download_segment(url, seg["start"], seg["end"], out_stem, quality, jid)
    convert_clip(raw, final, clip_dur, use_916, jid)
    raw.unlink(missing_ok=True)
    job_log(jid, f"[{idx}/{total}] Clip siap: {final.name}", "ok")
    return final.name

# ── Job Workers ───────────────────────────────────────────────────────────────
def run_analysis_job(jid, params):
    try:
        jobs[jid]["status"] = "running"
        url       = params["url"]
        clip_dur  = int(params.get("clip_dur", 30))
        num_clips = int(params.get("num_clips", 3))
        mode      = params.get("mode", "podcast")

        # ── FASE 1: Metadata (wajib, sequential) ──────────────────────────────
        job_log(jid, f"Mode: {mode.upper()} | Mengambil metadata...")
        job_progress(jid, 5, "metadata")
        meta     = fetch_info(url)
        duration = float(meta.get("duration") or 600)
        sc       = gaming_score(meta) if mode == "gaming" else meta_score(meta)
        jobs[jid]["meta"] = {
            "title":    meta.get("title",""),
            "uploader": meta.get("uploader",""),
            "duration": duration,
            "thumbnail":meta.get("thumbnail",""),
            "score":    sc,
            "mode":     mode,
        }
        job_log(jid, f"Video: {meta.get('title','')[:60]}", "ok")
        job_progress(jid, 12, "metadata")

        tmp_dir = TEMP / jid
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # ── FASE 2: Transcript + Direct URL — PARALEL ─────────────────────────
        job_log(jid, "Mengambil transcript & stream URL secara paralel...")
        job_progress(jid, 14, "parallel_fetch")

        trans_result  = [None]
        direct_result = [None]
        t_err         = [None]
        d_err         = [None]

        def _fetch_transcript():
            try:
                subs = fetch_transcript(url, tmp_dir)
                trans_result[0] = subs
            except Exception as e:
                t_err[0] = str(e)

        def _fetch_direct():
            try:
                direct_result[0] = get_direct_url(url)
            except Exception as e:
                d_err[0] = str(e)

        t1 = threading.Thread(target=_fetch_transcript, daemon=True)
        t2 = threading.Thread(target=_fetch_direct,    daemon=True)
        t1.start(); t2.start()
        t1.join();  t2.join()

        job_progress(jid, 22, "fetch_done")

        # Proses transcript
        trans_segs        = []
        assemblyai_hl_segs = []   # highlight dari AssemblyAI (jika dipakai)
        raw_subs          = trans_result[0] or []

        if raw_subs:
            trans_segs = score_transcript(raw_subs, duration, clip_dur, mode)
            job_log(jid, f"Transcript: {len(raw_subs)} baris → {len(trans_segs)} window", "ok")
        else:
            job_log(jid, "Tidak ada subtitle" + (f": {t_err[0]}" if t_err[0] else ""), "warn")

            # ── Fallback: AssemblyAI ──────────────────────────────────────────
            # Hanya jika API key tersedia dan direct_url sudah didapat
            if ASSEMBLYAI_KEY and direct_result[0]:
                try:
                    job_log(jid, "Mencoba AssemblyAI transcription sebagai fallback...", "info")
                    job_progress(jid, 24, "assemblyai")
                    aa_subs, assemblyai_hl_segs = assemblyai_transcribe(
                        direct_result[0], clip_dur, jid
                    )
                    if aa_subs:
                        trans_segs = score_transcript(aa_subs, duration, clip_dur, mode)
                        job_log(jid, f"AssemblyAI transcript: {len(aa_subs)} segmen "
                                     f"→ {len(trans_segs)} window scored", "ok")
                except Exception as e:
                    job_log(jid, f"AssemblyAI gagal: {e}", "warn")

        # ── FASE 3: Parallel Audio Probe ──────────────────────────────────────
        audio_segs = []
        direct_url = direct_result[0] or ""

        if not direct_url:
            job_log(jid, f"Gagal ambil stream URL{': ' + d_err[0] if d_err[0] else ''}", "warn")
        else:
            try:
                if mode == "gaming":
                    job_log(jid, f"Gaming: parallel audio spike ({MAX_PROBES_GAM} titik, {MAX_WORKERS} thread)...")
                    audio_segs = detect_gaming_highlights(direct_url, duration, clip_dur, jid)
                    if not audio_segs:
                        job_log(jid, "Tidak ada spike, fallback ke loudness probe...", "warn")
                        audio_segs = audio_probe_parallel(
                            direct_url, duration, clip_dur,
                            n_probes=MAX_PROBES_POD, probe_dur=PROBE_DUR_GAM,
                            mode="gaming", jid=jid)
                else:
                    job_log(jid, f"Podcast: parallel loudness probe ({MAX_PROBES_POD} titik, {MAX_WORKERS} thread)...")
                    audio_segs = audio_probe_parallel(
                        direct_url, duration, clip_dur,
                        n_probes=MAX_PROBES_POD, probe_dur=PROBE_DUR_POD,
                        mode="podcast", jid=jid)

                if audio_segs:
                    job_log(jid, f"Audio selesai: {len(audio_segs)} kandidat", "ok")
            except Exception as e:
                job_log(jid, f"Audio probe error: {e}", "warn")

        # ── FASE 4: Pilih segmen terbaik ──────────────────────────────────────
        # Merge AssemblyAI highlights (jika ada) ke dalam audio_segs
        if assemblyai_hl_segs:
            job_log(jid, f"Menggabungkan {len(assemblyai_hl_segs)} AssemblyAI highlight ke kandidat audio...")
            audio_segs = audio_segs + assemblyai_hl_segs

        job_log(jid, f"Memilih top segmen dari {len(audio_segs)} audio + {len(trans_segs)} transcript candidates...")
        job_progress(jid, 60, "selecting")
        best = select_top_segments(audio_segs, trans_segs, duration, clip_dur, num_clips, mode)

        if not best:
            job_log(jid, "Tidak ada sinyal, pakai auto-split...", "warn")
            usable     = max(1, int(duration) - clip_dur)
            # auto mode (num_clips==0): hitung dari durasi — 1 clip per clip_dur, maks 20
            auto_count = num_clips if num_clips > 0 else max(3, min(20, int(duration // clip_dur)))
            step       = max(clip_dur, usable // max(auto_count, 1))
            best       = []
            for i in range(auto_count):
                start = i * step
                end   = min(start + clip_dur, int(duration))
                if start >= int(duration): break
                best.append({
                    "start":start,"end":end,"final_score":round(max(10,50-i*5),1),
                    "audio_score":0,"trans_score":0,"matched":[],"preview":"","method":"auto-fallback"
                })
            job_log(jid, f"Auto-split: {len(best)} segmen", "warn")

        jobs[jid]["segments"] = best
        jobs[jid]["status"]   = "segments_ready"
        jobs[jid]["progress"] = 100
        job_log(jid, f"Selesai! Ditemukan {len(best)} segmen terbaik.", "ok")

    except Exception as e:
        jobs[jid]["status"] = "error"
        jobs[jid]["error"]  = str(e)
        job_log(jid, f"Error: {e}", "error")
    finally:
        shutil.rmtree(TEMP / jid, ignore_errors=True)

def run_download_job(jid, params):
    try:
        jobs[jid]["status"] = "downloading"
        url          = params["url"]
        clip_dur     = int(params.get("clip_dur", 30))
        quality      = params.get("quality", "720")
        use_916      = params.get("aspect", "916") == "916"
        segments_sel = params.get("segments", [])
        best         = jobs[jid].get("segments", [])

        to_dl = [best[i] for i in segments_sel if i < len(best)]
        if not to_dl: raise RuntimeError("No segments selected")

        job_log(jid, f"Memproses {len(to_dl)} clip...")
        out_files = []

        for idx, seg in enumerate(to_dl, 1):
            pct_start = 62 + int((idx - 1) / len(to_dl) * 35)
            pct_end   = 62 + int(idx       / len(to_dl) * 35)
            job_progress(jid, pct_start, "downloading")
            job_log(jid, f"[{idx}/{len(to_dl)}] Download {str(timedelta(seconds=seg['start']))} → {str(timedelta(seconds=seg['end']))}...")

            ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            out_stem = CLIPS / f"seg_{seg['start']}_{ts}"
            ar_sfx   = "9x16" if use_916 else "16x9"
            final    = CLIPS / f"clip_{seg['start']}s_{clip_dur}s_{ts}_{ar_sfx}.mp4"

            raw = download_segment(url, seg["start"], seg["end"], out_stem, quality, jid)
            convert_clip(raw, final, clip_dur, use_916, jid)
            raw.unlink(missing_ok=True)

            job_progress(jid, pct_end, "downloading")
            job_log(jid, f"[{idx}/{len(to_dl)}] Clip siap: {final.name}", "ok")
            out_files.append(final.name)

        jobs[jid]["result"]   = out_files
        jobs[jid]["status"]   = "done"
        jobs[jid]["progress"] = 100
        job_log(jid, f"Selesai! {len(out_files)} clip siap diunduh.", "ok")

    except Exception as e:
        jobs[jid]["status"] = "error"
        jobs[jid]["error"]  = str(e)
        job_log(jid, f"Error: {e}", "error")

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — AUTH
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET","POST"])
def login():
    if get_current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        user = login_user(username, password)
        if user:
            session["username"] = username
            session.permanent   = True
            return redirect(url_for("index"))
        return render_template("login.html", login_error="Username atau password salah", username=username)
    return render_template("login.html")

@app.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    result = register_user(
        data.get("username",""),
        data.get("password",""),
        data.get("display_name","")
    )
    return jsonify(result)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — PROFILE
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/profile")
@login_required
def profile():
    user = get_current_user()
    return render_template("profile.html", user=user)

@app.route("/api/profile", methods=["POST"])
@login_required
def api_profile():
    user = get_current_user()
    data = request.json or {}
    return jsonify(update_profile(user["username"], data.get("display_name",""), data.get("bio","")))

@app.route("/api/change_password", methods=["POST"])
@login_required
def api_change_password():
    user = get_current_user()
    data = request.json or {}
    return jsonify(change_own_password(user["username"], data.get("old_password",""), data.get("new_password","")))

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — ADMIN
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/admin")
@admin_required
def admin():
    return render_template("admin.html")

@app.route("/admin/api/me")
@admin_required
def admin_me():
    user = get_current_user()
    return jsonify(user)

@app.route("/admin/api/users")
@admin_required
def admin_users():
    return jsonify(get_all_users())

@app.route("/admin/api/users/create", methods=["POST"])
@admin_required
def admin_create_user():
    d = request.json or {}
    return jsonify(create_user(d.get("username","").strip(), d.get("password","").strip(), d.get("role","user")))

@app.route("/admin/api/users/<username>/delete", methods=["POST"])
@admin_required
def admin_delete_user(username):
    return jsonify(delete_user(username))

@app.route("/admin/api/users/<username>/toggle", methods=["POST"])
@admin_required
def admin_toggle_user(username):
    return jsonify(toggle_user_active(username))

@app.route("/admin/api/users/<username>/password", methods=["POST"])
@admin_required
def admin_change_password(username):
    d = request.json or {}
    return jsonify(change_password(username, d.get("password","")))

@app.route("/admin/api/users/<username>/role", methods=["POST"])
@admin_required
def admin_set_role(username):
    d = request.json or {}
    return jsonify(set_user_role(username, d.get("role","user")))

@app.route("/admin/api/users/<username>/license", methods=["POST"])
@admin_required
def admin_assign_license(username):
    d = request.json or {}
    return jsonify(assign_license(username, d.get("license_key")))

@app.route("/admin/api/users/<username>/revoke_license", methods=["POST"])
@admin_required
def admin_revoke_license(username):
    return jsonify(revoke_license(username))

@app.route("/admin/api/files")
@admin_required
def admin_files():
    files = []
    for f in sorted(CLIPS.glob("*.mp4"), key=lambda x: -x.stat().st_mtime):
        files.append({"name":f.name,"size":f.stat().st_size,"mtime":f.stat().st_mtime})
    return jsonify(files)

@app.route("/admin/api/jobs")
@admin_required
def admin_jobs():
    return jsonify({jid:{"status":j["status"],"progress":j["progress"],"stage":j.get("stage",""),
                         "segments":j.get("segments"),"error":j.get("error")} for jid,j in jobs.items()})

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — TOOL
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
@login_required
def index():
    return render_template("index.html")

@app.route("/api/analyze", methods=["POST"])
@login_required
def api_analyze():
    data = request.json or {}
    url  = (data.get("url") or "").strip()
    if not url: return jsonify({"error": "URL is required"}), 400
    jid = str(uuid.uuid4())[:8]
    jobs[jid] = {"status":"queued","progress":0,"stage":"","logs":[],
                 "result":None,"error":None,"meta":None,"segments":None,"params":data}
    user = get_current_user()
    if user: increment_usage(user["username"])
    threading.Thread(target=run_analysis_job, args=(jid, data), daemon=True).start()
    return jsonify({"job_id": jid})

@app.route("/api/download_clips", methods=["POST"])
@login_required
def api_download_clips():
    data = request.json or {}
    jid  = data.get("job_id")
    if not jid or jid not in jobs: return jsonify({"error": "Invalid job ID"}), 400
    params = {**jobs[jid].get("params", {}), "segments": data.get("segments", []),
              "quality": data.get("quality","720"), "aspect": data.get("aspect","916")}
    threading.Thread(target=run_download_job, args=(jid, params), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/stream/<jid>")
@login_required
def api_stream(jid):
    def generate():
        last_log      = 0
        last_status   = ""
        timeout_s     = 0
        meta_sent     = False   # track apakah meta sudah pernah dikirim
        while True:
            if jid not in jobs:
                yield f"data: {json.dumps({'error':'job not found'})}\n\n"
                break
            j        = jobs[jid]
            new_logs = j["logs"][last_log:]
            last_log = len(j["logs"])

            cur_status = j["status"]

            # Meta: kirim sekali saat tersedia, lalu kirim lagi setiap tick
            # segments_ready agar frontend tidak miss
            meta_payload = None
            if j.get("meta"):
                if not meta_sent or cur_status == "segments_ready":
                    meta_payload = j.get("meta")
                    meta_sent = True

            # Segments: kirim SETIAP tick selama segments_ready / done
            # sehingga frontend tidak miss meski ada delay render
            segments_payload = None
            if cur_status in ("segments_ready", "done") and j.get("segments"):
                segments_payload = j.get("segments")

            payload = {
                "status":            cur_status,
                "progress":          j["progress"],
                "stage":             j.get("stage", ""),
                "new_logs":          new_logs,
                "meta":              meta_payload,
                "segments":          segments_payload,
                "analysis_complete": cur_status in ("segments_ready", "done"),
                "result":            j.get("result"),
                "error":             j.get("error"),
            }
            last_status = cur_status
            yield f"data: {json.dumps(payload)}\n\n"

            # Berhenti hanya kalau benar-benar final
            if cur_status in ("done", "error"):
                break

            # segments_ready = analisis selesai, tunggu download dimulai (max 90s)
            if cur_status == "segments_ready":
                timeout_s += 1
                if timeout_s > 112:   # 112 * 0.8s ≈ 90 detik
                    break

            # downloading = aktif, reset timeout
            if cur_status == "downloading":
                timeout_s = 0

            time.sleep(0.8)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/status/<jid>")
@login_required
def api_status(jid):
    if jid not in jobs: return jsonify({"error":"Job not found"}), 404
    j = jobs[jid]
    return jsonify({"status":j["status"],"progress":j["progress"],"stage":j.get("stage",""),
                    "logs":j["logs"][-30:],"meta":j.get("meta"),"segments":j.get("segments"),
                    "result":j.get("result"),"error":j.get("error")})

@app.route("/api/files")
@login_required
def api_files():
    files = []
    for f in sorted(CLIPS.glob("*.mp4"), key=lambda x: -x.stat().st_mtime):
        files.append({"name":f.name,"size":f.stat().st_size,"mtime":f.stat().st_mtime})
    return jsonify(files)

@app.route("/api/download_file/<filename>")
@login_required
def api_download_file(filename):
    path = CLIPS / filename
    if not path.exists() or not path.is_file(): return "Not found", 404
    return send_file(path, as_attachment=True, download_name=filename)

@app.route("/api/delete_file/<filename>", methods=["DELETE"])
@login_required
def api_delete_file(filename):
    path = CLIPS / filename
    if path.exists(): path.unlink()
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
