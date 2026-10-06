# Timesheet

Self-hosted employee attendance and PTO tracker.

## Features
- Employee/admin login
- Reusable weekly schedules with effective dates
- One-click **I was here** on any scheduled day with no entry, including past dates, to record the full scheduled hours
- Split days: Worked + Sick/Vacation/Personal/etc.
- Extra or short hours are visibly flagged
- Sick, Vacation, and Personal balances
- Admin employee management
- Monthly summary report
- Admin-configurable site name, banner text, logo, and banner image
- Audit log stored in SQLite
- Docker deployment

## Run with Docker
1. Edit `docker-compose.yml` and change `SECRET_KEY` and `ADMIN_PASSWORD`.
2. Run:
   ```bash
   docker compose up -d --build
   ```
3. Open `http://SERVER-IP:8090`
4. Default username is `admin`; password is whatever you set in `ADMIN_PASSWORD`.

Data persists in `./data` and uploaded branding images persist in `./static/uploads`.

## Notes
PTO balances are entered by an administrator as available allocations. Sick, Vacation, and Personal hours recorded on timesheets are automatically deducted from the displayed remaining balance.

## v0.1.1 change
The **I was here** button is available on every scheduled day that has no existing entry, regardless of whether the date is today, in the past, or in the future. Once a day has an entry, use **Edit** instead so the quick button cannot accidentally overwrite split-day/PTO details.
