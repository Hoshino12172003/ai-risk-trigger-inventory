# Media Cloud exploratory audits

These scripts document the exploratory Ecuador flash-demand searches performed
before a formal Media Cloud corpus was incorporated into the research pipeline.
They are retained for methodological provenance, but their generated CSV files
are machine-local intermediate results and are intentionally excluded from Git.

Run the scripts from the repository root in chronological order:

1. `audit_ecuador_coverage.py`
2. `audit_flash_queries_v2.py`
3. `screen_flash_candidates_v3.py`
4. `search_shock_induced_flash.py`

The API scripts read `MC_API_KEY` from the environment. No key is stored in the
repository. The optional `mediacloud` client is not a core project dependency.
These outputs are candidate evidence only: they are not canonical events,
calibrated risk atoms, or optimization inputs.
