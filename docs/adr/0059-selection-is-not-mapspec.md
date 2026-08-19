# Feature selection is session working memory, not MapSpec

Clicking features is how users name a problem (“buffer these schools”). Putting
that set on MapSpec would version the cartographic intent on every click; hiding
it in the browser would leave the Agent blind.

**Decision:** Selection lives on the Session as Observed-side working memory. The
Agent may read it; analysis tools still take explicit `ref_id` / feature ids.
Selection is not a MapSpec field. “Pin this selection” as its own Intent, and
whether a Checkpoint must snapshot Selection, are deferred.
