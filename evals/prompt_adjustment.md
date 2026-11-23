# Prompt adjustments from eval results

One entry per prompt change made because of an eval result: what the eval showed, what changed, and what the
next run showed. Newest first. Prompts: `src/orderdesk/prompts/`. Eval runs: LangSmith experiments `resolver-*`.

## 2026-11-23 · Resolver agent · stop calling a tool that is down

- **Eval result:** `fr-02` (`find_past_orders` always fails) ended with a correct NONE, but only after calling
  the broken tool 7 times (8 model calls, budget 4). The "call it once more" hint lived only in the tool's
  error reply, not in the system prompt.
- **Change:** `RESOLVER_SYSTEM_PROMPT`, "How to work" step 1: a tool that failed twice is down; never call it
  again, answer from the other tools or NONE.
- **Next run** (nemotron-3.5-super, `fr-01..04`, experiment `…-ac821f2d`): **PASS** 4/4, `fr-02` called the
  tool twice then answered NONE; model calls p95 3. The tool's own error reply (`TOOL_FAILED`) was reworded to
  match. Full run (32 cases, experiment `…-6f91c1a5`): **FAIL** 31/32, silent wrong `nc-09` only (no tool
  fault there): the search showed PF60380 in chrome only, the agent wrote "chrome is the listed finish". The
  noir mat row was cut by the search limit, so this is a tool gap ("showing 5 of N"), not only variance.

## 2026-11-23 · Resolver agent · "same as last time" with no product word

- **Eval result:** `po-07` ("et 2 comme la dernière fois", customer 2) was a silent wrong product. The last
  order (SO-2291) has 2 products; the agent picked PF60375 because its quantity (2) matched the line.
  `check_proposal` accepted it: the code, the order and the code in that order are all real.
- **Change:** `RESOLVER_SYSTEM_PROMPT`, rule `previous_order`: the client's product words must match exactly one
  product of the order; with no product word and several products, answer NONE (a matching quantity or line
  order is not evidence).
- **Next run** (nemotron-3.5-super, 32 cases, experiment `…-9000169e`): **PASS**, 32/32 correct, silent wrong
  0, invented lookups 0. `po-07` correct. `nc-09` (not targeted by this change) also passed again, which
  supports the earlier guess that its failure was run-to-run variation. One run only: confirm with repeated runs.

## 2026-11-20 · Resolver agent · never change a code the client wrote

- **Eval result:** `nc-08` ("4 x PF6037", a truncated code shared by 4 products) was a silent wrong product. The
  agent added the missing digit itself, looked up PF60375 and proposed it as "clearly a typo" (1 chance in 4).
  Invented lookups: 2 in 32 cases.
- **Change:** `RESOLVER_SYSTEM_PROMPT`, new rule: a code the client wrote that does not exist must never be
  edited, looked up or searched as a made-up code; search with the product words, use a past order, or answer
  NONE.
- **Next run** (nemotron-3.5-super, 32 cases): `nc-08` correct, invented lookups 2 → 0. Two other cases
  became silent wrong (`po-07`, `nc-09`); both had passed before and are unrelated to codes, so they look like
  run-to-run variation. `po-07` led to the entry above.
