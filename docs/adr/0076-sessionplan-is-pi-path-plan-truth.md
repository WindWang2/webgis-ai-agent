# SessionPlan is the Pi-path plan truth

Pi is the product agent host. ChatEngine’s CanonicalPlan stays the fallback’s step list. The Pi path’s plan object is **SessionPlan**: a Session-keyed envelope in SessionStore whose GIS chapter is an embedded MapProductPlan and whose progress is capability completion, not a tool-call sequence. We rejected transplanting CanonicalPlan onto Pi, using MapProductPlan’s name as the host truth, and storing the envelope in a Pi session entry (ADR-0055).
