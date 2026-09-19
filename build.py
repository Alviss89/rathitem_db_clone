# Downloads rAthena renewal data and builds site/index.html
import json, os, re, shutil, subprocess, tempfile, time, datetime, urllib.request, urllib.error, yaml

Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
FIX = {"shadowgear": "ShadowGear", "petegg": "PetEgg", "petarmor": "PetArmor", "delayconsume": "DelayConsume"}

def sparse_clone(url, *paths):
    d = tempfile.mkdtemp()
    run = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
    run("clone", "--depth", "1", "--filter=blob:none", "--sparse", url, ".")
    run("sparse-checkout", "set", "--no-cone", *paths)
    return d

src = sparse_clone("https://github.com/rathena/rathena.git",
                   "/db/re/item_db_*.yml", "/db/re/mob_db.yml", "/npc/re/mobs/", "/npc/re/scripts_monsters.conf")
load = lambda p: yaml.load(open(os.path.join(src, p), encoding="utf-8"), Loader=Loader).get("Body") or []

# ---- Items: [id, name, type, weight, sell]
items, aegis_to_item = [], {}
for part in ("equip", "etc", "usable"):
    for it in load(f"db/re/item_db_{part}.yml"):
        buy, sell = it.get("Buy"), it.get("Sell")
        if sell is None:
            sell = buy // 2 if buy is not None else 0  # rAthena rule: no Sell = half of Buy
        t = it.get("Type", "Etc")
        items.append([it["Id"], it.get("Name", ""), FIX.get(t.lower(), t), it.get("Weight", 0), sell])
        aegis_to_item[it["AegisName"].lower()] = it["Id"]
assert len(items) > 10000, f"only {len(items)} items, download probably broke"
items.sort(key=lambda r: r[0])
ITEM_IDS = {r[0] for r in items}

# ---- Monsters
# mobs[id] = [name, level, mvp, spawns[[map,count]], hp, size, race, element, elementLevel, boss, 0 (unused), drops[[item,rate]], mvpDrops[[item,rate]]]
mobs, aegis_to_mob, drops = {}, {}, {}
for m in load("db/re/mob_db.yml"):
    mid = m["Id"]
    mvp = 1 if m.get("MvpExp") or (m.get("Modes") or {}).get("Mvp") else 0
    boss = 1 if mvp or m.get("Class") == "Boss" else 0
    norm, mvpd = [], []
    for kind, key, out in ((0, "Drops", norm), (1, "MvpDrops", mvpd)):
        for d in m.get(key) or []:
            iid = aegis_to_item.get(str(d["Item"]).lower())
            if iid:
                out.append([iid, d["Rate"]])
                drops.setdefault(iid, []).append([mid, d["Rate"], kind])  # kind 1 = MVP reward
    mobs[mid] = [m.get("Name", m["AegisName"]), m.get("Level", 1), mvp, {}, m.get("Hp", 1),
                 m.get("Size", "Small"), m.get("Race", "Formless"), m.get("Element", "Neutral"), m.get("ElementLevel", 1),
                 boss, 0, norm, mvpd]
    aegis_to_mob[m["AegisName"].lower()] = mid

# ---- Spawns: only files turned on in scripts_monsters.conf
conf = open(os.path.join(src, "npc/re/scripts_monsters.conf"), encoding="utf-8").read()
for path in re.findall(r"^\s*npc:\s*(\S+)", conf, re.M):
    f = os.path.join(src, path)
    if not os.path.exists(f):
        continue
    for line in open(f, encoding="utf-8", errors="replace"):
        if line.lstrip().startswith("//"):
            continue
        cols = line.rstrip("\n").split("\t")
        if len(cols) < 4 or cols[1] not in ("monster", "boss_monster"):
            continue
        key, amount = (cols[3].split(",") + ["1"])[:2]
        mid = int(key) if key.strip().isdigit() else aegis_to_mob.get(key.strip().lower())
        if mid in mobs:
            spawn = mobs[mid][3]
            mp = cols[0].split(",")[0]
            spawn[mp] = spawn.get(mp, 0) + (int(amount) if amount.strip().isdigit() else 1)
for v in mobs.values():
    v[3] = sorted(v[3].items(), key=lambda x: -x[1])

# ---- Divine Pride gap fill: monsters rAthena only has as placeholders (# - Id: lines)
# Runs at most once every 28 days, 1 request per 2 seconds, results kept in dp/ in the repo.
DP_KEY = os.environ.get("DP_API_KEY", "")
DP_BASE = os.environ.get("DP_BASE", "https://www.divine-pride.net/api/database/Monster/")
DP_DIR = "dp"
os.makedirs(DP_DIR, exist_ok=True)
cache_f, meta_f = os.path.join(DP_DIR, "monsters.json"), os.path.join(DP_DIR, "meta.json")
dp_cache = json.load(open(cache_f)) if os.path.exists(cache_f) else {}   # id -> data, or {"missing": date}
meta = json.load(open(meta_f)) if os.path.exists(meta_f) else {}
mobtxt = open(os.path.join(src, "db/re/mob_db.yml"), encoding="utf-8").read()
placeholders = [int(i) for i in re.findall(r"^#\s+-\s+Id:\s*(\d+)", mobtxt, re.M)]
placeholders = sorted({i for i in placeholders if i not in mobs})
today = datetime.date.today()
last = meta.get("lastFill")
due = not last or (today - datetime.date.fromisoformat(last)).days >= 28
def want(i):
    c = dp_cache.get(str(i))
    if c is None:
        return True
    if "missing" in c:   # not on Divine Pride last time: re-check twice a year
        return (today - datetime.date.fromisoformat(c["missing"])).days >= 180
    return False
