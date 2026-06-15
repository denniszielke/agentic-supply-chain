---
name: portfolio-analysis
description: Use when the user asks how a category or the overall assortment is performing, where the margin and volume sit, which products or categories are strong or weak, or where there is headroom to grow margin. Produces an evidence-backed portfolio view across categories and personas.
---

# Portfolio Analysis Skill

Analyse the product portfolio across categories and personas to find where
margin and volume concentrate and where there is headroom to act.

## When to use
- "How is category X performing?"
- "Where do we make the most / least margin?"
- "Which categories are exposed to competitor pressure?"
- "What should we prioritise this quarter?"

## Method
1. **Map the portfolio.** Call `list_categories` for the aggregate weekly
   margin, volume and average margin % per category. Rank categories by margin
   contribution and by margin %.
2. **Drill where it matters.** For the top contributors and the weakest margin
   categories, call `get_category_margin_forecast` and `list_products` to see
   the per-SKU spread. Identify margin leaders, margin laggards and high-volume
   traffic drivers.
3. **Overlay personas.** Call `list_personas` and map each category to the
   personas that shop it most (`category_affinity`). Note where a high-margin
   category depends on a price-sensitive persona (fragile) vs a low-sensitivity
   persona (defensible).
4. **Overlay competition.** Use `search_competitor_promotions` to see which of
   our strong categories competitors are actively attacking.
5. **Classify.** Bucket each category as: *Defend* (high margin, under attack),
   *Grow* (margin headroom, low competitive pressure), *Traffic* (low margin,
   high volume, footfall driver) or *Fix* (low margin, low volume).

## Output format
Return a category-level table: category, weekly volume, weekly margin €,
avg margin %, dominant persona, competitive pressure (low/med/high), and
classification. Follow with 3–5 prioritised recommendations, each tied to the
evidence (which tool/number drove it).

## Guardrails
- Always quantify claims with the figures returned by the tools — no
  unsupported assertions.
- Distinguish *contribution* (absolute € margin) from *efficiency* (margin %):
  a low-% category can still be the biggest contributor.
