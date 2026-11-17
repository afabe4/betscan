# data/

Scheduled runs keep state here between runs (`BETSCAN_HOME=data`):

- `snapshots.sqlite`: line snapshots (pruned to 14 days), every logged pick with its grade, injury statuses
- `latest_ncaab.json`: the latest scan's bets and watch list
- `card.html`: the bet card the last run built

`http/` is a download cache and isn't committed.
