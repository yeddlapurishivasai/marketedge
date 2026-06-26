"""Scanner subsystem (feature 011).

Scanners screen the ingested daily-bar universe ({Market}Bars1D) for technical setups.
The package is split into:

* ``indicators`` - load daily bars from the DB and build an ``IndicatorSnapshot`` (SMA/EMA/
  ATR/RSI/turnover/RVOL/PGO/NATR/candle/trend flags) plus the underlying arrays scanners
  need for windowed look-backs.
* ``definitions`` - the individual scanner families (registered by name).
* ``runner`` - orchestrates a scan run: resolve universe, refresh today's bar, execute one
  or all scanners, and persist results idempotently per (scanner, scan date).
"""
