#!/usr/bin/env python3
"""
CLIPPER TOOL - Web Version
Flask backend for Railway deployment
"""

import os, sys, json, time, re, math, subprocess, shutil, threading, uuid
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from flask import Flask, render_template, request, jsonify, send_file, Response

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "clipper-tool-secret-2024")

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

# ── FYP Score ─────────────────────────────────────────────────────────────────
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
    trs = (100 if (time.time()-ts)/86400<=1 else 85 if (time.time()-ts)/86400<=7 else
           65 if (time.time()-ts)/86400<=30 else 40 if (time.time()-ts)/86400<=90 else 20) if ts else 50

    bd    = {"engagement":round(es),"view_ratio":round(rs),"duration":round(ds),"watch_time":round(ws),"trending":round(trs)}
    total = sum(bd[k]*_MW[k] for k in _MW)
    hooks = ["viral","fyp","foryou","omg","epic","shocking","must watch","pov","storytime","exposed","reaction","trending","crazy"]
    bonus = min(10, sum(2 for k in hooks if k in txt))
    total = min(100, total+bonus)

    if total>=80:   lb,lc="VIRAL POTENTIAL","#ff4444"
    elif total>=65: lb,lc="HIGH FYP CHANCE","#cc44ff"
    elif total>=48: lb,lc="WORTH CLIPPING","#44ff88"
    elif total>=30: lb,lc="LOW POTENTIAL","#ffaa00"
    else:           lb,lc="SKIP","#888888"

    return {"total":round(total,1),"breakdown":bd,"label":lb,"color":lc,"bonus":bonus,
            "raw":{"duration":dur,"views":vw,"likes":lk,"comments":cm,"subs":sub}}

# ── Transcript ────────────────────────────────────────────────────────────────
_HOOK = ["tapi tunggu","wait","hold on","but first","sebelum itu","ternyata","yang bikin",
         "kamu harus","jangan lewatkan","rahasia","bocoran","faktanya","rupanya","siapa sangka",
         "gak nyangka","gak percaya","gila","gokil","parah","kocak","wow","amazing","incredible",
         "insane","unbelievable","omg","tiba-tiba","seketika","akhirnya","suddenly","finally",
         "turns out","revealed","shocking","exposed","reaction","cobain","harus coba","wajib",
         "terbaik","nomor satu","pov","storytime","wait for it","plot twist","nah fr"]

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

def score_transcript(segs, duration, clip_dur):
    if not segs: return []
    buckets = defaultdict(list)
    for sg in segs: buckets[int(sg["start"])].append(sg["text"].lower())
    step = max(1, clip_dur // 4)
    results = []
    for s in range(0, max(0, int(duration - clip_dur)) + 1, step):
        e   = s + clip_dur
        txt = " ".join(" ".join(buckets[t]) for t in range(s, min(e, int(duration))) if t in buckets)
        if not txt: continue
        matched = [w for w in _HOOK if w in txt]
        wc      = max(1, len(txt.split()))
        density = len(matched) / math.sqrt(wc) * 100
        pos_b   = max(0, 15*(1 - s/max(duration,1)))
        score   = min(100, round(density + pos_b, 1))
        if matched or density > 0:
            results.append({"start":s,"end":e,"score":score,"matched":matched[:5],
                             "preview":txt[:80].replace("\n"," "),"method":"transcript"})
    results.sort(key=lambda x: -x["score"])
    return results

# ── Audio Probe ───────────────────────────────────────────────────────────────
def probe_segment_loudness(url, start, probe_dur=8):
    cmd = ["ffmpeg","-y","-ss",str(start),"-i",url,"-t",str(probe_dur),"-vn","-af","volumedetect","-f","null","-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", r.stderr)
    if m:
        try: return float(m.group(1))
        except: pass
    return 0.0

def get_direct_url(url):
    r = subprocess.run(["yt-dlp","-g","--no-playlist","--no-warnings","-f","bestaudio[ext=m4a]/bestaudio/best",url],
                       capture_output=True, text=True, timeout=30)
    lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("http")]
    return lines[0] if lines else ""

def audio_probe_candidates(direct_url, duration, clip_dur, n_probes=8, jid=""):
    if not direct_url or duration <= 0: return []
    step      = max(1, int((duration - clip_dur) / max(n_probes - 1, 1)))
    positions = list(range(0, int(duration - clip_dur) + 1, step))[:n_probes]
    results   = []
    for i, pos in enumerate(positions):
        if jid: job_log(jid, f"Audio probe [{i+1}/{len(positions)}] @ {str(timedelta(seconds=pos))}")
        loudness = probe_segment_loudness(direct_url, pos)
        results.append((pos, loudness))
        if jid: job_progress(jid, 25 + int(i/len(positions)*30), "audio_probe")
    vals = [v for _,v in results if v != 0.0]
    if not vals: return []
    mn, mx = min(vals), max(vals)
    span   = max(mx - mn, 1)
    scored = []
    for pos, vol in results:
        score = 0 if vol==0.0 else round((vol-mn)/span*100,1)
        scored.append({"start":pos,"end":pos+clip_dur,"score":score,"loudness_db":vol,"method":"audio"})
    scored.sort(key=lambda x: -x["score"])
    return scored

