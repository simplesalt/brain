# Crawl notebooks

Observable Notebook Kit notebooks for exploring the crawl pipeline's data.

## Use

1. Port-forward the crawl Postgres cluster:

   ```sh
   kubectl -n ssint-main-crawl port-forward svc/crawl-pg-rw 15432:5432
   ```

2. Install and preview:

   ```sh
   cd notebooks/crawl
   npm install
   npm run preview
   ```

   Open the localhost URL it prints.

3. `npm run build` bakes the query results into `.observable/dist`. That output contains real
   candidate/search data — do not commit it or publish it without thinking about what's in it.

4. The credentials in `.observable/databases.json` are the `crawl_read` read-only login. They are
   committed intentionally: this login is treated as non-secret by repo convention, and access is
   gated by the `kubectl` port-forward, not by the password.
