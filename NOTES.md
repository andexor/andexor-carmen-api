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
