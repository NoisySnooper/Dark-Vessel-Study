"""Learned verifier stage: CA-CFAR candidates -> small CNN (vessel vs clutter).

Modules
  labels   AI2 Skylight Sentinel-1 point labels (Web Mercator tiling px -> lon/lat), attribute join
  chips    per-window candidate generation with the baseline CFAR settings, labeling, 64 px chips
  model    the CNN
  train    dataset, augmentation, training loop
  evaluate precision/recall/F1, PR curve, recall by AIS length bin with Wilson intervals
"""
