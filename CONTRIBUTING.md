# Contributing to Bucket Builder

Thank you for helping. Bug reports, ideas and code are all welcome.

## Reporting an issue

Open an [issue](https://github.com/calvin-young/blender-bucket-builder/issues) with:

* the Blender version, the operating system and the graphics backend
  (Edit > Preferences > System), and the add-on version;
* what you did, step by step, what you expected and what happened;
* for slowness: **Sidebar > Bucket > Options > Performance > Copy Report**,
  pasted into the issue;
* if you can, a **small synthetic file** that shows it: a few primitive meshes
  named like `part-001`, in a scene named like `bucket-01`.

Never attach real customer parts, production builds, printer addresses or
anything else you would not publish.

## Finding something to work on

Issues labelled `good first issue` are small and well described.
`help wanted` marks larger pieces nobody is working on yet.

## Sending changes back

If you have built something on a fork for your own workflow, please send it
upstream once it works for you. Draft pull requests are welcome: it is easier
to talk about code than about a description of it.

1. Branch from `main` (`git checkout -b my-change`).
2. Make one logical change. Keep the code style of the file you are in.
3. Run the tests that cover it (see `AGENTS.md`, "Setup, test, package");
   add a test for what you fixed or added.
4. Open a pull request and fill in the template, including how you tested.

## Using an AI assistant

That is fine. Please run and try the result yourself before you open the pull
request, and fill in the "Requested vs. assumed" section honestly: what you
asked for, what the assistant assumed, and what nobody checked.

## Data

Issues, pull requests, tests and screenshots use synthetic data only.

## Licence

By contributing you agree that your contribution is licensed under the
GPL-3.0-or-later, the licence of this project (see `LICENSE`).
