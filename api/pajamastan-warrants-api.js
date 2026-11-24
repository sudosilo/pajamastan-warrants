// Pulls the Mohave County courts warrant search for every letter and returns the combined docket.
// The response is cached at the edge for three hours so the courts site is hit a few times a day at most.
const SOURCE = "https://www.mohavecourts.com/search/warrant";
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
const wait = ms => new Promise(r => setTimeout(r, ms));

async function letter(l) {
  const r = await fetch(SOURCE + "?first_name=&last_name=" + l, { headers: { "Accept": "application/json" } });
  if (!r.ok) throw new Error("courts site answered " + r.status);
  let body = await r.json();
  if (typeof body === "string") body = body === "null" ? [] : JSON.parse(body);
  return Array.isArray(body) ? body : [];
}

function dayStamp(d) {
  const t = Date.parse((d || "") + "T12:00:00-07:00");
  return isNaN(t) ? 0 : Math.floor(t / 1000);
}

module.exports = async (req, res) => {
  const found = {};
  try {
    // Four searches run at a time so the whole alphabet finishes well inside the time limit.
    const queue = LETTERS.slice();
    async function worker() {
      while (queue.length) {
        const l = queue.shift();
        for (const c of await letter(l)) {
          const w = { court: c[0] || "", last: c[1] || "", first: c[2] || "", middle: c[3] || "", warrant_date: c[5] || "", case: c[6] || "" };
          if (!w.case) continue;
          w.first_seen = dayStamp(w.warrant_date);
          found[w.case + "|" + w.last + "|" + w.first] = w;
        }
        await wait(200);
      }
    }
    await Promise.all([worker(), worker(), worker(), worker()]);
  } catch (e) {
    res.setHeader("Cache-Control", "no-store");
    return res.status(502).json({ error: e.message });
  }
  const list = Object.values(found).sort((a, b) => b.first_seen - a.first_seen);
  // Compact rows keep the response under the size limit: [court index, last, first, middle, warrant date, case]
  const courts = [...new Set(list.map(w => w.court))];
  const rows = list.map(w => [courts.indexOf(w.court), w.last, w.first, w.middle, w.warrant_date, w.case]);
  res.setHeader("Cache-Control", "public, s-maxage=10800, stale-while-revalidate=86400");
  res.status(200).json({ last_run: Math.floor(Date.now() / 1000), courts, rows });
};
