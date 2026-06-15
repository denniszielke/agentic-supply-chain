---
name: pricing-optimization
description: Use when the user asks to optimise the price of a product or category, find the margin-maximising price, decide how deep a discount can go, or respond to a specific competitor price. Produces an elasticity- and margin-grounded pricing recommendation.
---

# Internal Pricing Optimization Skill

Recommend the price for a product (or set of products) that maximises **weekly
gross margin** given internal cost, demand elasticity and competitor pricing.

## When to use
- "What's the optimal price for product X?"
- "How low can we go on Y and still make money?"
- "Competitor Z dropped to €1.99 — how should we respond?"
- "Re-price category C to lift margin."

## Method
1. **Establish the floor.** Call `get_product_pricing` for procurement cost and
   logistics cost. The absolute floor is `procurement_cost + logistics_cost`;
   never recommend at or below it.
2. **Read the market.** Call `search_competitor_promotions` for the same product
   / category to anchor on the cheapest competing price and the typical discount
   depth.
3. **Search the price curve.** Call `simulate_price_change` at several candidate
   prices (e.g. current, −10%, −20%, matching the competitor). Each call returns
   the elasticity-driven volume and the resulting weekly margin.
4. **Pick the margin-maximising point.** Choose the price with the highest
   forecast *weekly* margin, not the highest unit margin. If two prices are close
   on margin, prefer the one that is more competitive (better footfall) and
   respects shelf life for perishables.
5. **Weight by persona.** If a persona is specified, pass `persona_id` so the
   elasticity reflects that shopper's price sensitivity.

## Output format
For each product return: floor price, competitor best price, current price and
margin, recommended price, forecast volume at the recommendation, forecast
weekly margin, and the margin delta vs today. State explicitly whether the move
is *accretive* or *dilutive* and why.

## Guardrails
- Optimise weekly margin (`unit_margin × volume`), never unit margin alone.
- Flag any recommendation within ~5% of the cost floor as high-risk.
- Keep procurement cost internal; report only prices, margins and deltas.
