# Ghosts in the Code: Building a Black Box for the AI That Writes Your Software

It's 2:47 in the morning. Your phone lights up the ceiling.

Production is down. Not gently down — *spectacularly* down. And when you trace the failure back through the wreckage, you land on a service that nobody on your team remembers writing. Because nobody did. Three weeks ago, an AI agent built it overnight: fifteen thousand lines, thirty-five files, clean as a whistle, humming along right up until the moment it wasn't.

So you do the only thing left to do. You open the logs to ask the most important question in modern software:

*Why did it do that?*

And the logs say: `200 OK`.

That's it. That's all you get. The request went out, a response came back, the bytes were the right shape. The *reasoning* — the messy, brilliant, occasionally unhinged train of thought that produced fifteen thousand lines of code you now have to defend in a war room — is gone. Evaporated the instant the stream closed. You are standing in a crime scene with no witness, no camera, and a chalk outline where your understanding used to be.

Here's the thing that should keep you up at night even when production *isn't* down: every serious machine humanity builds has a black box. Airplanes have one. Race cars have one. Even your gym watch records more about your Tuesday jog than your stack records about the AI rewriting your billing system. We bolt flight recorders onto anything whose failure is expensive and whose behavior is mysterious.

AI agents are the most expensive, most mysterious thing in your building.

And they're flying blind.

---

## The one-line séance

So let's build the black box. But let's be honest about the constraint that kills most good intentions: nobody is going to adopt a debugging tool that makes their life harder *today* to maybe save them at 2:47am *someday*. If capturing the ghost costs you a single afternoon of refactoring, you won't do it. I won't either.

So the recorder has to be invisible. It has to be a thing you drop in front of your existing code without your existing code noticing.

That's the whole trick, and it fits on one line. You already talk to your AI provider through an SDK. You just change *where* the SDK points:

```python
client = OpenAI(base_url="http://localhost:8000/openai/v1", api_key="gateway-token")
```

That's the entire integration. Same SDK, same method calls, same streaming tokens arriving at the same speed. Your code thinks it's talking to OpenAI. It's actually talking to a gateway that relays every byte faithfully to OpenAI and back — while quietly keeping a perfect recording of the whole conversation on the side.

It's a dashcam. You don't change how you drive. You just, one day, have the footage.

---

## A recorder must never crash the plane

Now here's where most "let's log everything" projects quietly betray you.

The day you add logging is the day your logging becomes a way to fail. The database hiccups, and suddenly your *user-facing requests* are hanging on a write to a telemetry table. The recorder, bolted on to watch the engine, reaches over and grabs the controls.

That's unforgivable. A flight recorder that can crash the plane isn't a safety device; it's a liability with a nice logo.

So the gateway is split into two worlds that physically cannot hurt each other.

The first world — call it the **data plane** — has exactly one job: relay your request, stream the answer back, get out of the way. It never parses anything. It never waits on the database. It is dumb and fast and bulletproof on purpose.

The second world — the **telemetry plane** — is where all the interesting, fallible work happens: untangling each provider's response, extracting the reasoning, writing it down. And this world is allowed to fail. If the database is unreachable, if a parser trips over a weird payload, if the recording queue backs up — your request still completes, exactly as fast as it would have without any of this. The recording is simply dropped, counted, and forgotten.

A good witness watches everything and touches nothing. That's the design in one sentence.

There's exactly one exception, and we'll get to it, because it's the most interesting alarm in the building.

---

## So where do the ghosts go to live?

This is the real question. You've intercepted the conversation. You're holding the ghost in your hands. Now — where do you *put* it?

Let's think it through together, because the answer is less obvious than it looks, and the journey is the whole point.

First, what does one of these recordings even look like? It's a single AI interaction: who asked, what they asked, which model answered, how long it took, how many tokens it burned, what the model *said*, and — the crown jewel — what the model was *thinking* on the way there.

