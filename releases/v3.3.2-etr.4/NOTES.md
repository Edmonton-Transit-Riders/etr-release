Works on networks that block internet time servers.

- If the tracker cannot reach a time server (some business and office
  networks block them), it now sets its clock over a normal web connection
  instead. Before this, it stayed on "Waiting for time sync".
- No change on networks where time sync already works.
