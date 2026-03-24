import sys, os
# Make trade-bot/ findable so panel files can do "import state", "import helpers" etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
