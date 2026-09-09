"""corral_core — the code full Corral and Corral Light must not fork.

One home for the ACP client and the pane/permission machinery. Nothing in
here may import from `corral/`: Corral Light is the public product and must
stand alone on a host where full Corral does not exist.
"""
