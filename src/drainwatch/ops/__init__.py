"""Operations domain: users, sources, assets, inspections, incidents, work orders, audit.

The narrow ``drainwatch.api.storage`` module persists one thing — a time series of choke
readings per drain — and that shape cannot carry the operational entities the V1
interface is built around. This package adds them without disturbing it: the readings
store keeps its own table and its own route contract, and everything new lives here.
"""
