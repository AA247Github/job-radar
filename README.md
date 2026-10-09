# job-radar

Nightly sweep of employer job boards (Greenhouse, Ashby, Lever public APIs) for
London / remote UK data roles. Output: `data/latest.json`, read each morning by
a Claude scheduled task.

## Setup (5 minutes)
1. Create a **public** GitHub repo named `job-radar` (public so the raw JSON URL
   works without a token). Upload every file in this folder, keeping the paths.
2. Repo **Settings > Actions > General > Workflow permissions**: choose
   "Read and write permissions" and save.
3. **Actions** tab > "job-radar nightly sweep" > **Run workflow** to test it once.
   After about two minutes `data/latest.json` should appear in the repo.
4. Tell Claude your GitHub username so the scheduled task points at
   `https://raw.githubusercontent.com/<you>/job-radar/main/data/latest.json`.

## Editing the watchlist
Edit `config.json`. `probe` tokens are tried on all three ATSs every night;
any that return jobs show up under `discovered_tokens` in `latest.json`.
Move them into the right ATS list once confirmed.
