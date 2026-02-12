-- ============================================================================
-- Fabric OBO POC — Teardown (Remove Everything)
-- ============================================================================
-- Run this in the Fabric Warehouse SQL editor to remove all POC objects.
-- Useful for resetting the environment or cleaning up after a demo.
--
-- Order matters: drop policy → function → tables
-- ============================================================================

-- 1. Drop the RLS security policy (must be dropped before the function)
IF EXISTS (SELECT 1 FROM sys.security_policies WHERE name = 'AccountsFilter')
    DROP SECURITY POLICY dbo.AccountsFilter;

-- 2. Drop the predicate function
IF EXISTS (SELECT 1 FROM sys.objects WHERE name = 'fn_SecurityPredicate' AND type = 'IF')
    DROP FUNCTION dbo.fn_SecurityPredicate;

-- 3. Drop the tables
IF EXISTS (SELECT 1 FROM sys.tables WHERE name = 'RepUserMapping')
    DROP TABLE dbo.RepUserMapping;

IF EXISTS (SELECT 1 FROM sys.tables WHERE name = 'Accounts')
    DROP TABLE dbo.Accounts;

-- Verify everything is gone
SELECT 'Remaining tables:' AS Check_, name FROM sys.tables WHERE name IN ('Accounts', 'RepUserMapping')
UNION ALL
SELECT 'Remaining policies:', name FROM sys.security_policies WHERE name = 'AccountsFilter';
