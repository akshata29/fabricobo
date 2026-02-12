-- ============================================================================
-- Fabric OBO POC — Step 2: Seed Sample Data
-- ============================================================================
-- Run this in the Fabric Warehouse SQL editor AFTER running 01-create-tables.sql.
--
-- IMPORTANT: Replace {yourdomain} with your actual Entra tenant domain
--            (e.g., contoso.onmicrosoft.com or contoso.com)
--
-- This creates:
--   - 7 sample accounts split between two reps (REP001 and REP002)
--   - 2 user-to-rep mappings for the test users
--
-- After RLS is applied:
--   - User A (REP001) sees: Contoso, Northwind, Adventure Works, Fabrikam (4 accounts)
--   - User B (REP002) sees: Tailspin, Wide World, Proseware (3 accounts)
-- ============================================================================

-- Sample accounts
INSERT INTO dbo.Accounts VALUES
(1, 'Contoso Ltd',           250000.00, '2024-01-15', 'East',    'REP001'),
(2, 'Northwind Traders',     180000.00, '2024-03-22', 'West',    'REP001'),
(3, 'Adventure Works',       320000.00, '2024-06-10', 'Central', 'REP001'),
(4, 'Fabrikam Inc',          150000.00, '2024-09-05', 'East',    'REP001'),
(5, 'Tailspin Toys',         175000.00, '2025-01-25', 'West',    'REP002'),
(6, 'Wide World Importers',  420000.00, '2025-02-14', 'East',    'REP002'),
(7, 'Proseware Inc',          88000.00, '2025-05-01', 'Central', 'REP002');

-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │ UPDATE THESE EMAIL ADDRESSES to match your Entra test users!            │
-- │ The emails must exactly match USER_NAME() as seen by Fabric.            │
-- └──────────────────────────────────────────────────────────────────────────┘
INSERT INTO dbo.RepUserMapping VALUES
('REP001', 'fabricusera@{yourdomain}.onmicrosoft.com'),
('REP002', 'fabricuserb@{yourdomain}.onmicrosoft.com');

-- Verify the data
SELECT 'Accounts' AS TableName, COUNT(*) AS RowCount FROM dbo.Accounts
UNION ALL
SELECT 'RepUserMapping', COUNT(*) FROM dbo.RepUserMapping;
