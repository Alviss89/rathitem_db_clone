# Downloads rAthena renewal item files and builds site/index.html
import json, os, urllib.request, datetime, yaml

BASE = "https://raw.githubusercontent.com/rathena/rathena/master/db/re/item_db_{}.yml"
FIX = {"shadowgear": "ShadowGear", "petegg": "PetEgg", "petarmor": "PetArmor", "delayconsume": "DelayConsume"}
Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

rows = []
for part in ("equip", "etc", "usable"):
    with urllib.request.urlopen(BASE.format(part)) as r:
        data = yaml.load(r.read().decode("utf-8"), Loader=Loader)
    for it in data.get("Body") or []:
        buy, sell = it.get("Buy"), it.get("Sell")
        if sell is None:
            sell = buy // 2 if buy is not None else 0  # rAthena rule: no Sell = half of Buy
        t = it.get("Type", "Etc")
        rows.append([it["Id"], it.get("Name", ""), FIX.get(t.lower(), t), it.get("Weight", 0), sell])

assert len(rows) > 10000, f"only {len(rows)} items, download probably broke"
rows.sort(key=lambda r: r[0])

page = open("template.html", encoding="utf-8").read()
page = page.replace("__DATA__", json.dumps(rows, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/"))
page = page.replace("__DATE__", datetime.date.today().strftime("%d %b %Y"))
os.makedirs("site", exist_ok=True)
open("site/index.html", "w", encoding="utf-8").write(page)
print(f"built site/index.html with {len(rows)} items")
