# Private benchmark buckets

Put real buckets here (3MF or STL). **Nothing in this folder except this
README and its `.gitignore` is ever committed**: git ignores everything else,
and `tests/test_private_guard.py` fails if a file here is tracked anyway (for
example with `git add -f`).

The benchmark script (Stage 2 onward, see `docs/AUTOPACK.md` §16) reads the
buckets here and writes only a results table with generic labels
(`bucket-01`, `bucket-02`, ...). The table is safe to share.

To fix which label belongs to which file, keep a `manifest.json` here (it is
ignored too):

```json
{
  "bucket-01": "socket_and_insoles.3mf",
  "bucket-02": "customer_x_order_1234.3mf"
}
```

File names are never written to the results, since a name can itself be
sensitive. Files not in the manifest get the next free label in alphabetical
order of their names.

Check that nothing here will be committed:

```
git status --ignored benchmarks/private
python3 tests/test_private_guard.py
```
