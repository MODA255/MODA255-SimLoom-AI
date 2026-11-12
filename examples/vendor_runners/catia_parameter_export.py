"""Windows/software worker entry point. Requires the matching SimLoom installation."""
import sys
from engineering_agent.vendor_runners import main

if __name__ == "__main__":
    main('catia_parameters', sys.argv[1])
