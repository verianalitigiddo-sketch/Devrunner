"""
sample_data/ altındaki demo verisini üretir (gerçek API yanıt biçiminde, gerçek ad / IP / token içermez).

    python tools/make_sample_data.py
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sample_data"
PREFIX = "DDDB.Iz.Anm."
BASE = "https://devops.halkbank.com.tr/DefaultCollection/Halkbank"
NOW = datetime(2026, 11, 16, 9, 30, tzinfo=timezone.utc)
rnd = random.Random(42)

# ad, CI var mı, CD türü (yaml | release | None), son CI sonucu
APPS = [
    ("Musteri360", True, "yaml", "succeeded"),
    ("KrediSkor", True, "yaml", "failed"),
    ("KampanyaTakip", True, "release", "succeeded"),
    ("RaporPortal", True, "yaml", "partiallySucceeded"),
    ("VeriKalite", True, None, "succeeded"),
    ("ChurnTahmin", True, "yaml", "inProgress"),
    ("SubeAnaliz", False, None, None),
    ("DokumanArama", True, "yaml", "canceled"),
]
PEOPLE = ["Ayşe Yılmaz", "Mehmet Kaya", "Elif Demir", "Can Öztürk", "Zeynep Arslan"]


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f0Z")


def repo_id(i):
    return f"0000000{i}-aaaa-4bbb-8ccc-00000000000{i}"


def build(def_id, def_name, bid, result, when, branch="master"):
    status = "inProgress" if result == "inProgress" else "completed"
    start = when
    finish = None if status != "completed" else when + timedelta(minutes=rnd.randint(2, 14), seconds=rnd.randint(0, 59))
    b = {
        "id": bid, "buildNumber": f"{when:%Y%m%d}.{rnd.randint(1, 6)}", "status": status,
        "queueTime": iso(when - timedelta(seconds=20)), "startTime": iso(start),
        "sourceBranch": f"refs/heads/{branch}", "sourceVersion": "%040x" % rnd.getrandbits(160),
        "requestedFor": {"displayName": rnd.choice(PEOPLE)},
        "definition": {"id": def_id, "name": def_name},
    }
    if status == "completed":
        b["result"] = result
        b["finishTime"] = iso(finish)
    return b


def main():
    repos, defs, builds, rdefs, deps, commits, prs = [], [], {}, [], {}, {}, {}
    def_id, bid = 100, 5000
    for i, (name, has_ci, cd, ci_result) in enumerate(APPS, start=1):
        rid = repo_id(i)
        repos.append({"id": rid, "name": PREFIX + name, "defaultBranch": "refs/heads/master",
                      "webUrl": f"{BASE}/_git/{PREFIX}{name}", "isDisabled": False,
                      "project": {"name": "Halkbank"}})
        commits[rid] = {"count": 5, "value": [
            {"commitId": "%040x" % rnd.getrandbits(160), "comment": msg,
             "author": {"name": rnd.choice(PEOPLE), "date": iso(NOW - timedelta(hours=6 * k + rnd.randint(0, 5)))},
             "remoteUrl": f"{BASE}/_git/{PREFIX}{name}/commit/x"}
            for k, msg in enumerate(["Sorgu performansı iyileştirildi", "Bağımlılıklar güncellendi",
                                     "Hata düzeltmesi: tarih filtresi", "Yeni rapor sekmesi", "README güncellendi"])]}
        prs[rid] = {"count": 1, "value": [] if i % 3 else [{
            "pullRequestId": 40 + i, "title": "Yeni KPI kartları", "isDraft": i == 3,
            "createdBy": {"displayName": rnd.choice(PEOPLE)}, "creationDate": iso(NOW - timedelta(days=1)),
            "sourceRefName": "refs/heads/feature/kpi", "targetRefName": "refs/heads/master",
            "repository": {"name": PREFIX + name}}]}
        pipes = []
        if has_ci:
            pipes.append(("CI", ci_result))
        if cd == "yaml":
            pipes.append(("CD", "succeeded" if ci_result != "failed" else "failed"))
        for suffix, result in pipes:
            def_id += 1
            dname = f"{PREFIX}{name}_Container-{suffix}"
            history = []
            for k in range(12):
                bid += 1
                res = result if k == 0 else rnd.choice(["succeeded"] * 6 + ["failed", "partiallySucceeded", "canceled"])
                history.append(build(def_id, dname, bid, res, NOW - timedelta(hours=5 * k + rnd.randint(0, 4), minutes=rnd.randint(0, 59))))
            builds[str(def_id)] = {"count": len(history), "value": history}
            latest = dict(history[0])
            latest.pop("definition")
            completed = next((dict(b) for b in history if b["status"] == "completed"), None)
            if completed:
                completed.pop("definition")
            defs.append({"id": def_id, "name": dname, "path": "\\", "type": "build", "queueStatus": "enabled",
                         "latestBuild": latest, "latestCompletedBuild": completed})
        if cd == "release":
            def_id += 1
            rname = f"{PREFIX}{name}_Container-CD"
            rdefs.append({"id": def_id, "name": rname, "path": "\\"})
            values = []
            for k in range(8):
                status = "succeeded" if k else "inProgress"
                when = NOW - timedelta(hours=9 * k + 1)
                values.append({
                    "id": 900 + k, "deploymentStatus": status, "operationStatus": "Approved",
                    "queuedOn": iso(when), "startedOn": iso(when + timedelta(seconds=30)),
                    "completedOn": None if status == "inProgress" else iso(when + timedelta(minutes=6)),
                    "requestedFor": {"displayName": rnd.choice(PEOPLE)},
                    "release": {"id": 300 + k, "name": f"Release-{120 - k}",
                                "artifacts": [{"definitionReference": {"branch": {"name": "refs/heads/master"}}}]},
                    "releaseDefinition": {"id": def_id, "name": rname},
                    "releaseEnvironment": {"name": ["Dev", "Test", "Prod"][k % 3]}})
            deps[str(def_id)] = {"count": len(values), "value": values}

    # Diğer projelerden gelmiş, öneke uymayan bir repo (listelenmemeli)
    repos.append({"id": "ffffffff-0000-4000-8000-000000000000", "name": "Baska.Ekip.Uygulama",
                  "defaultBranch": "refs/heads/main", "project": {"name": "Halkbank"}})

    timeline_default = {"records": [
        {"id": f"t{n}", "type": "Task", "name": step, "order": n, "state": "completed", "result": res,
         "startTime": iso(NOW - timedelta(minutes=20 - n)), "finishTime": iso(NOW - timedelta(minutes=19 - n)),
         "log": {"id": n}, "issues": [{"type": "error", "message": "Process completed with exit code 1."}] if res == "failed" else []}
        for n, (step, res) in enumerate([("Initialize job", "succeeded"), ("Checkout", "succeeded"),
                                         ("Python kurulumu", "succeeded"), ("Bağımlılıklar", "succeeded"),
                                         ("Testler", "succeeded"), ("Docker build", "succeeded"),
                                         ("Docker push", "succeeded"), ("Imaj taraması", "failed"),
                                         ("Finalize Job", "succeeded")], start=1)]}
    ok_timeline = json.loads(json.dumps(timeline_default))
    for r in ok_timeline["records"]:
        r["result"], r["issues"] = "succeeded", []

    devops = OUT / "devops"
    devops.mkdir(parents=True, exist_ok=True)
    files = {
        "connection": {"authenticatedUser": {"providerDisplayName": "Demo Kullanıcı"}},
        "repositories": {"count": len(repos), "value": repos},
        "build_definitions": {"count": len(defs), "value": defs},
        "builds": builds,
        "timelines": {"default": ok_timeline, **{str(b["id"]): timeline_default for v in builds.values()
                                                  for b in v["value"] if b.get("result") == "failed"}},
        "release_definitions": {"count": len(rdefs), "value": rdefs},
        "deployments": deps,
        "commits": commits,
        "pullrequests": prs,
    }
    for name, data in files.items():
        (devops / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------- Kubernetes (gerçek API JSON biçimi, camelCase) ----------------
    deployments, pods, ingresses, services, events = [], [], [], [], []
    for i, (name, *_rest) in enumerate(APPS, start=1):
        if name == "SubeAnaliz":
            continue   # hiç aktarılmamış uygulama
        app = name.lower()
        replicas = 2 if i % 2 else 1
        ready = replicas if name != "KrediSkor" else 0
        image = f"registry.local/verianalitigi/{app}:1.{i}.{rnd.randint(0, 20)}"
        labels = {"app": app}
        deployments.append({"metadata": {"name": app, "namespace": "verianalitigi", "labels": labels,
                                          "creationTimestamp": iso(NOW - timedelta(days=30 + i))},
                            "spec": {"replicas": replicas, "selector": {"matchLabels": labels},
                                     "template": {"spec": {"containers": [{"name": app, "image": image}]}}},
                            "status": {"replicas": replicas, "readyReplicas": ready, "availableReplicas": ready,
                                       "updatedReplicas": replicas}})
        services.append({"metadata": {"name": f"{app}-svc"}, "spec": {"selector": labels}})
        ingresses.append({"metadata": {"name": f"{app}-ing"},
                          "spec": {"rules": [{"host": f"{app}.dk8s.gentest.hqtest.local",
                                              "http": {"paths": [{"backend": {"service": {"name": f"{app}-svc"}}}]}}]}})
        for r in range(replicas):
            pod = f"{app}-{rnd.getrandbits(32):08x}-{rnd.getrandbits(20):05x}"
            crash = name == "KrediSkor"
            pods.append({
                "metadata": {"name": pod, "namespace": "verianalitigi", "labels": labels,
                             "creationTimestamp": iso(NOW - timedelta(hours=rnd.randint(2, 200)))},
                "spec": {"nodeName": f"node-{rnd.randint(1, 4)}", "containers": [{"name": app, "image": image}]},
                "status": {"phase": "Running", "podIP": f"10.0.{i}.{10 + r}",
                           "containerStatuses": [{
                               "name": app, "image": image, "ready": not crash, "restartCount": 17 if crash else rnd.randint(0, 1),
                               "state": {"waiting": {"reason": "CrashLoopBackOff"}} if crash else
                               {"running": {"startedAt": iso(NOW - timedelta(hours=3))}}}]}})
            if crash:
                events.append({"metadata": {"name": f"{pod}.ev"}, "involvedObject": {"kind": "Pod", "name": pod},
                               "type": "Warning", "reason": "BackOff", "count": 63,
                               "message": "Back-off restarting failed container",
                               "lastTimestamp": iso(NOW - timedelta(minutes=2))})
            events.append({"metadata": {"name": f"{pod}.pulled"}, "involvedObject": {"kind": "Pod", "name": pod},
                           "type": "Normal", "reason": "Pulled", "count": 1,
                           "message": f"Container image \"{image}\" already present on machine",
                           "lastTimestamp": iso(NOW - timedelta(hours=3))})
    k8s = {"deployments": {"items": deployments}, "pods": {"items": pods}, "ingresses": {"items": ingresses},
           "services": {"items": services}, "events": {"items": events}}
    (OUT / "k8s.json").write_text(json.dumps(k8s, ensure_ascii=False, indent=1), encoding="utf-8")
    print("sample_data üretildi")


if __name__ == "__main__":
    main()
