#!/usr/bin/env python3
"""A pretend device for trying the site without a game: starts a "hunt",
prints its code, meets a Dewpider every few seconds and answers the site's
buttons the way Hunt would.

    python fake_device.py http://127.0.0.1:8791 [samples/fight-menu.png]
"""
import asyncio
import base64
import io
import random
import secrets
import sys

import aiohttp

URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8791"
PICTURE = sys.argv[2] if len(sys.argv) > 2 else None


def sprite():
    """The wild Pokemon's 96 x 96 sprite area, sampled like Hunt does (the samples are reference size)."""
    from PIL import Image
    img = Image.open(PICTURE).convert("RGB")
    xs = [int(1170 + (i + .5) * 4.165) for i in range(96)]
    ys = [int(285 + (i + .5) * 4.165) for i in range(96)]
    out = Image.new("RGB", (96, 96))
    out.putdata([img.getpixel((x, y)) for y in ys for x in xs])
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def jpeg():
    from PIL import Image
    img = Image.open(PICTURE).convert("RGB").crop((300, 140, 1750, 1220))
    img.thumbnail((960, 960))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=70)
    return out.getvalue()


async def main():
    key = secrets.token_urlsafe(32)
    head = {"Authorization": f"Bearer {key}"}
    s = {"hunting": True, "running": True, "remote": False, "phase": "waiting", "count": 0,
         "balls": ["Quick Ball", "Timer Ball", "Repeat Ball"], "heal_item": "Full Restore", "pp_item": "Max Elixir",
         "moves": [], "log": [], "done": 0}
    lead = {"name": "Clodsire", "level": 109, "hp": 376, "max": 376}
    want_lead = False
    async with aiohttp.ClientSession(headers=head) as http:
        async def post_state(fresh=False):
            nonlocal want_lead
            body = dict(s, lead=dict(lead, fresh=fresh) if s["phase"] == "battle" else {})
            async with http.post(URL + "/api/device/state", json=body) as r:
                want_lead = (await r.json()).get("want_lead", False)

        async def shot():
            if PICTURE:
                await http.post(URL + "/api/device/shot", data=jpeg(), headers={"Content-Type": "image/jpeg"})

        def say(line):
            print(line)
            s["log"] = (s["log"] + [line])[-6:]

        async with http.post(URL + "/api/device/start", json={"label": "Fake phone", "kind": "test", "build": "dev"}) as r:
            print("code:", (await r.json())["code"])

        async def encounters():
            while True:
                await asyncio.sleep(4)
                if s["phase"] == "battle" and s["remote"]:
                    continue
                s["phase"] = "battle"
                s["msg"] = ""
                s["count"] += 1
                s["enc"] = {"species": "Dewpider", "level": "54", "shiny": random.random() < .2, "tint": False,
                            "reskin": random.choice(["", "WIZARD"]), "gradient": random.choice(["", "Mirage"]), "status": ""}
                lead["hp"] = random.randint(200, 376)
                say(f"#{s['count']} {s['enc']['species']} Lv54, lead HP {lead['hp']}")
                if PICTURE:
                    await http.post(URL + "/api/device/sprite", json={"count": s["count"], "species": "Dewpider",
                                                                      "png": sprite()})
                await post_state(fresh=want_lead or s["remote"])
                if s["remote"]:
                    s["moves"] = [{"name": "Yawn", "pp": 10, "max": 10}, {"name": "Protect", "pp": 10, "max": 10}]
                    await shot()
                    await post_state(fresh=True)
                    continue
                await asyncio.sleep(1.5)
                if not s["remote"]:
                    say("  ran")
                    s["phase"] = "waiting"
                    await post_state()

        async def commands():
            while True:
                async with http.get(URL + "/api/device/poll") as r:
                    got = await r.json()
                for c in got["commands"]:
                    print("command:", c)
                    cmd = c["cmd"]
                    if cmd == "takeover":
                        s["remote"] = True
                        say("<< taken over from the website")
                        if s["phase"] == "battle":
                            s["moves"] = [{"name": "Yawn", "pp": 10, "max": 10}, {"name": "Protect", "pp": 10, "max": 10}]
                            await shot()
                    elif cmd == "resume":
                        s["remote"] = False
                        say(">> handed back")
                    elif cmd == "login":
                        say(f"  website login: {c.get('result')}")
                    elif cmd == "shot":
                        await shot()
                    else:
                        s["acting"] = {"ball": f"Throwing a {c.get('name')}", "move": f"Using {c.get('name')}",
                                       "heal": "Using a Full Restore", "pp": "Using a Max Elixir",
                                       "run": "Running"}[cmd]
                        await post_state()
                        await asyncio.sleep(2)
                        s["acting"] = ""
                        s["done"] = c["seq"]
                        if cmd == "ball" and random.random() < .4:
                            s["msg"] = "caught it!"
                            s["phase"] = "waiting"
                        elif cmd == "run":
                            s["msg"] = "got away"
                            s["phase"] = "waiting"
                        else:
                            s["msg"] = {"ball": "Oh no! The Pokemon broke free!", "move": f"Clodsire used {c.get('name')}!",
                                        "heal": "Clodsire's HP was restored.", "pp": "PP was restored."}[cmd]
                            if cmd == "move":
                                for m in s["moves"]:
                                    if m["name"] == c["name"]:
                                        m["pp"] -= 1
                        await shot()
                await post_state(fresh=want_lead and s["phase"] == "battle")

        await post_state()
        await asyncio.gather(encounters(), commands())


asyncio.run(main())
