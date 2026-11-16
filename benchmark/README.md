# OCHAsk RAG benchmark

The fixed English dataset contains ten questions built from physical, 1-based pages of the linked PDFs, independent of OCHAsk. Each evidence record records the source URL and SHA-256 of the downloaded PDF. Document IDs refer to `data/ocha_situation_reports_urls.csv`; the presence of an ID in that CSV does **not** establish that the report has been indexed. Ellipses in excerpts indicate omitted text. Re-download a PDF and check its hash before using its excerpts as evidence; if an upstream PDF changes, verify the original before revising and versioning the dataset. V02 is scoped to one report and tests the report's explicit statement that the requested figure is unavailable.

## Current status

This repository currently contains a prototype report loader, a list of source URLs, and a landing-page database migration. It does not contain an indexed PDF corpus, a retrieval/generation pipeline, a question-answering API, or an LLM provider integration. Therefore an end-to-end benchmark run and its real scores cannot be produced in this checkout. The runner supplies a judge rubric and validation, but **does not include a provider-specific LLM client**. Both adapters below must be implemented against actual services before use. Mock results are not benchmark results.

## Execution

Python 3.14, no additional packages. Create executable adapter commands for the existing RAG system and the chosen LLM service. The runner invokes each command directly, without a shell, sending one JSON object on stdin and expecting exactly one JSON object on stdout. Diagnostics belong on stderr. Pass secrets through the adapter's environment, never as command arguments or in output. Neither adapter should change the dataset.

- RAG input: `{"id":"E01","question":"..."}`. Output: `{"answer":"...","retrieved_documents":[{"document_id":"GK00123","page":2,"text":"retrieved passage"}],"citations":[{"document_id":"GK00123","page":2}],"usage":{},"estimated_cost":null}`. Use the actual retrieved context and citations, not the reference. Use `null` for `retrieved_documents` when retrieval is unobservable and `[]` when it was observed but returned nothing. Additional model/configuration and usage details can be included and are saved verbatim.
- Judge input: `{"model":"...","rubric":"...","question":{...},"system_output":{...}}`. The adapter must send this information to the configured LLM and return its JSON judgment directly, including optional usage/cost fields. It should use a structured-output mode when the provider supports it. Required fields: `correctness`, `faithfulness`, `citation_quality` (integer 0–5; the latter two may be null when evidence is insufficient); `abstention_correct` (boolean for unanswerable questions, otherwise null); `reasoning` (object with string fields named `correctness`, `faithfulness`, `citation_quality`, `abstention`); and `errors` (array of strings). To assess citations beyond reference excerpts, supply the actual cited page texts in the system output. The runner does not download or verify citations itself.

Example after adapters exist:

```sh
python benchmark/run.py --rag-command 'python path/to/rag_adapter.py' \
  --judge-command 'python path/to/judge_adapter.py' --judge-model MODEL_VERSION \
  --rag-version 'PIPELINE_AND_CONFIG_VERSION' --output benchmark/results
```

Use `--ids E01,H01` and/or `--difficulty 'very hard'` to select questions. `--output` controls the directory; the runner overwrites `results.json` and `report.md` there. It records the dataset hash, dataset and runner versions, model/configuration identifiers supplied on the command line, full adapter output including optional usage/cost, measured RAG and judge latency, errors, and per-question judgments. Avoid writing secrets or private data into adapter output. A nonzero exit indicates at least one question failed; failed judgments are excluded from means and counted explicitly. Individual judge judgments are nondeterministic and should be reviewed, especially for citation support.

## Scoring

The judge scores factual correctness/completeness, support by retrieved text, and citation support independently on 0–5 scales. A null means the score could not be established, never zero. The runner forces faithfulness to null if no retrieved passage text is supplied and citation quality to zero for an observed empty citation list and to null when citations are unobservable; adapters/judges must return null when other evidence needed for verification is missing. Retrieval coverage is the fraction of required document IDs present in observed retrieval, or null when retrieval is unobservable; it does not prove that the right pages or passages were retrieved. Abstention accuracy applies only to unanswerable questions. Group metrics are arithmetic means over available scored values; `scored / total` and the detailed JSON make failures visible. Latency is wall-clock adapter invocation time, not model-only latency. Token usage and estimated costs are retained only when adapters provide them; pricing and cost calculation are the adapter's responsibility.
