"""AIS correlation: interpolate AIS to the SAR timestamp and match one-to-one with detections.

No AIS source is connected yet (GFW token pending; no scraping). The module runs on any
DataFrame with the documented columns, and is tested on synthetic data.
"""
