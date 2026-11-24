---
name: helm-chart
description: Use when writing, changing or reviewing a Helm chart, its templates, or its values.
---
<!-- agent-prompt -->
- Declare every resource your app needs in its chart, never as a separate manifest.
- If you are about to write a ConfigMap or a heredoc of config, ship that config as a read-only filesystem layer instead.
- Change an installed app by publishing a new image or layer and updating the release, never with an install or upgrade hook that edits what is already running.
- Test a chart in CI: lint it, render it with default values, and check its values schema. You have no cluster access, so do not plan tests that need one.
- Define CRDs only in a repo that exists to publish that CRD.
- Honour every value the release sets, such as namespace and names. Never hard-code over a value.
- Pin every chart, image and layer to a version.
- Give every chart a CI job that runs on its own, checks the chart's sources for new releases, and rebuilds on the new version. If the build or the release fails, the job keeps the old pin and files an issue in the chart's repo naming the version and how it failed.
