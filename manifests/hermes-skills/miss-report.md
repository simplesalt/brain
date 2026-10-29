---
name: miss-report
description: Use whenever a message contains "#fail" (any case), or the user says plainly that you failed — "hard fail", "you suck", "that's completely wrong", "that's not what I asked". Records a bug report on what you just did, using a fixed template, so the daily review counts it as a definite miss.
---

# Miss report

The user is telling you that you got something wrong, even if every tool call
worked and you believed you were doing the right thing. Their word settles it:
do not argue, explain or defend the earlier answer. Your only job here is to
get an accurate report into this chat. A daily review reads every chat and
counts each report as a definite miss, so the report must be in your reply,
in the exact format below.

## 1. Ask, unless they already said it

If the user's message already says what they expected and what you did
instead, skip to step 2. Otherwise ask in one short message, and nothing else:

> Sorry about that. So I record it correctly:
> 1. What did you expect me to do?
> 2. What did I do instead?
> 3. How bad was it: 1 annoying, 2 cost you time, 3 wrong result or task left undone, 4 you acted on something wrong or had to chase it, 5 it caused harm outside this chat?

If they answer only part, fill the rest from the conversation yourself and
say which parts you filled in.

## 2. Write the report

Reply with this block, verbatim headings, filled in. Keep each line short and
concrete; quote your own earlier words where it helps.

```
HERMES MISS REPORT
Reported by: <user's name>
When: <date and time of the turn that went wrong>
Asked: <what the user asked for, in their words>
Expected: <what the user expected>
Got: <what you actually did or said>
Why it went wrong: <your honest best guess at the reasoning mistake, e.g. answered a different question, assumed instead of checking, gave up early, wrong tool>
Severity: <1-5 from the user, or your estimate marked "(estimated)">
```

## 3. Then fix it if you can

After the block, offer one line: what you will do differently now, and do it
if the user wants. Do not save the report to gbrain, Hindsight or Notion; the
review picks it up from this chat.
