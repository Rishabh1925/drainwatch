"""HTTP routers for the operations API.

Split by audience rather than by entity: ``auth`` is public, ``operations`` is the
signed-in operator surface, ``admin`` is admin-only, and ``analysis`` is the frame
submission path. The guard each router applies is decided by that audience, which makes
it hard to add a route to the wrong one by accident.
"""
