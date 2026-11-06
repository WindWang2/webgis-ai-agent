# Anonymous owner credential storage

Migration `0102_owner_token_hashes` hashes existing `conversations.owner_token`
and `analysis_tasks.owner_token` values in bounded batches. New ORM writes and
equality queries hash the presented credential automatically. Database reads
return a typed verifier; the verifier itself cannot authenticate a client.
Only a creating request receives its newly minted raw token. Existing session
requests supply their own credential; internal history reads do not recover
one from the database. Rotation replaces the verifier and the session-data
digest together.

Apply the migration before serving this application version. The raw token
must remain random with at least 256 bits of entropy. No server encryption key
or recoverable plaintext is stored with the verifier. Session-data guards
accept a typed verifier only from an already authorized server path, such as
a signed terrain request. JSON/header/query strings never acquire that type.

The hash migration is one-way. Downgrade retains conversation and task rows
but clears their anonymous credential verifiers, revoking anonymous access;
it cannot recover old raw tokens. Back up before a rollback and plan fresh
anonymous sessions if reverting this application version. Authenticated
ownership remains governed by `user_id`/`creator_id`.

PostgreSQL sync and async connections explicitly use UTC. Existing naive
timestamp columns therefore store/read UTC even when the database's default
session time zone differs. Input/output boundaries still normalize aware
timestamps where required.