Simple enough. Until you remember you support five different providers, and every one of them speaks a different dialect. OpenAI hands you one shape. Anthropic, a different one. Gemini buries the model name in the *URL*. Ollama does its own thing entirely. Tomorrow one of them ships a new field, because they ship new fields the way the rest of us drink coffee.

So picture doing this the old way, in a rigid table with fixed columns. You'd sit down to design the schema and immediately drown. A column for every field every provider might ever return? Half of them sit empty in every row, like a customs form with boxes for passports from countries that don't exist yet. And the moment a provider adds a field, you're writing a migration — at 2:47am, naturally, because that's when you need the data.

You could give up and stuff the whole mess into one big text blob. Congratulations: you can now store anything and search for *nothing*.

Right? You feel the trap closing. The data is fundamentally **shape-shifting**, and you're trying to pour it into a mold.

So you reach for a database that doesn't need a mold.

---

## Hat #1: the shapeshifter

MongoDB stores documents, not rows. A document is just... the shape of the thing you actually have. The OpenAI recording can look like an OpenAI recording. The Gemini one can look like a Gemini one. The whole sprawling trajectory of a coding session — every nested bit of metadata, every tool call, the chain-of-thought, the lot — lives in a single document that looks the way the data *wants* to look.

New provider next quarter? New field next week? You just... write it. No migration. No 2:47am schema change. No empty columns staring back at you.

The first time you try to log something as gloriously irregular as an AI's train of thought, the document model stops feeling like a "NoSQL choice" and starts feeling like the only honest way to model reality. The data was always polymorphic. MongoDB just stops pretending it isn't.

That alone would justify the choice. But it's the *least* interesting reason MongoDB belongs here, and this is where the story turns.

---

## Hat #2: the librarian who reads minds

Fast-forward six months. The edge-case bug from the opening returns. You have a hunch the agent did something sketchy around database connection pooling, but you have a *million* recordings and no idea which one.

Now — how do you find it?

If your recordings are text in a log pile, you `grep`. You search for the exact words. But you don't know the exact words. The agent didn't write "I am nervous about connection pooling." It wrote three paragraphs of careful reasoning that *circled* the idea without ever using your search term. Keyword search finds matches. It doesn't find *meaning*. And meaning is the entire thing you're hunting for.

What you actually want is to walk up to a librarian and say:

> *"Find me every time the assistant hesitated about the safety of our connection pooling."*

— and have them come back with the right memories, even though you never gave them the right words.

That librarian is **vector search**, and it lives inside the same MongoDB you're already using.

Here's the quiet magic of it. As each recording is filed away, the gateway turns the reasoning into a *vector* — a long list of numbers that captures its meaning, its vibe, its semantic fingerprint. (It uses Voyage AI's code-tuned model to do it — and Voyage, fittingly, is part of the MongoDB family now, so the whole thing stays under one roof.) Two thoughts that *mean* similar things end up with similar fingerprints, even if they share not one single word.

So when you ask your question, MongoDB doesn't match letters. It matches *minds*. It hands you the moment the agent was nervous, by the shape of the nervousness.

This is the part people don't believe until they see it. You built a black box, and it turned out to be a time machine. You can stand in the present, describe a feeling, and have the database walk you to the exact moment in your codebase's past where that feeling lived.

And notice what you did *not* have to do: you did not stand up a second database. No separate vector service to deploy, no nightly job to copy your data into it, no two systems drifting out of sync at the worst possible moment. The recordings and the search over their meaning live in the same place. The librarian works in the same building as the archive.

Hold that thought. It's about to become the whole argument.

---

## Hat #3: the cache that takes out its own trash

Quick detour, because it's a small thing that tells a big story.

Some of these AI calls are identical and expensive — the same prompt, over and over, burning the same tokens to get the same answer. The obvious move is to cache them. Ask once, remember, replay for free.

But a cache you never clean is just a landfill with good intentions. Stale answers pile up forever until they become the problem instead of the solution. So now — in the classic architecture — you go bolt on *another* system, a separate cache server, with its own expiry rules and its own operational baggage.

