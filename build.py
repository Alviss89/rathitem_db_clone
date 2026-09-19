# Downloads rAthena renewal data and builds site/index.html
import json, os, re, subprocess, tempfile, datetime, yaml

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

data = json.dumps({"items": items, "drops": drops, "mobs": mobs}, separators=(",", ":"), ensure_ascii=False)
page = open("template.html", encoding="utf-8").read()
page = page.replace("__DATA__", data.replace("</", "<\\/"))
page = page.replace("__DATE__", datetime.date.today().strftime("%d %b %Y"))
os.makedirs("site", exist_ok=True)
open("site/index.html", "w", encoding="utf-8").write(page)
print(f"built site/index.html: {len(items)} items, {len(mobs)} monsters, {len(page)//1024} KB")
