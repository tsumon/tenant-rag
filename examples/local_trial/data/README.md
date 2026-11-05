# Frozen synthetic tenant fixtures

All source documents, store names, policy values, and queries are synthetic and authored for this example. No private data or real business policies are included. Sources mix English and Chinese; they contain deliberately conflicting policies for fictional tenants Aurora and Cedar.

Each tenant has five Markdown documents. The filename stem is `doc_id`; IDs intentionally repeat across tenants, so a document is identified by `(tenant, doc_id)`. Load each tenant directory independently. `queries.json` contains 14 answerable queries in matched tenant pairs plus two out-of-scope queries. Each relevant item includes an exact source substring. Cross-language cases use Chinese queries and English evidence, but bilingual documents also contain Chinese equivalents: these are not a pure cross-lingual benchmark.

The source documents and query judgments were frozen before any embedding, retrieval, or model-based tuning. Model acquisition was a separate setup step. `SHA256SUMS` records the UTF-8 bytes of the ten source documents, this README, and the query file. Run `sha256sum -c SHA256SUMS` from this directory to check integrity. Do not change these fixtures to improve a model result; any later revision must be versioned separately.

This tiny, hand-authored example demonstrates reproducibility and logical tenant filtering, not security certification or an independent benchmark of retrieval quality. Relevant document judgments are author-defined; no statistical performance claims are supported. Out-of-scope cases are answerability fixtures, not proof that nearest-neighbor retrieval will abstain.