todo = [i for i in placeholders if want(i)] if (due and DP_KEY) else []
print(f"Divine Pride: {len(placeholders)} placeholders, {sum(1 for v in dp_cache.values() if 'missing' not in v)} saved, "
      f"{len(todo)} to look up" + ("" if DP_KEY else " (no DP_API_KEY, skipped)") + ("" if due else f" (not due, last fill {last})"))

def dp_get(mid):
    req = urllib.request.Request(f"{DP_BASE}{mid}?apiKey={DP_KEY}", headers={"Accept-Language": "en", "Accept": "application/json",
                                 "User-Agent": "AlvissROTools/1.0 (monthly gap fill; https://alviss89.github.io/rathitem_db_clone/)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return 200, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:   # told to slow down: wait as asked, then try once more
                wait = int(e.headers.get("Retry-After") or 60)
                print(f"  rate limited, waiting {wait}s"); time.sleep(min(wait, 600)); continue
            return e.code, None
        except Exception:
            time.sleep(10)
    return 429, None

def save_dp():
    json.dump(dp_cache, open(cache_f, "w"), separators=(",", ":"), ensure_ascii=False)
    json.dump(meta, open(meta_f, "w"), indent=1)

fetched = 0
stop_at = time.time() + 60 * float(os.environ.get("DP_MINUTES", "120"))  # time budget per run; the rest continues next run
for n, mid in enumerate(todo):
    if time.time() > stop_at:
        print(f"Divine Pride: time budget used, {len(todo) - n} left for the next run"); break
    code, d = dp_get(mid)
    time.sleep(float(os.environ.get("DP_DELAY", "2")))
    if code == 200 and d:
        dp_cache[str(mid)] = d if isinstance(d, dict) else {"missing": today.isoformat()}
        fetched += 1
    elif code == 404:
        dp_cache[str(mid)] = {"missing": today.isoformat()}
    elif code in (401, 403):
        print(f"Divine Pride refused the key (HTTP {code}); stopping"); break
    elif code == 429:
        print("Divine Pride still rate limiting after waiting; stopping for now"); break
    else:
        print(f"  {mid}: HTTP {code}, will retry next month")
    if n % 50 == 49:
        save_dp()
else:
    if todo:
        meta["lastFill"] = today.isoformat()
if todo:
    save_dp()
print(f"Divine Pride: fetched {fetched}")

RACE = {"formless": "Formless", "undead": "Undead", "brute": "Brute", "plant": "Plant", "insect": "Insect", "fish": "Fish",
        "demon": "Demon", "demihuman": "Demihuman", "angel": "Angel", "dragon": "Dragon"}
ELEM = {"neutral": "Neutral", "water": "Water", "earth": "Earth", "fire": "Fire", "wind": "Wind", "poison": "Poison",
        "holy": "Holy", "dark": "Dark", "shadow": "Dark", "ghost": "Ghost", "undead": "Undead"}
key = lambda s: re.sub(r"[^a-z]", "", str(s or "").lower())
extra_names = {}
for sid, d in dp_cache.items():
    mid = int(sid)
    if "missing" in d or mid in mobs:
        continue
    def conv(lst):
        out = []
        for x in lst or []:
            iid, p = x.get("itemId"), x.get("probability")
            if iid is None or p is None:
                continue
            out.append([iid, max(1, round(float(p) * 10000))])
            if iid not in ITEM_IDS and x.get("itemName"):
                extra_names[iid] = x["itemName"]
        return out
    norm, mvpd = conv(d.get("drops")), conv(d.get("mvpDrops"))
    spawn = {}
    for s in d.get("spawns") or []:
        if s.get("mapName"):
            spawn[s["mapName"]] = spawn.get(s["mapName"], 0) + int(s.get("quantity") or 0)
    typ = str(d.get("type") or "").lower()
    mvp = 1 if mvpd or "mvp" in typ else 0
    boss = 1 if mvp or "boss" in typ or "mini" in typ else 0
    elem = ELEM.get(key(str(d.get("element") or "Neutral").split()[0]), "Neutral")
    mobs[mid] = [d.get("name") or f"Monster {mid}", d.get("level") or 1, mvp, sorted(spawn.items(), key=lambda x: -x[1]),
                 d.get("health") or 0, str(d.get("size") or "Small").title(), RACE.get(key(d.get("race")), "Formless"),
                 elem, d.get("elementLevel") or 1, boss, 0, norm, mvpd, 1]   # last 1 = data from Divine Pride
    for kind, lst in ((0, norm), (1, mvpd)):
        for iid, rate in lst:
            drops.setdefault(iid, []).append([mid, rate, kind])

# ---- Your own names and pictures (overrides.txt)
images = {}
if os.path.exists("overrides.txt"):
    for line in open("overrides.txt", encoding="utf-8"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")] + ["", ""]
        if not parts[0].isdigit() or int(parts[0]) not in mobs:
            print(f"overrides.txt: skipped '{line.strip()}' (unknown monster ID)"); continue
        mid = int(parts[0])
        if parts[1]:
            mobs[mid][0] = parts[1]
        if parts[2]:
            images[mid] = parts[2]

data = json.dumps({"items": items, "drops": drops, "mobs": mobs, "xnames": extra_names, "img": images}, separators=(",", ":"), ensure_ascii=False)
page = open("template.html", encoding="utf-8").read()
page = page.replace("__DATA__", data.replace("</", "<\\/"))
page = page.replace("__DATE__", datetime.date.today().strftime("%d %b %Y"))
os.makedirs("site", exist_ok=True)
if os.path.isdir("img"):
    shutil.copytree("img", "site/img", dirs_exist_ok=True)
open("site/index.html", "w", encoding="utf-8").write(page)
print(f"built site/index.html: {len(items)} items, {len(mobs)} monsters, {len(page)//1024} KB")
