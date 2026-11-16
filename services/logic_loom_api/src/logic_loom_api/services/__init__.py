"""Feature services: presentation logic that sits between routes and adapters.

Not to be confused with the repository's top-level ``services/`` directory (the
uv workspace member holding this package). Introduced by the Failure Memory
workspace, which uses a dedicated service rather than more code inside an
adapter: adapters talk to the outside world, services shape what the routes
return.
"""
