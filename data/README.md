# Data

- `sample_leads.json`: two fake leads for the stubs. Emails use `sandbox.test` so nothing can be sent for real.
- `customer_brief.example.json`: the inputs a customer gives us (offer, ICP, product info). Copy it to `customer_brief.json` for local runs.
- Apollo people exports (CSV) go in `apollo/`, or `apollo/<customer_id>/` for another customer. Git-ignored: don't commit personal data. Start the server with `SALES_LEADS=apollo` to use them.
