<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Andexor Network, Inc.
Author: Ed Jenkins
-->

# Notes to review later

## Workers AI models: findings from feature 011 (2026-11-24)

Saved from the report on feature 011 (issue #33), for review later.

**Decided on 2026-11-24:** no changes to the models for now. See how they behave in production first, then review these findings.

**Changed since the report:** the page limit is to become 16 (`specs/019-research-max-pages/spec.md`). It is the stored setting `ai.research.max_pages`, changed by a database migration. The number in code stays 8 and is used only when the setting is missing.

### How it works

- **Research reads the company's own website.** These models cannot search, so the API fetches the home page, then the about, services, product, and contact pages it links to on the same site. The cap is the setting `ai.research.max_pages`, 8 by default.
- **Pages are cut to fit each model.** Llama holds 24,000 tokens, so it gets less page text than the other two. I also cap page text at 40,000 tokens for every model, because input is billed.
- **One adapter serves all three models.** They all answer in the same shape, so there is no per-model code. Scoring and the facts list reuse Claude's prompts and JSON shapes.
- **Page text is treated as data.** It goes between tags, and the prompt says it is not instructions and tells the model not to add what it remembers from elsewhere.

### Real runs on the preview

One test company (Cloudflare's website), research then scoring:

| Model | Research | Scoring | Tokens in | Tokens out | Estimated cost |
| --- | --- | --- | --- | --- | --- |
| gpt-oss-120b | 22 s | 10 s | 8,028 | 3,548 | $0.006 |
| GLM-5.3 | 86 s | 44 s | 7,633 | 25,313 | $0.12 |
| Llama 3.3 70B | 40 s | 1 s | 6,579 | 1,344 | $0.005 |

- **Cost of all my testing was about $0.18**, estimated from token counts at catalog prices. Claude's one run in feature 010 was about $0.21.
- **GLM-5.3 is the expensive one.** It reasons at its highest effort by default, and nearly all its cost is reasoning.
- **Llama was poor.** It filled most facts with "not provided" and answered no rubric question. I now drop such empty facts for every model.
- **gpt-oss-120b varied between two runs.** It recommended "Maybe" once and "Reach out" once for the same company, on only two or three answers. Claude said "Skip".

### Discovery D4 is answered

- The `AI` binding works from Python and returns a Python dict.
- Context windows are 128,000 tokens (gpt-oss-120b), 1,048,576 (GLM-5.3), and 24,000 (Llama).
- GLM's `web_search_options` is accepted but does nothing: the model said it cannot browse. It is not used.

### Verified

- The type check has no errors and the 99 unit tests pass, with canned pages and replies and no network call.
- The preview smoke test passes all 97 calls and calls no real model.
- A website that cannot be reached fails the run in under a second with a clear message, before any model is called.

### Decision for you

Should I lower GLM-5.3's reasoning effort? It would likely cut its cost and time several times over, but it needs a setting that only that model has, and it changes what you are comparing. I left the model's default in place.

Answer on 2026-11-24: not now. Leave the models as they are until they have been seen in production.

### Not verified

- A small company's website. Every real run read one large company's site, which is not your typical prospect.
- A site built entirely by JavaScript gives the model little, because pages are not rendered.
- A refusal and a cut-off answer from a real model, which are covered by canned replies only.
- The pipeline's token deploying with the `AI` binding. The PR check will show it.

### Found later the same day

- **Some websites refuse the Worker.** One test site answered normally from a desktop and answered 403 to the same request from the Worker. A company with such a site cannot be researched by the Workers AI models; the run fails in under a second and says no page could be read. The Worker log now names each skipped page and why (`carmen_page_skipped`).
- **16 pages is live.** The setting was changed by a migration, applied to the preview and production. One run on a site with many pages took 64 seconds and 17,696 input tokens with gpt-oss-120b, against about 5,300 on a site with few pages.

Spec: [specs/011-ai-adapter-workers-ai/spec.md](specs/011-ai-adapter-workers-ai/spec.md). The page reader is in [src/pages.py](src/pages.py).

## Websites that refuse the Worker: the 403 test (2026-11-24)

Saved from the answer to two questions: why a server would block Cloudflare Workers, and whether the 403 was rate limiting.

The delay made no difference: all four spaced attempts from the Worker got the same 403.

### The test

I fetched `https://www.python.org` from the preview Worker four times, with random waits of 8, 17, and 22 seconds between them. Each attempt was refused with `status 403, content type none`.

Two things in the evidence argue against rate limiting:

- **The very first request is refused.** Each run asks for the home page once and stops when it is refused. There was no burst of requests to trip a limit.
- **A long gap did not help either.** These attempts came about two hours after my earlier ones, and the answer was the same.

One thing I cannot rule out: Workers share outgoing addresses with other Cloudflare customers. The site could be limiting those addresses because of everyone's traffic combined, and a delay on our side would not change that.

A delay between pages would only matter for a site that accepts the first page and then refuses later ones. I have not seen that happen, so I did not add a delay to the code.

### Why a server would block Workers

These are the usual reasons; I do not know which one this site has.

- **Anyone can run code there cheaply.** A Worker is free or nearly free to start, so scrapers, credential-stuffing bots, and attack tools use them. Blocking the whole platform removes a lot of abuse in one rule.
- **The traffic is not from people.** Requests from a hosting or cloud network are almost never a person in a browser. Many firewalls refuse or challenge whole cloud networks by default.
- **Workers announce themselves.** Cloudflare adds a `CF-Worker` header to every outgoing request from a Worker, naming the zone that owns it. A site can refuse on that header alone, and our code cannot remove it.
- **AI scraping.** Many sites now block automated readers that feed pages to models, which is exactly what this is.
- **Competing networks.** That site sits behind a different delivery network from Cloudflare. Such networks commonly ship rule sets that refuse traffic from other clouds.

### What this means for you

A prospect whose site is behind such a firewall cannot be researched by the Workers AI models. Claude is unaffected, because it searches through Anthropic's servers and not through the Worker. The run fails in under a second, costs nothing, and the Worker log names the page and the 403.

I did not try changing the User-Agent, which is the one thing on our side that could matter; say so if you want that tested.

### Related facts from the same day

- The 403 was not caused by `robots.txt`. A server does not enforce that file, the page reader does not read it, and that site's file allows the home page.
- Sites tried from the Worker: `www.cloudflare.com` and `www.mozilla.org` were read; `www.python.org` answered 403 on its home page. Three sites are too few to say how often a real prospect's site will refuse.
- Open choice, not built: have the page reader obey `robots.txt`.
- Built afterward (issue #35, `specs/020-page-fetch-reasons/spec.md`): the run's failure message now names the page and the reason, such as that the site answered 403, and a run that read only some pages says so on the company's timeline.

## Jev (TypeSafe): findings from feature 012 (2026-11-24)

Saved from testing the TypeSafe adapter (issue #37), for review later. No threshold was tuned.

- **Jev is fast and nearly free.** One scoring run of the whole rubric took 0.5 seconds and 2,222 input tokens, about $0.0001. The other models took 1 to 44 seconds to score.
- **It answers only what the material settles.** A yes-or-no question is YES at 0.7 or more and NO at 0.3 or less; between them it stays unknown. In one run, 8 of 16 yes-or-no questions were settled and 8 were not.
- **Ratings and choices were all left unknown.** Each has a companion question, "is there enough information to answer this?", and it must reach 0.7. In the one real run all four were under it.
- **The companion question looks strict.** Jev picked "an in-house team" with probability 1.00 and gave its companion 0.37; it picked the top search visibility with 0.84 and gave its companion 0.21. A person would call both answerable for that company. To review later: lower the companion threshold, reword the companion, or drop it for a choice whose top option is near 1.00.
- **Some settled answers rest on thin evidence.** Jev said YES to "Is the site fast?" at 0.83 from a dossier that was written from page text alone. The probability is stored with each answer, so weak answers can be seen.
- **All ratings share six general levels**, from "not at all" to "completely". Levels written for each criterion would likely help.
- **Built the same day: weak evidence is now marked.** Every question has a companion, and each Jev answer's rationale holds its numbers, such as "Jev: yes 0.85. Enough information to answer: 0.23 (weak evidence)." In the next real run, 7 of the 9 yes-or-no answers kept were marked weak evidence, including "Is the site secure?" and "Is the site reliably up?". To review later: whether a yes-or-no answer with weak evidence should be left unknown, as ratings and choices are. Today it is kept and marked.
- **Where to look:** every judgment of a run, the unsettled ones included, is in the Worker log as `carmen_typesafe_judgments`.

## Questions that need measuring, not judging (2026-11-24)

Saved from the discussion of what the rubric questions mean, for review later.

- **"Is the site fast?" now has a rule.** It is YES when the PageSpeed score for mobile is 90 or more and NO below, following Google (90 or above is good, 50 to 89 needs improvement, below 50 is poor). Code answers it from the fact `pagespeed_mobile`, and no model is asked it any more (`specs/021-measured-criteria/spec.md`).
- **Nothing measures PageSpeed yet.** Until something calls Google's PageSpeed service, the fact exists only when a person enters it, so the question is usually unknown. AGS will measure it (see the decision of 2026-11-27 below).
- **"Is the site reliably up?" cannot be answered without monitoring.** One visit shows the site was up for a moment. A prospect's own answer is not reliable. Options: leave it unknown, reword it to what one visit can show, use outside uptime history, or monitor prospects with the scheduler of feature 015.
- **Other measurement questions with no rule yet:** "Is the site secure?" (HTTPS can be checked in one visit) and "Does the site pass Core Web Vitals on mobile?" (Google's service can measure it on demand).
- **"Is the business in metro Atlanta?" now has a definition (decided by Ed on 2026-11-24).** Metro Atlanta is the local territory: Fulton, DeKalb, Cobb, and Gwinnett counties, four of the five core counties of the Atlanta Regional Commission. An address in one of them is YES; anywhere else is NO. It is in the question's help text, which every model is given. Strong evidence is still a city on the company record, a `location` fact, or an address in the dossier.
- **Most other questions still have no stated criteria.**
- **Fixed the same day: the direction of Need** (issue #38, `specs/022-need-direction/spec.md`). A YES on a yes-or-no Need question used to raise the Need score, so a company with an excellent website scored a Need of 100. The scoring code now makes Need the share of points not earned, for every Need question, and the same company scores 0. Every Need score computed before this was the wrong way round for the 14 yes-or-no questions, so the earlier test results in these notes ("Need 100") reflect the old arithmetic.

## Public sources for a prospect's uptime and speed (researched 2026-11-24)

Saved for later; nothing here is built. The question was which public monitoring and "is it down" sites could tell us about a prospect's website, and whether each has an API or pages regular enough for a bot.

**Main finding:** no public site keeps an uptime history for an arbitrary small business website. The "is it down" sites check a site at the moment you ask, which the API can do itself. History exists only where someone was already watching: the company's own status page, crowd reports for big brands, or the Internet Archive's captures.

| Source | What it knows | Access | Covers small prospects? |
| --- | --- | --- | --- |
| The company's own status page (Atlassian Statuspage and similar) | Current status, components, recent incidents | Public JSON with no key: `/api/v2/summary.json`, `status.json`, `incidents.json` on the status page's host. Checked on one page | Rarely. Mid-sized and large tech companies |
| StatusGator | Collects thousands of official status pages, with history | REST API v3 on every plan, the free one included; history length depends on the plan | No. Vendors and services, not arbitrary sites |
| Downdetector | Outage reports from the public, for well-known brands | API on paid plans only, by third-party reports (prices unclear and conflicting). The site answered 403 to an automated request | No. Large consumer brands |
| IsItDownRightNow | A live check, the response time, and "last down" | No documented API. A regular HTML fragment at `check.php?domain=`. Using it is scraping; read their terms first | Any site, but almost no history |
| Down for Everyone or Just Me, and similar | A live check | No API found | Any site, no history |
| Internet Archive Wayback Machine, CDX API | Every capture of a site with its HTTP status, back many years | Free JSON API, no key. Checked | Yes, most sites have captures, but few per month. A weak signal |
| Google PageSpeed Insights API | The PageSpeed score, measured on demand | Free; a key is recommended for automated use | Yes, any site. This is what could fill `pagespeed_mobile` |
| Google Chrome UX Report (CrUX) API | Real visitors' Core Web Vitals, with weekly history | Free, 150 queries a minute | Only sites with enough Chrome traffic. Many small sites are absent |
| Cloudflare Radar URL Scanner | A scan of a page on demand | API | Any site, no uptime history |

**What this suggests:**

- For "Is the site reliably up?", a prospect's own status page is the only good public evidence, and few prospects have one. The Wayback Machine is a weak second. A real answer needs our own monitoring: Grafana for sites we manage, and a scheduled check for prospects.
- Two other measurement questions can be answered today from Google with no monitoring: "Is the site fast?" from PageSpeed Insights, and "Does the site pass Core Web Vitals on mobile?" from CrUX where the site has enough traffic, or from PageSpeed Insights otherwise.
- Before any bot reads a site that has no API, check that site's terms. Downdetector already refuses automated requests.

Not checked: each site's terms of use, current prices, and how many real prospects appear in any of these.

## "How active is their marketing today?": how it is calculated (2026-11-24)

Saved from an answer given during feature 012, for review later.

**Changed since the answer:** step 4 mentions an "invert" setting. That setting has been removed. The scoring code now turns a good answer into low need for every Need question, so the result is the same: very active marketing means little need.

It is not calculated from data. It is a judgment on a 0 to 5 scale, turned into points by code.

1. **The question** is a rating: "How active is their marketing today? (0 = none, 5 = very active)". It belongs to the Need section for the Growth Marketing service, with a weight of 3.
2. **Jev picks a level.** It is given six general levels, from "not at all" to "completely", and returns a probability for each. In the test run the most likely level was 4, at 0.56.
3. **The companion decides whether to keep it.** It came back at 0.73, just over the 0.7 needed, so the answer was kept.
4. **Code turns the level into points.** Level 4 of 5 is 0.8. This question is set to "invert", so the points become 0.2.
5. **The points feed the Need score** for Growth Marketing: very active marketing means little need, and no marketing means a strong need.

What Jev based level 4 on: the dossier described the site as "marketing-heavy" with many calls to action. Nothing counted ads, posts, or campaigns. The linked fact, `marketing_activity`, can hold a rating that a person enters from what the customer says and what you observe, but no code fills it in.

## Decisions to revisit

None is open. "Is the site reliably up?" is measured by the uptime checker since 2026-11-26 (`specs/026-uptime-checker/spec.md`).

## Ideas parked for later

- **A flag for how an answer was obtained (asked for on 2026-11-24).** Whether an answer came from Ed observing the site, from Ed asking the prospect, or from automated means. Today an answer records only MANUAL or AI. Not built; it needs a change to the data model.
- **Automate "Is the business in metro Atlanta?" (asked for on 2026-11-24, for later).** Today the definition (Fulton, DeKalb, Cobb, and Gwinnett counties) is a help text, so a person or a model still has to work out the county. Ed does not want to depend only on that hint: code should also answer the question from the business's ZIP code, or its city and state. The help text stays; the automation is added to it, not put in its place (clarified by Ed on 2026-11-24). Not now. It would need a list of which ZIP codes or cities are in the four counties, and it fits the measured-criteria mechanism, where code answers a question from a fact and no model is asked.
- **Third-party uptime sources (asked for on 2026-11-24, for later).** Left alone for now. When this is taken up, consider only the sources that have an API, and leave out the ones that do not. From the research above, with an API: a company's own status page (public JSON, no key), StatusGator (REST API on every plan), the Internet Archive's Wayback Machine (free JSON API), and Downdetector (API on paid plans only). Without an API, so excluded: IsItDownRightNow, and Down for Everyone or Just Me and similar sites.
- **Use Google's PageSpeed Insights API and Chrome UX Report (CrUX) API (decided 2026-11-24, for later).** Ed wants both. PageSpeed Insights measures any site on demand and would fill the fact `pagespeed_mobile`, which the "Is the site fast?" rule already reads. CrUX gives real visitors' Core Web Vitals and would answer "Does the site pass Core Web Vitals on mobile?" where the site has enough traffic. Both are free; PageSpeed Insights needs an API key in practice (a call without one was refused), kept in the Secrets Store. To be done after the backlog is finished and shipped. The steps for creating the PageSpeed key and where to store it are in `MANUAL-TASKS.md`, under "Google PageSpeed Insights key (for later)".
- **PageSpeed is measured by Andexor Grass Spider (AGS), not by Carmen (decided 2026-11-27).** This changes the entry above for PageSpeed Insights: it is a technical analysis, so it belongs in AGS. AGS holds the Google key; Carmen gets no key and makes no call to Google. The flow, once AGS has the feature: Carmen sends a message to a queue that asks AGS for a site's PageSpeed data. If AGS already has the data, it posts it to Carmen with an API call. If not, it calls PageSpeed Insights first and then posts it. Carmen's receiving side exists: the machine method `add_facts` and the fact `pagespeed_mobile`, which the "Is the site fast?" rule reads. Still to design when Ed says AGS is ready: the queue, the shape of the request, and when Carmen asks. Until then the fact exists only when a person enters it. Nothing was said about CrUX, so that part of the entry above stands.
- **`andexor-net-website-ping` is not for uptime (Ed, 2026-11-24).** I had asked whether it could be used to monitor prospects. The answer is no. That project monitors clicks from the Andexor Network website. Its purpose, eventually, is to compare those clicks with other monitoring data such as Grafana metrics, to tell a bot that scrapes a page or the whole site from a human who clicks on what they want to see.

**Order of work (Ed, 2026-11-24):** finish the backlog and ship what we have first. Then go through everything under "Decisions to revisit" and "Ideas parked for later".
- **Branded HTML email (asked for on 2026-11-24, for later).** Ed wants the digest and the alerts sent as HTML in a branded template. Not now. Today every message is plain text from a file in `src/templates/email/` (feature 015), and the `send_email` binding already takes an `html` field beside `text`.
