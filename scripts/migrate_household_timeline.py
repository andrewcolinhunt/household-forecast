"""Preview or apply the reviewed household timeline conversion with private backups.

Usage: pixi run --locked python scripts/migrate_household_timeline.py preview DIRECTORY
       pixi run --locked python scripts/migrate_household_timeline.py apply DIRECTORY

Use --settings FILE for profile_contexts mapping standalone profile IDs to Plan IDs.
The converter leaves immutable forecast history untouched.
"""

from __future__ import annotations

from migrate_planning_retirement import main

if __name__ == "__main__":
    main()
