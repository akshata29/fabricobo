# Fabric Warehouse SQL Scripts

These scripts set up the Fabric Warehouse for the OBO Identity Passthrough POC. Run them **in order** in the Fabric Warehouse SQL editor.

## Scripts

| # | Script | What It Does |
|---|--------|-------------|
| 01 | `01-create-tables.sql` | Creates the `Accounts` and `RepUserMapping` tables |
| 02 | `02-seed-data.sql` | Inserts 7 sample accounts and 2 user-to-rep mappings |
| 03 | `03-create-rls-policy.sql` | Creates the RLS predicate function and security policy |
| 04 | `04-verify-rls.sql` | Runs verification queries to confirm the setup |
| 05 | `05-teardown.sql` | Removes everything (for cleanup or reset) |

## Before You Start

1. **Create a Fabric Warehouse** in your workspace (see `docs/SETUP_GUIDE.md` section 4)
2. **Open the SQL editor** in the warehouse
3. **Update `02-seed-data.sql`** — replace `{yourdomain}` with your Entra tenant domain (e.g., `contoso.onmicrosoft.com`)

## Running the Scripts

1. Open the Fabric Warehouse in the browser
2. Click **New SQL query**
3. Copy-paste each script in order and click **Run**
4. After script 04, verify:
   - 7 rows in Accounts
   - 2 rows in RepUserMapping
   - RLS policy shows as enabled

## Expected Results After RLS

| User | RepCode | Accounts Visible | Total Balance |
|------|---------|-----------------|---------------|
| Fabric User A (`fabricusera@...`) | REP001 | Contoso, Northwind, Adventure Works, Fabrikam | $900,000 |
| Fabric User B (`fabricuserb@...`) | REP002 | Tailspin, Wide World, Proseware | $683,000 |
| Workspace Admin | (all) | All 7 accounts | $1,583,000 |

## Adding More Test Users

To add another user, insert a row into `RepUserMapping`:

```sql
INSERT INTO dbo.RepUserMapping VALUES ('REP001', 'newuser@yourdomain.onmicrosoft.com');
```

To add a new rep with their own data partition:

```sql
-- Add accounts for the new rep
INSERT INTO dbo.Accounts VALUES
(8, 'New Customer Inc', 100000.00, '2025-06-01', 'West', 'REP003');

-- Map a user to the new rep
INSERT INTO dbo.RepUserMapping VALUES ('REP003', 'fabricuserc@yourdomain.onmicrosoft.com');
```

## Resetting the Environment

Run `05-teardown.sql` to drop everything, then re-run scripts 01-03 to start fresh.
