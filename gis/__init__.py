"""Ingestione layer statici + costruzione mappa di idoneità (traccia A, spec §3, §8).

STUB in questo kickoff: qui va la pipeline che scarica DEM/forestale/suolo/disturbo
per Veneto+Trentino, li riporta alla griglia comune (config/grid.yaml), applica il
crosswalk (config/crosswalk.yaml) e produce, per ogni cella, il dict di feature che
engine.static_scorer.static_suitability consuma. Vedi gis/README.md.
"""
