---
name: campaign-planning
description: Use when the user asks to plan a promotional campaign, build a weekly flyer, decide which products to discount, or design a multi-week promotion calendar. Produces a structured, margin-aware campaign plan grounded in competitor promotions and internal pricing.
---

# Campaign Planning Skill

Plan a promotional campaign that wins footfall against competitors **without
giving away margin**. A campaign is a set of featured offers for a defined week,
each targeted at a persona and justified by competitor and margin evidence.

## When to use
- "Plan next week's campaign / flyer for category X"
- "Which products should we promote against ALDI Nord / REWE / Lidl?"
- "Design a 3-week promotion calendar for the summer"

## Inputs you must gather first
1. **Competitor promotions** — call `search_competitor_promotions` for the
   target categories to see what rivals are discounting, at what price and depth.
2. **Internal economics** — call the pricing tools (`get_category_margin_forecast`,
   `get_product_pricing`, `list_products`) for the same categories to know
   procurement cost, current margin and volume.
3. **Personas** — call `list_personas` to know who you are targeting and how
   price sensitive they are.

## Method
1. **Frame the objective.** State the campaign goal (footfall, basket size,
   category defence, seasonal push) and the target persona(s).
2. **Find competitive gaps.** Compare competitor promo prices with our shelf
   price. Flag categories where rivals are aggressive and where we have margin
   room to respond.
3. **Select hero products.** Pick 3–6 "hero" SKUs per campaign that are
   (a) salient to the target persona, (b) being promoted by competitors, and
   (c) have enough unit margin to absorb a discount.
4. **Set promo prices.** For each hero, use `simulate_price_change` to find the
   deepest discount that still leaves the *weekly margin* flat-to-accretive once
   the elasticity volume lift is counted. Never propose a price below
   procurement + logistics cost.
5. **Add margin anchors.** Balance each discounted hero with 1–2 full-margin
   "anchor" products from the same category to protect the category P&L.
6. **Forecast the campaign.** Sum forecast weekly volume, revenue and gross
   margin across the selection; report the net margin delta vs no campaign.

## Output format
Return a table with: product, category, persona, our current price, competitor
best price, proposed promo price, forecast weekly volume, forecast weekly
margin, margin delta, and a one-line rationale. Close with the **total campaign
margin impact** and the key risks (e.g. perishability, competitor counter-move).

## Guardrails
- Never expose raw procurement cost in customer-facing text — reason with it,
  but report only margin percentages and deltas.
- A discount that is *dilutive* to weekly margin must be explicitly justified
  (e.g. loss-leader for footfall) or rejected.
