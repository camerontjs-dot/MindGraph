# Lossless nomination transport encoding: RC0 research result

**Experiment:** [MindGraph #63](https://github.com/camerontjs-dot/MindGraph/issues/63)  
**Parent negative control:** [#62](https://github.com/camerontjs-dot/MindGraph/issues/62), compact CLI JSON larger than legacy in all 35 cases.  
**Disposition:** `LOSSLESS_PACKING_MECHANICALLY_POSSIBLE` on exact frozen cohort, **NOT** `GRAPH_UTILITY_SUPPORTED`, context-window savings, or approval to change a maintained API.

## Question and experiment

The pinned MindGraph #46 existing JSON compact nominations repeat a long set of field names for every returned row. We compared **the original compact payload**, the **legacy full-list payload**, both symmetrically minified, and a new **lossless columnar encoding** that transmits each column name once and then ordered rows of all original values.

Unlike dropping `citation_class`, provenance warnings, index identity, content hash or expansion handles, this approach does not discard any nomination field or original envelope member. It does change the serialized shape; consumers would require a versioned decoder. It is optimized *for structured transport*, not for direct human reading or an unqualified model.

The #62 results and exploratory field-cost observation were known at design time. This is therefore a **new successor** with a fresh frozen code/inputs lineage, not an unblinded favorable rerun of the old #62 hypothesis.

### Frozen identities

- Product/normal nomination source: exact [MindGraph PR #46](https://github.com/camerontjs-dot/MindGraph/pull/46) candidate at `11f6f8161dbf6cb78ebc1363a28948a4606fe6e0`.
- Query cohort: **all 34** historical #54 Knowledge/Projects cases plus one explicitly unlabelled Operations smoke, order unchanged.
- Exact #62 private transport result input SHA-256 `7d6952f8b9ff19d6d914dcad2719d63bf27c2abd4670eb26f381186677f7489c`.
- Packer source SHA-256 `38797319ccb2e9d9faf5f72d170bac7778f7e6dd736f387101f524e38cadbf36`.
- Pre-score successor freeze SHA-256 `8eb8abe6715eef4b04f071693aa6f05ab8b6ec3bae16309839e686f3173d6e9c`.
- Private decisive source/result SHA-256 `d89930f32534de75711875fcb9ede0ed76375a1fdd81fa1f4b8194b7b48143bc`.
- Separate verifier source SHA-256 `c90337149a665f6033e79502306f703ac0b9a595245863a8135ef299c38bb2e4`.
- Independent-formula result `VERIFY_PASS`, private verifier receipt SHA-256 `d80728a4e939870a815aa5c970b7054768dbb59c6f576dedb37e80edd9cbb6bd`.

### Observed minified JSON bytes

| Workload | Legacy full-list, minified | Existing compact object, minified | Lossless packed columns, minified | Packed vs legacy |
|---|---:|---:|---:|---:|
| Knowledge (12 queries, 120 nominations) | 216,386 | 251,409 | **195,201** | **-9.8%** |
| Projects (22 queries, 220 nominations) | 354,698 | 412,418 | **309,370** | **-12.8%** |
| Operations smoke (1 query, 10 nominations) | 15,749 | 18,665 | **13,981** | **-11.2%** |

All **35/35 packed documents** reconstruct the original compact JSON envelope, every nomination field value, row order, citation/provenance class and expansion handle, with no source or query change. Every original nomination used one identical column-key set under the frozen product. The packed representation can be understood as a dictionary-assisted transport format with separate machine decoding.

The separate verifier independently decoded all saved packed cases, reconstructed and compared each complete original JSON object, recalculated fair symmetric minified bytes, checked SHA-256 inputs and totals, and rejected duplicate-column and falsified-source-value controls. The packer's synthetic gates also rejected column-order, row-arity and missing-header mutations before scoring.

### What this does not establish

- **Actual LLM token count or overall session tokens.** Raw encoded handles, column arrays and JSON keys tokenize differently; a smaller wire form could be harder for a model to interpret.
- **Agent selection quality.** The nominated sources were not chosen by an independently isolated contemporary model; #33–35 gates remain blocked.
- **Source currentness or authenticated approval.** Exact #46 producer identity was unavailable for frozen Knowledge/Projects, present for Operations; packing does not add authority. Expansion controls remain unchanged.
- **Runtime/consumer integration.** No Draft #46, Conduit, MainFrame, installed service, index, schema, release, or production user-visible output was changed.
- **A general compression policy.** An arbitrary downstream client must validate the column dictionary and preserve all supplied field meanings; consumers needing human-readable objects can decode before presentation.

### Next qualified engineering decision

Keep this as a research apparatus in the vNext programme. The next distinct product work should propose a **versioned decoder/transport optional mode** only if an actual consumer boundary has a reason to use it, and independently test incorrect dictionaries, wrong scopes, changed producer identities, stale/wrong handles, schema upgrades and end-to-end model context/cost. The strong baseline is current compact and source-expanded behavior, not just a byte count. A fast, reversible adapter can be evaluated in Conduit or direct MainFrame independently.

**Do not merge this column format into maintained MindGraph purely because its serialized bytes are smaller.** The production output contract and an agent's actual decisions are separate qualification claims.
