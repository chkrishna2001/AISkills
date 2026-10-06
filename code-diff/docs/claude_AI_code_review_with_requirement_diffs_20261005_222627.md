---
title: "AI code review with requirement diffs"
source: https://claude.ai/chat/f09654a7-0da8-4d79-b995-138f556c926e
platform: CLAUDE
exportDate: 10/5/2026, 10:26:27 PM
---

# AI code review with requirement diffs

*Exported from [CLAUDE](https://claude.ai/chat/f09654a7-0da8-4d79-b995-138f556c926e)*

---

### 🧑 **User**

everyday people are using AI tools like claude,cursor,codex to write code, since ai writes code very fast people are unable to review it properly. sometimes we ask it to write some code and it might have deviated a little bit but it could be a bigger architectural shift humans maynot notice and it has to be fixed later. 
what if agent can provide a diff in such a way it can tie code change with thte requirement and layout everything in layman terms so developers can understand it properly and take decisions ?

---

### 🤖 **Assistant**

Exploring tools for explaining requirement-traceable diffs simply.

This is a real problem, and you've put your finger on the important part. The danger isn't bad code in a single line, which linters and tests catch. It's  **intent drift** : the agent quietly solves a slightly different problem than you asked for, and the diff looks reasonable line by line, so nobody notices.

 

### Why today's diffs fail here

 

A normal diff answers "what lines changed?" A reviewer actually needs answers to different questions:

 
 - Which part of my request does each change serve?
 - What did it do that I  *didn't*  ask for?
 - Did it change how the system is structured, not just what it does?
 - What decisions did it make on my behalf?
 
 

AI made writing code 10x faster but left reviewing at 1x, so this gap keeps widening.

 

### What your idea could look like

 

Think of it as a  **"Change Contract"**  the agent produces alongside the diff:

 

```
REQUIREMENT: "Add CSV export to the reports page"

✅ Mapped changes (what you asked for)
  R1 Export button on reports page   → ReportPage.tsx (+24 lines)
  R2 Generate CSV from report data   → csvExporter.ts (new file)

⚠️ Unrequested changes (agent's own decisions)
  • Moved report data fetching from the page into a new shared
    service, ReportService.ts.
    Plain English: "Before, each page got its own data. Now all
    pages go through one central place."
    Why it matters: 4 other pages now depend on this new service.
    Your call: [Keep] [Revert to original structure] [Discuss]

🏗️ Architecture impact: MEDIUM
  • New dependency: ReportPage → ReportService → API client
  • No database or API contract changes

❓ Assumptions the agent made
  • Large reports (>10k rows) are exported in one go, with no streaming
  • Dates are formatted as YYYY-MM-DD
```

 

The core features would be:

 
 1. **Requirement traceability.**  Every hunk links back to a specific requirement, or gets flagged as "orphan."
 2. **Drift detection.**  Compare the change against the existing architecture (module boundaries, dependency graph, patterns) and call out structural shifts separately from feature work.
 3. **Plain-language explanations**  written as cause and consequence ("now X depends on Y"), not as a restatement of the code.
 4. **Explicit decision points.**  Turn hidden choices into things the human approves or rejects.
 5. **Risk-tiered review.**  Tell the developer where to spend their 10 minutes of attention.
 
 

### The hard parts (where the real product is)

 
 - **Requirements are fuzzy.**  A one-line prompt or vague Jira ticket is a weak anchor. You may need to have the agent restate the requirement as a checklist  *before*  coding and get it confirmed, so there's something concrete to trace against.
 - **The explainer can't be the author.**  If the same agent writes the code and explains it, it will rationalize its own drift. A separate, independent reviewer pass that compares intent with the actual diff is much more trustworthy.
 - **Knowing what "architecture" means for a codebase.**  Drift detection needs a baseline: dependency graphs, layer rules, or an architecture doc that's kept current.
 - **Noise.**  If every PR flags 15 "decisions," people stop reading. Ranking matters more than completeness.
 
 

### Competitive landscape

 

As far as I know, tools like CodeRabbit, Qodo (which has a ticket-compliance check against Jira), Greptile, and GitHub Copilot's PR summaries cover pieces of this. They mostly  *summarize*  diffs, though. The gaps that still seem open are  **orphan-change and architecture-drift detection framed as decisions for the human** , and doing it  *at agent-time* , before a PR even exists, inside Claude Code, Cursor, or Codex sessions. The market moves fast, so it's worth a proper scan before building.

 

### A crisp pitch

 

*"AI writes code faster than humans can review it. [Product] shows you not what changed, but  **why** : every change mapped to your requirement, every unrequested decision surfaced, every architectural shift explained in plain English, before it becomes tech debt."*

 

This also fits naturally next to data-validation thinking: it's essentially  *validation of intent*  rather than validation of data.

 

I can take this further in one of three directions: a competitive scan of current tools, a prototype (for example, a Claude Code skill or hook that generates this Change Contract after each task), or a short concept deck. Which would be most useful?

---

### 🧑 **User**

a skill is more than enough for this or this idea has a potential of product ?

---

### 🤖 **Assistant**

**Start as a skill, but the idea is bigger than a skill.**  Those two things aren't in conflict; the skill is how you find out whether the product is real.

 

### What a skill can do well

 

A skill (in Claude Code, Cursor rules, or similar) can get you about 70% of the visible value quickly:

 
 - Make the agent restate the requirement as a checklist before coding.
 - After coding, produce the Change Contract: mapped changes, orphan changes, assumptions, plain-English impact.
 - Ask the human to approve flagged decisions.
 
 

You could build this in a weekend and use it on real work. If it changes how you review, that's strong signal.

 

### Where a skill hits its limits

 

These are the parts that make it a product:

 
 1. **The author grades its own homework.**  A skill runs inside the same agent that wrote the code, so it tends to explain away its own drift. A product runs as an  *independent*  reviewer, which is a fundamentally more trustworthy design.
 2. **No architectural memory.**  A skill sees one session. Real drift detection needs a persistent model of the codebase (dependency graph, layer rules, past decisions) and needs to compare each change against it over time.
 3. **Agent-agnostic.**  Teams use Claude Code, Cursor, Codex, and Copilot all at once. A skill is tied to one tool. A product sits at the PR or Git layer and works no matter which agent wrote the code.
 4. **Requirement sources.**  Pulling intent from Jira, Linear, specs, and Slack threads, and keeping traceability over time, is integration work a skill can't carry.
 5. **Team and audit value.**  Leads want to see "how much unrequested change is landing in our codebase each week?" Regulated industries (banking, healthcare) may need  *proof*  that AI-written changes match approved requirements. That's dashboards, history, and compliance, which means a product, and that's what people pay for.
 
 

### The honest risks

 
 - **Platform risk is high.**  Anthropic, GitHub, Cursor, and others could ship "explain the diff against the task" as a built-in feature. A thin version of this idea  *will*  get commoditized.
 - **Crowded neighborhood.**  AI code-review tools are already abundant, so "better PR summaries" won't stand out.
 - **The defensible part**  is the hard stuff: architecture-baseline drift detection, requirement traceability across tools, and the audit/governance angle. If you build a product, that's where the moat is.
 
 

### Suggested path

 
 1. **Build the skill**  and use it on your own and colleagues' work for 2–3 weeks.
 2. **Watch one metric:**  how often does it catch an unrequested or architectural change that a human would have missed? If the answer is "regularly," you have a product signal.
 3. **If yes, position the product around governance** , along the lines of  *"trust and traceability for AI-written code."*  Selling to engineering leads and compliance teams is a stronger business than selling to individual developers, who expect such features for free.
 
 

Want me to draft the skill so you can start testing it?

9 hours ago

---

