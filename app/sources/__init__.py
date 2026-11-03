"""Lead discovery and enrichment sources: one module per source, one registry, one waterfall.

Every source is a `SourceSpec` (see base.py) built from the provider's public documentation. The doc link is in the
module that uses it and in docs/SOURCES.md. Nothing here scrapes a site that forbids it: sources are official APIs the
owner connects with their own keys, public open-data endpoints, public job-board JSON feeds that companies publish
for programmatic use, and the company's own website through the polite robots.txt-respecting fetcher.
"""
