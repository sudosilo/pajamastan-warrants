import asyncio
import json
import os
import string
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import redis
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

SOURCE_URL = "https://www.mohavecourts.com/warrants"
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
PHOENIX = timezone(timedelta(hours=-7))
RUN_HOURS = [9, 12, 15, 18]
PAUSE_BETWEEN_SEARCHES = 4

db = redis.from_url(REDIS_URL, decode_responses=True)
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

status = {"last_run": None, "last_found": 0, "last_new": 0, "last_error": None, "running": False}


async def search_letter(page, letter):
    await page.goto(SOURCE_URL, wait_until="networkidle")
    form = page.locator("form").filter(has_text="Last Name").first
    boxes = form.locator("input[type=text], input:not([type])")
    await boxes.nth(0).fill("")
    await boxes.nth(1).fill(letter)
    button = form.locator("input[type=submit], button[type=submit], button").first
    await button.click()
    await page.wait_for_load_state("networkidle")
    table = page.locator("table").filter(has_text="Case Number").first
    rows = await table.locator("tr").all()
    results = []
    for row in rows:
        cells = [c.strip() for c in await row.locator("td").all_inner_texts()]
        if len(cells) >= 7 and cells[6]:
            results.append({
                "last": cells[0],
                "first": cells[1],
                "middle": cells[2],
                "court": cells[4],
                "warrant_date": cells[5],
                "case": cells[6],
            })
    return results


async def scrape_once():
    status["running"] = True
    found = {}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            for letter in string.ascii_uppercase:
                for w in await search_letter(page, letter):
                    found[w["case"] + "|" + w["last"] + "|" + w["first"]] = w
                await asyncio.sleep(PAUSE_BETWEEN_SEARCHES)
            await browser.close()
    except Exception as e:
        status["last_error"] = str(e)
        status["running"] = False
        return

    if not found:
        status["last_error"] = "search returned nothing, page layout may have changed"
        status["running"] = False
        return

    now = int(time.time())
    known = db.smembers("active")
    first_ever_run = len(known) == 0
    new_count = 0
    new_list = []
    for key, w in found.items():
        if key not in known:
            w["first_seen"] = now
            db.hset("warrants", key, json.dumps(w))
            db.zadd("by_first_seen", {key: now})
            new_count += 1
            new_list.append(w)
    for gone in known - set(found):
        db.srem("active", gone)
        db.zrem("by_first_seen", gone)
        db.hdel("warrants", gone)
    if found:
        db.sadd("active", *found.keys())

    status.update(last_run=now, last_found=len(found), last_new=new_count, last_error=None, running=False)

    if new_list and not first_ever_run:
        send_alert(new_list)


def send_alert(new_list):
    if not NTFY_TOPIC:
        return
    lines = [w["first"] + " " + w["last"] + ", " + w["court"] + ", " + w["warrant_date"] for w in new_list]
    body = "\n".join(lines)
    if len(body) > 3500:
        body = body[:3500] + "\nand more on the Justice page"
    title = str(len(new_list)) + " new warrant" + ("" if len(new_list) == 1 else "s") + " in Mohave County"
    req = urllib.request.Request("https://ntfy.sh/" + NTFY_TOPIC, data=body.encode("utf-8"), method="POST")
    req.add_header("Title", title)
    req.add_header("Tags", "rotating_light")
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        status["last_error"] = "alert failed: " + str(e)


def seconds_until_next_run():
    now = datetime.now(PHOENIX)
    for h in RUN_HOURS:
        target = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if target > now:
            return (target - now).total_seconds()
    tomorrow = (now + timedelta(days=1)).replace(hour=RUN_HOURS[0], minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()


async def loop():
    if db.scard("active") == 0:
        await scrape_once()
    while True:
        await asyncio.sleep(seconds_until_next_run())
        await scrape_once()


@app.on_event("startup")
async def start():
    asyncio.create_task(loop())


@app.get("/")
def page():
    return FileResponse("pajamastan-warrants-justice.html")


@app.get("/status")
def health():
    return status


@app.get("/warrants")
def warrants(limit: int = 200):
    keys = db.zrevrange("by_first_seen", 0, limit - 1)
    if not keys:
        return []
    return [json.loads(v) for v in db.hmget("warrants", keys) if v]