Or. You let MongoDB hold the cache too, and you tell it one thing: *these entries expire after an hour.* And then it cleans up after itself. The milk carton has a printed expiry date and the audacity to throw itself out. No second server. No new thing to monitor. No new thing to page you.

Are you noticing a pattern yet? Good. So am I.

---

## Hat #4: the safe with a search slot

Now the exception I promised you. The one alarm that's allowed to ground the plane.

Think about what you're actually recording. Your prompts. Your proprietary code. The AI's reasoning about your most sensitive systems. This isn't telemetry — it's the literal blueprint of your business, written in plain language, sitting in a database. It is the single most valuable, most dangerous pile of text your company owns.

Encrypting it is non-negotiable. But here's the cruel little paradox that usually wins: the moment you encrypt your data, you can't search it anymore. It becomes a locked diary. Perfectly safe, perfectly useless. You can have the vault, *or* you can have the time-traveling librarian. Pick one.

Except MongoDB refuses to make you pick.

With **Queryable Encryption**, the secrets are locked *before they ever leave your server*. The database stores them, serves them, backs them up — and never, at any point, can it read them. Not the database. Not a snapshot. Not an attacker who walks off with the whole disk. Not even your cloud provider. The plaintext simply isn't there to steal.

And yet — *and yet* — you can still search it.

It's a safe with a search slot. A locked diary that can still answer "have I ever written about X?" without ever being opened. The vault and the librarian, in the same box, at the same time. Most databases will tell you that's impossible. This one just does it.

And this is the one place the gateway is allowed to slam on the brakes. Remember our rule — the recorder must never crash the plane? Encryption is the exception, on purpose. If you turn it on and it's misconfigured, the gateway *refuses to start*. It would rather ground the entire flight than risk writing one line of your crown jewels in plaintext. Everywhere else, failure is quiet and forgiving. Here, failure is loud and absolute. Because some mistakes you want to find out about at the gate, not at 2:47am.

---

## The glue is where things break

Now step back and count.

To build this on the "obvious" stack, you'd need a database for the documents. Then a separate engine for vector search, with a pipeline to keep it in sync. Then a cache server, with its own eviction story. Then a key-management system and an encryption layer you pray actually composes with all of the above. Five systems. Five things to deploy, five things to secure, five things to upgrade, and — this is the killer — *four seams* between them where your data has to hop from one system to another.

Every seam is a leak. Every integration is a 2:47am phone call waiting for its moment. Building your black box out of five separate products is like building one car out of five different kit cars: it might roll, but you'll spend the rest of your life chasing rattles in the joints.

MongoDB collapses the whole thing into one chassis. The documents, the meaning-search over them, the self-expiring cache, and the field-level encryption — same database, same driver, same query language, same deployment. Not four products stapled together. One.

That's not a feature checklist. That's *fewer things that can break*. And in a system whose entire reason for existing is "be there, reliably, on the worst night of the quarter," fewer-things-that-can-break is the only feature that ultimately matters.

---

## The point of remembering

So let's go back to 2:47am, one more time. But this time, you built the black box.

The phone still lights up. Production is still down. The mystery service is still a mystery. But now you walk up to your own database — the one that's been quietly recording every ghost that ever passed through your code — and you simply *ask*. In plain language. About a feeling. And it walks you straight to the moment the agent reasoned its way into the bug, in its own words, decrypted just for you, found by meaning instead of luck.

You don't pray to a vendor's walled garden for the context. You own it. Which means the day a better, cheaper, faster model shows up, you don't start over — you replay your entire history into the new one and it inherits everything your codebase has ever known. The ghosts work for you now.

We spent a decade learning to record *what* our code does. The age of AI demands we record *why*. The "what" was always going to fit in rigid little rows. The "why" — shape-shifting, meaning-laden, secret, and vast — was always going to need somewhere more honest to live.

It needed a place that could be a shapeshifter, a mind-reading librarian, a self-cleaning cupboard, and an unbreakable safe — all at once, all in one box.

That's the box. Go build your black box before your next 2:47am, not after it.
