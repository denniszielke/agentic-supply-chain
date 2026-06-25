# Retail Items Index and Agentic Retrieval Definition

## 1. Items Index Description (for Retrieval Agents)

### Purpose
The `retail-items` index stores concrete promotional offer instances for products across suppliers and validity windows. Each document represents a retrievable offer record that can be filtered, ranked, compared, and cited by an agent.

### Retrieval intent supported by this schema
- Find active promotions for a product, brand, or category.
- Compare current/original prices and discount levels across suppliers.
- Return packaging-aware and unit-price-aware comparisons.
- Filter by time validity, promotion type, coupon requirement, and availability.
- Support hybrid retrieval (keyword + semantic + vector) with grounded fields.

### Index configuration (from `scripts/create_search_index.py`)
- Index name: `retail-items` (default via `AZURE_SEARCH_ITEM_INDEX_NAME`)
- Semantic config: `item-semantic`
- Semantic title field: `name`
- Semantic content field: `description_text`
- Semantic keyword field: `brand`
- Vector field: `embedding`
- Vector profile: `hnsw`

### Field groups the retrieval agent should use

#### Identity and linking
- `id` (key)
- `item_id`
- `supplier_id`
- `category_id`
- `source_ref`

#### Product basics (search/display)
- `name`
- `brand`
- `description_text`

#### Attributes
- `attributes_origin`
- `attributes_quality_grade`
- `attributes_bio`
- `attributes_animal_welfare`

#### Packaging
- `packaging_unit_type`
- `packaging_quantity`
- `packaging_packaging_type`

#### Pricing
- `pricing_current_price`
- `pricing_currency`
- `pricing_original_price`
- `pricing_discount_percentage`
- `pricing_unit_price`
- `pricing_unit_reference`

#### Promotion
- `promotion_type`
- `promotion_bonus_amount`
- `promotion_coupon_required`

#### Conditions and validity
- `conditions_deposit`
- `conditions_availability`
- `offer_validity_start_date`
- `offer_validity_end_date`

### Retrieval behavior guidance
- Use `name`, `brand`, and `description_text` for lexical + semantic matching.
- Use `embedding` for semantic nearest-neighbor retrieval when query intent is conceptual.
- Apply filters first when user intent is structured (supplier, date, coupon, category).
- Rank business answers using active validity, discount strength, and unit-price relevance.
- Always return traceable evidence fields: `supplier_id`, `name`, `pricing_current_price`, `pricing_original_price`, `pricing_discount_percentage`, `offer_validity_start_date`, `offer_validity_end_date`, `source_ref`.

---

## 2. Agentic Retrieval Agent Description

### Agent name
Retail Promotion Retrieval Agent

### Mission
Groundedly answer pricing and promotion questions by retrieving offer-level records from `retail-items`, then summarizing with explicit supplier/product/price/validity evidence.

### Inputs
- User query (natural language)
- Optional constraints: `supplier_id`, `category_id`, date window, coupon requirement, promotion type

### Outputs
- Concise answer with item-level facts only from retrieved documents
- Comparison table (when multi-supplier intent)
- Evidence block with source fields and validity dates

### Retrieval strategy
1. Query understanding
- Detect intent: lookup, compare, explain, or no-active-promo check.
- Extract entities: product, brand, supplier, promotion keywords, timeframe.

2. Candidate retrieval
- Hybrid retrieval:
  - Semantic: `item-semantic`
  - Keyword: product/brand terms
  - Vector: `embedding` when needed for fuzzy product intent
- Use a bounded top-k (for example 10-30) before reranking.

3. Filtering and trust
- Prefer active offers (`offer_validity_start_date <= now <= offer_validity_end_date`).
- Respect user filters first, then relevance.
- Never fabricate unavailable prices/promotions.

4. Response shaping
- For lookup: best active matches with direct evidence.
- For comparison: group by supplier and show price/original/discount/validity.
- For no match: explicitly state no active promotion found and provide current shelf-price fields if present.

### Guardrails
- Do not answer factual promotion/price questions without retrieval.
- Do not infer missing discount or validity values.
- If multiple currencies appear, do not normalize unless explicitly configured.
- Cite only retrieved records and include source metadata when available.

---

## 3. Knowledge Agent Retrieval Request Query

Use this as the retrieval query payload pattern for a knowledge-agent call over the knowledge base that includes `retail-items`.

### A. Natural-language retrieval query (recommended for intent layer)
`Find active promotions for Greek yogurt in the next 7 days across all suppliers. Return supplier name/id, product name, current price, original price, discount %, unit price, coupon requirement, and offer validity. Sort by highest discount and then lowest unit price.`

### B. Structured request template (search-oriented)
```json
{
  "query": "Find active promotions for Greek yogurt in the next 7 days across all suppliers",
  "knowledgeSource": "retail-items-ks",
  "retrieval": {
    "mode": "hybrid",
    "semanticConfiguration": "item-semantic",
    "top": 20,
    "select": [
      "id",
      "item_id",
      "supplier_id",
      "name",
      "brand",
      "description_text",
      "pricing_current_price",
      "pricing_original_price",
      "pricing_discount_percentage",
      "pricing_unit_price",
      "pricing_unit_reference",
      "promotion_type",
      "promotion_bonus_amount",
      "promotion_coupon_required",
      "offer_validity_start_date",
      "offer_validity_end_date",
      "source_ref"
    ],
    "filter": "offer_validity_start_date le now() and offer_validity_end_date ge now()"
  },
  "answer": {
    "format": "table+summary",
    "mustCiteFields": [
      "supplier_id",
      "name",
      "pricing_current_price",
      "pricing_original_price",
      "pricing_discount_percentage",
      "offer_validity_start_date",
      "offer_validity_end_date",
      "source_ref"
    ]
  }
}
```

### C. Comparison query variant
`Compare active strawberry jam promotions between ALDI, REWE, and EDEKA. Include package size, current/original price, discount %, and end date. Highlight the best price per unit.`

---

## 4. Optional system prompt snippet for this retrieval agent

```text
You are a grounded retail promotion retrieval agent.
Always retrieve from retail-items before answering factual promotion or price questions.
Return supplier, product, current/original price, discount %, validity start/end, and source reference.
If no active promotion exists, state that clearly and avoid speculation.
For comparison requests, produce a concise side-by-side result.
```
