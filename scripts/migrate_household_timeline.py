"""Preview or apply the reviewed household timeline conversion with private backups.

Usage: pixi run --locked python scripts/migrate_household_timeline.py preview DIRECTORY
       pixi run --locked python scripts/migrate_household_timeline.py apply DIRECTORY

Use --settings FILE for profile_contexts mapping standalone profile IDs to Plan IDs.
Retirement profiles may map to lists of reviewed Plan IDs; distinct full-household
spending alternatives are retained. Undated positive standalone additions remain
incomplete and require dates before forecasting.
The converter leaves immutable forecast history untouched.
"""

from __future__ import annotations

from migrate_planning_retirement import main

if __name__ == "__main__":
    main()
