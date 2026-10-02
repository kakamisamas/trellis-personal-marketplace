"""Run the §2.4 Jev request over samples.jsonl; write jev_results.jsonl. Needs TYPESAFE_API_KEY."""
import json, os, sys, time, urllib.request
key = os.environ.get("TYPESAFE_API_KEY")
if not key: sys.exit("TYPESAFE_API_KEY missing")
src, out = sys.argv[1], sys.argv[2]
done = {}
if os.path.exists(out):
    for l in open(out): r = json.loads(l); done[r["id"]] = r
with open(out, "a") as f:
    for l in open(src):
        s = json.loads(l)
        if s["id"] in done: continue
        body = {"model": "jev-latest", "state": s["text"][-3000:], "questions": {
            "needs_user": {"type": "noul", "instructions": "这段话的结尾是否在等用户回答或做决定，包括可做可不做的提议"},
            "blocking": {"type": "noul", "instructions": "如果用户不回复，这项工作是否无法继续"},
            "kind": {"type": "choice", "instructions": "这段话的性质", "criteria": {"问你拍板": None, "要凭据或权限": None, "汇报完成": None, "中途汇报": None}}}}
        req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=json.dumps(body).encode(),
              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read()); status = resp.status
        except urllib.error.HTTPError as e:
            payload = {"error": e.read().decode(errors="ignore")}; status = e.code
        rec = {"id": s["id"], "status": status, "ms": int((time.time()-t0)*1000), "label": s["label"], "answers": payload.get("answers"), "raw": payload if status != 200 else None}
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
        print(s["id"], status, rec["ms"], "ms", json.dumps(payload.get("answers"), ensure_ascii=False)[:160])
