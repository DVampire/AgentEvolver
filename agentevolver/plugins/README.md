---
name: plugins
description: "External-provider plugins (data sources, software, …). A plugin is a packaging unit surfaced on the canvas as a semantic node (a data_source plugin becomes a datasource node); every plugin returns the canonical {message, data, files} envelope."
version: 1.0.0
type: module
category: plugins
requirements: []
metadata: {}
---
# Plugins

External-provider plugins adapt outside services into AgentEvolver. Each plugin declares a
`kind` (`data_source` today) and returns the canonical `{message, data, files}` envelope, so
its output composes with any other capability. A plugin is a *packaging* unit — it is never a
workflow step itself; a `data_source` plugin surfaces on the canvas as a semantic `datasource`
node, dispatched through `plugin_manager`.
