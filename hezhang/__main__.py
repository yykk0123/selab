"""Launch the graphical application; retain the terminal client via --cli."""

import sys

if "--cli" in sys.argv:
    sys.argv.remove("--cli")
    from .cli import main
elif "--backup" in sys.argv or "--restore" in sys.argv:
    from .cli import main
elif "--web" in sys.argv:
    sys.argv.remove("--web")
    from .gui import main
else:
    from .desktop import main

main()