# ── Segment Selection ─────────────────────────────────────────────────────────
def select_top_segments(audio_segs, trans_segs, duration, clip_dur, num_clips):
    step = max(1, clip_dur // 4)
    grid = {}
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
        final = min(100, round(d["audio"]*0.45 + d["trans"]*0.55 + (d["n"]-1)*4, 1))
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
            if len(selected) >= num_clips: break
    return selected

# ── Smart Crop ────────────────────────────────────────────────────────────────
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

# ── Download + Convert ────────────────────────────────────────────────────────
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
    pr = subprocess.run(["ffprobe","-v","quiet","-print_format","json","-show_streams",str(src)],
                        capture_output=True, text=True)
    w, h = 1280, 720
    try:
        for s in json.loads(pr.stdout).get("streams",[]):
            if s.get("codec_type")=="video": w,h=int(s.get("width",1280)),int(s.get("height",720)); break
    except: pass

    if use_916:
        if jid: job_log(jid, "Analyzing subject position for smart crop...")
        cx = detect_subject_cx(src)
        vf = _smart_vf_916(w, h, cx)
    else:
        vf = None

    cmd = ["ffmpeg","-y","-i",str(src),"-t",str(clip_dur)]
    if vf: cmd += ["-vf",vf]
    cmd += ["-c:v","libx264","-preset","fast","-crf","23","-c:a","aac","-b:a","128k","-movflags","+faststart",str(dst)]
    if jid: job_log(jid, f"Converting {'9:16 portrait' if use_916 else '16:9 landscape'}...")
    job_progress(jid, 85, "converting")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    proc.wait()
    if proc.returncode!=0 and use_916:
        vf2  = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        cmd2 = ["ffmpeg","-y","-i",str(src),"-t",str(clip_dur),"-vf",vf2,
                "-c:v","libx264","-preset","fast","-crf","23","-c:a","aac","-b:a","128k","-movflags","+faststart",str(dst)]
        proc2 = subprocess.Popen(cmd2, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        proc2.wait()
        if proc2.returncode!=0: raise RuntimeError("FFmpeg conversion failed")

# ── Job Workers ───────────────────────────────────────────────────────────────
def run_analysis_job(jid, params):
    try:
        jobs[jid]["status"] = "running"
        url      = params["url"]
        clip_dur = int(params.get("clip_dur", 30))
        num_clips= int(params.get("num_clips", 3))

        job_log(jid, "Fetching video metadata...")
        job_progress(jid, 5, "metadata")
        meta     = fetch_info(url)
        duration = float(meta.get("duration") or 600)
        sc       = meta_score(meta)
        jobs[jid]["meta"] = {
            "title":    meta.get("title",""),
            "uploader": meta.get("uploader",""),
            "duration": duration,
            "thumbnail":meta.get("thumbnail",""),
            "score":    sc,
        }
        job_log(jid, f"Video found: {meta.get('title','')[:60]}", "ok")
        job_progress(jid, 12, "metadata")

        tmp_dir = TEMP / jid
        tmp_dir.mkdir(parents=True, exist_ok=True)

        job_log(jid, "Fetching transcript...")
        job_progress(jid, 15, "transcript")
        trans_segs = []
        try:
            raw_subs = fetch_transcript(url, tmp_dir)
            if raw_subs:
                trans_segs = score_transcript(raw_subs, duration, clip_dur)
                job_log(jid, f"Transcript: {len(raw_subs)} lines, {len(trans_segs)} windows", "ok")
            else:
                job_log(jid, "No subtitles available", "warn")
        except Exception as e:
            job_log(jid, f"Transcript: {e}", "warn")

        job_log(jid, "Probing audio loudness...")
        job_progress(jid, 22, "audio_probe")
        audio_segs = []
        try:
            direct_url = get_direct_url(url)
            if direct_url:
                n_probes   = max(6, min(12, int(duration // max(clip_dur,1))))
                audio_segs = audio_probe_candidates(direct_url, duration, clip_dur, n_probes, jid)
                job_log(jid, f"Audio: {len(audio_segs)} candidate positions", "ok")
            else:
                job_log(jid, "Could not get direct stream URL", "warn")
        except Exception as e:
            job_log(jid, f"Audio probe: {e}", "warn")

        job_log(jid, f"Selecting top {num_clips} segments...")
        job_progress(jid, 58, "selecting")
        best = select_top_segments(audio_segs, trans_segs, duration, clip_dur, num_clips)

        # Fallback otomatis jika semua sinyal gagal
        if not best:
            job_log(jid, "No signals detected, using auto-split fallback...", "warn")
            usable = max(1, int(duration) - clip_dur)
            step   = max(clip_dur, usable // max(num_clips, 1))
            best   = []
            for i in range(num_clips):
                start = i * step
                end   = min(start + clip_dur, int(duration))
                if start >= int(duration): break
                best.append({
                    "start":       start,
                    "end":         end,
                    "final_score": round(max(10, 50 - i * 5), 1),
                    "audio_score": 0,
                    "trans_score": 0,
                    "matched":     [],
                    "preview":     "",
                    "method":      "auto-fallback",
                })
            job_log(jid, f"Auto-split: {len(best)} segment(s) generated", "warn")

        jobs[jid]["segments"] = best
        jobs[jid]["status"]   = "segments_ready"
        jobs[jid]["progress"] = 100
        job_log(jid, f"Analysis complete. Found {len(best)} segment(s).", "ok")

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

        out_files = []
        for idx, seg in enumerate(to_dl, 1):
            job_log(jid, f"Processing clip {idx}/{len(to_dl)}...")
            job_progress(jid, int(idx/len(to_dl)*85), "processing")
            ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_stem = CLIPS / f"seg_{seg['start']}_{ts}"
            ar_sfx   = "9x16" if use_916 else "16x9"
            final    = CLIPS / f"clip_{seg['start']}s_{clip_dur}s_{ts}_{ar_sfx}.mp4"
            raw      = download_segment(url, seg["start"], seg["end"], out_stem, quality, jid)
            convert_clip(raw, final, clip_dur, use_916, jid)
            raw.unlink(missing_ok=True)
            out_files.append(final.name)
            job_log(jid, f"Clip {idx} ready: {final.name}", "ok")

        jobs[jid]["result"]   = out_files
        jobs[jid]["status"]   = "done"
        jobs[jid]["progress"] = 100
        job_log(jid, f"Done! {len(out_files)} clip(s) created.", "ok")

    except Exception as e:
        jobs[jid]["status"] = "error"
        jobs[jid]["error"]  = str(e)
        job_log(jid, f"Error: {e}", "error")

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.json or {}
    url  = (data.get("url") or "").strip()
    if not url: return jsonify({"error": "URL is required"}), 400
    jid = str(uuid.uuid4())[:8]
    jobs[jid] = {"status":"queued","progress":0,"stage":"","logs":[],
                 "result":None,"error":None,"meta":None,"segments":None,"params":data}
    threading.Thread(target=run_analysis_job, args=(jid, data), daemon=True).start()
    return jsonify({"job_id": jid})

@app.route("/api/download_clips", methods=["POST"])
def api_download_clips():
    data = request.json or {}
    jid  = data.get("job_id")
    if not jid or jid not in jobs: return jsonify({"error": "Invalid job ID"}), 400
    params = {**jobs[jid].get("params", {}), "segments": data.get("segments", []),
              "quality": data.get("quality","720"), "aspect": data.get("aspect","916")}
    threading.Thread(target=run_download_job, args=(jid, params), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/stream/<jid>")
def api_stream(jid):
    def generate():
        last_log = 0
        last_status = ""
        while True:
            if jid not in jobs:
                yield f"data: {json.dumps({'error':'job not found'})}\n\n"; break
            j = jobs[jid]
            new_logs  = j["logs"][last_log:]
            last_log  = len(j["logs"])
            payload   = {
                "status": j["status"], "progress": j["progress"], "stage": j.get("stage",""),
                "new_logs": new_logs,
                "meta":     j.get("meta") if j.get("meta") and last_status!=j["status"] else None,
                "segments": j.get("segments") if j["status"]=="segments_ready" else None,
                "result":   j.get("result"),
                "error":    j.get("error"),
            }
            last_status = j["status"]
            yield f"data: {json.dumps(payload)}\n\n"
            if j["status"] in ("done","error","segments_ready"): break
            time.sleep(0.8)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/status/<jid>")
def api_status(jid):
    if jid not in jobs: return jsonify({"error":"Job not found"}), 404
    j = jobs[jid]
    return jsonify({"status":j["status"],"progress":j["progress"],"stage":j.get("stage",""),
                    "logs":j["logs"][-30:],"meta":j.get("meta"),"segments":j.get("segments"),
                    "result":j.get("result"),"error":j.get("error")})

@app.route("/api/files")
def api_files():
    files = []
    for f in sorted(CLIPS.glob("*.mp4"), key=lambda x: -x.stat().st_mtime):
        files.append({"name":f.name,"size":f.stat().st_size,"mtime":f.stat().st_mtime})
    return jsonify(files)

@app.route("/api/download_file/<filename>")
def api_download_file(filename):
    path = CLIPS / filename
    if not path.exists() or not path.is_file(): return "Not found", 404
    return send_file(path, as_attachment=True, download_name=filename)

@app.route("/api/delete_file/<filename>", methods=["DELETE"])
def api_delete_file(filename):
    path = CLIPS / filename
    if path.exists(): path.unlink()
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
